"""
Cove Data Protection — PostgreSQL loader.

Insert-only. The `cove` schema is assumed to already exist (see sql/schema.sql
for its definition); this module neither creates nor migrates it. Reading the
data back is done in SQL — see sql/queries.sql.

Each run inserts one snapshot row and stamps every child row with that
snapshot_id, so loads accumulate history rather than overwrite it.

Connection comes from COVE_PG_DSN in .env, e.g.
    COVE_PG_DSN=postgresql://reporting:secret@localhost:5432/ipc_reporting

Every statement names the `cove` schema explicitly, so the code does not depend
on the connecting role's search_path.

Entry points:
  save_dataset(dataset)          Insert a Dataset, returns snapshot_id
  load_to_postgres(customer)     Fetch from API + insert in one call
  fetch_active_customers()       Read active rows from cove.customers
  active_customer_names()        Same, names only
  load_active_customers()        One snapshot per active customer

CLI:
  python cove_db.py load                  load the whole estate
  python cove_db.py load "Acme"           load one customer
  python cove_db.py load-active           one snapshot per active customer
  python cove_db.py customers             list active customer names
"""

import os

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

from cove_dataset import Dataset, fetch_dataset

load_dotenv()

# Every table reference is schema-qualified with this rather than relying on
# search_path — a wrong search_path fails with a confusing "relation does not exist".
SCHEMA_NAME = "cove"

# Child tables get snapshot_id prepended to their dataset columns.
# device must come first — the others carry a foreign key to it.
CHILD_TABLES = ["device", "datasource_session", "m365_source", "red_flag"]


def _dsn() -> str:
    dsn = os.getenv("COVE_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "COVE_PG_DSN is not set. Add it to .env, e.g.\n"
            "  COVE_PG_DSN=postgresql://user:password@localhost:5432/ipc_reporting"
        )
    return dsn


def connect():
    """Open a connection with search_path pointed at the cove schema."""
    conn = psycopg2.connect(_dsn())
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {SCHEMA_NAME}, public")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------

def save_dataset(dataset: Dataset, conn=None, notes: str = None) -> int:
    """
    Insert a Dataset as one snapshot. Returns the new snapshot_id.

    The whole load is a single transaction — a failure part-way leaves no
    half-written snapshot behind.
    """
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA_NAME}.snapshot
                    (captured_at, customer_filter, device_count, notes)
                VALUES (%s, %s, %s, %s)
                RETURNING snapshot_id
                """,
                (dataset.captured_at, dataset.customer_filter,
                 len(dataset.device), notes),
            )
            snapshot_id = cur.fetchone()[0]

            for name in CHILD_TABLES:
                table = dataset[name]
                if not len(table):
                    continue
                cols = ["snapshot_id"] + table.columns
                rows = [(snapshot_id, *t) for t in table.tuples()]
                execute_values(
                    cur,
                    f'INSERT INTO {SCHEMA_NAME}.{name} ({", ".join(cols)}) VALUES %s',
                    rows,
                )

        conn.commit()
        return snapshot_id
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def load_to_postgres(customer: str = None, notes: str = None) -> dict:
    """Fetch from the Cove API and insert a snapshot. Returns a load report."""
    dataset = fetch_dataset(customer)
    conn = connect()
    try:
        snapshot_id = save_dataset(dataset, conn, notes=notes)
    finally:
        conn.close()

    report = dataset.summary()
    report["snapshot_id"] = snapshot_id
    return report


# ---------------------------------------------------------------------------
# Read — customers
# ---------------------------------------------------------------------------

# cove.customers is maintained outside this loader (it is not in sql/schema.sql),
# so this module only ever reads from it.
CUSTOMER_COLUMNS = ["id", "customer_name", "created_at", "is_active"]


def fetch_active_customers(conn=None) -> list:
    """
    Return the active rows of cove.customers as a list of dicts, name-sorted.

    Keys are CUSTOMER_COLUMNS. Inactive rows are excluded by the query, so the
    caller never has to filter on is_active itself.
    """
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {", ".join(CUSTOMER_COLUMNS)}
                FROM {SCHEMA_NAME}.customers
                WHERE is_active
                ORDER BY customer_name
                """
            )
            return [dict(zip(CUSTOMER_COLUMNS, row)) for row in cur.fetchall()]
    finally:
        if own:
            conn.close()


def active_customer_names(conn=None) -> list:
    """Just the names — handy for driving a per-customer load or report loop."""
    return [c["customer_name"] for c in fetch_active_customers(conn)]


# ---------------------------------------------------------------------------
# Automated load, driven by cove.customers
# ---------------------------------------------------------------------------

def load_active_customers(notes: str = None, verbose: bool = True) -> dict:
    """
    Load every active customer in cove.customers, one snapshot each.

    One API fetch and one snapshot per customer, so each gets its own history
    row and a customer that fails does not take the rest of the run with it.
    Customers the API returns no devices for are reported as misses and
    skipped — they get no empty snapshot.

    Returns {"loaded": [...], "empty": [name], "failed": [{...}]}.
    """
    conn = connect()
    try:
        names  = active_customer_names(conn)
        result = {"loaded": [], "empty": [], "failed": []}

        if verbose:
            print(f"{len(names)} active customers in {SCHEMA_NAME}.customers\n")

        for name in names:
            try:
                dataset = fetch_dataset(name)
            except Exception as exc:
                result["failed"].append({"customer": name, "error": str(exc)})
                if verbose:
                    print(f"  ✗  {name:<45} ERROR: {exc}")
                continue

            if not len(dataset.device):
                result["empty"].append(name)
                if verbose:
                    print(f"  !  {name:<45} no devices returned by the API — skipped")
                continue

            try:
                snapshot_id = save_dataset(dataset, conn, notes=notes)
            except Exception as exc:
                result["failed"].append({"customer": name, "error": str(exc)})
                if verbose:
                    print(f"  ✗  {name:<45} ERROR: {exc}")
                continue

            result["loaded"].append({
                "customer":    name,
                "snapshot_id": snapshot_id,
                "devices":     len(dataset.device),
            })
            if verbose:
                print(f"  ✓  {name:<45} snapshot {snapshot_id:<6} {len(dataset.device):>4} devices")

        if verbose:
            print(
                f"\nDone. {len(result['loaded'])} loaded, "
                f"{len(result['empty'])} with no devices, "
                f"{len(result['failed'])} failed."
            )
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""

    if cmd == "customers":
        for c in fetch_active_customers():
            print(f"  {c['id']:>5}  {c['customer_name']}")
        return 0

    if cmd == "load-active":
        result = load_active_customers()
        # Misses are a warning, not a failure — only real errors set exit 1.
        return 1 if result["failed"] else 0

    if cmd == "load":
        customer = " ".join(argv[2:]) or None
        report   = load_to_postgres(customer)
        print(f"snapshot_id = {report['snapshot_id']}  ({report['captured_at']})")
        for table, count in report["row_counts"].items():
            print(f"  {table:<20} {count:>6} rows")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
