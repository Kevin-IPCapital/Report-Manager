"""
Autotask — PostgreSQL loader.

Insert-only. The `autotask` schema is assumed to already exist (see
sql/schema.sql for its definition); this module neither creates nor migrates it.
Reading the data back is done in SQL — see sql/queries.sql.

Each run inserts one snapshot row and stamps every child row with that
snapshot_id, so loads accumulate history rather than overwrite it.

Connection comes from AUTOTASK_PG_DSN in .env, e.g.
    AUTOTASK_PG_DSN=postgresql://reporting:secret@localhost:5432/ipc_reporting

Every statement names the `autotask` schema explicitly, so the code does not
depend on the connecting role's search_path.

Entry points:
  save_dataset(dataset)             Insert a Dataset, returns snapshot_id
  load_to_postgres(company)         Fetch from API + insert in one call
  fetch_active_customers()          Read active rows from autotask.customers
  active_customer_names()           Same, names only
  load_active_customers()           One snapshot per active customer
  sync_customers(name)              Populate autotask.customers from the API

CLI:
  python autotask_db.py customers                    list active customers
  python autotask_db.py sync-customers "IP Capital"  add/refresh matching companies
  python autotask_db.py load "IP Capital"            load one company
  python autotask_db.py load-active                  one snapshot per active customer
"""

import argparse
import os
import sys

import psycopg2
from psycopg2.extras import Json, execute_values

from autotask_client import DEFAULT_TIMEZONE, AutotaskClient, load_dotenv
from autotask_dataset import TABLE_ORDER, Dataset, fetch_dataset

load_dotenv()

# Every table reference is schema-qualified with this rather than relying on
# search_path — a wrong search_path fails with a confusing "relation does not exist".
SCHEMA_NAME = "autotask"

# Child tables get snapshot_id prepended to their dataset columns.
# company must come first — the others carry a foreign key to it.
CHILD_TABLES = TABLE_ORDER

# Columns holding JSON rather than a scalar; wrapped with psycopg2's Json adapter.
JSON_COLUMNS = {"details"}

DEFAULT_DAYS = 7
DEFAULT_MAX_TICKETS = 500


def _dsn() -> str:
    dsn = os.getenv("AUTOTASK_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "AUTOTASK_PG_DSN is not set. Add it to .env, e.g.\n"
            "  AUTOTASK_PG_DSN=postgresql://user:password@localhost:5432/ipc_reporting"
        )
    return dsn


def connect():
    """Open a connection with search_path pointed at the autotask schema."""
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
                    (captured_at, company_filter, days_back, ticket_count, notes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING snapshot_id
                """,
                (dataset.captured_at, dataset.company_filter, dataset.days_back,
                 len(dataset.ticket), notes),
            )
            snapshot_id = cur.fetchone()[0]

            for name in CHILD_TABLES:
                table = dataset[name]
                if not len(table):
                    continue
                cols = ["snapshot_id"] + table.columns
                json_at = [i for i, c in enumerate(table.columns) if c in JSON_COLUMNS]
                rows = []
                for values in table.tuples():
                    values = list(values)
                    for i in json_at:
                        values[i] = Json(values[i]) if values[i] is not None else None
                    rows.append((snapshot_id, *values))
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


def load_to_postgres(
    company: str,
    last_days: int = DEFAULT_DAYS,
    max_records: int = DEFAULT_MAX_TICKETS,
    timezone_name: str = DEFAULT_TIMEZONE,
    with_notes: bool = True,
    with_contacts: bool = True,
    notes: str = None,
) -> dict:
    """Fetch from the Autotask API and insert a snapshot. Returns a load report."""
    dataset = fetch_dataset(
        company,
        last_days=last_days,
        max_records=max_records,
        timezone_name=timezone_name,
        with_notes=with_notes,
        with_contacts=with_contacts,
    )
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

CUSTOMER_COLUMNS = ["id", "customer_name", "autotask_company_id", "created_at", "is_active"]


def fetch_active_customers(conn=None) -> list:
    """
    Return the active rows of autotask.customers as a list of dicts, name-sorted.

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


def sync_customers(name_filter: str, limit: int = 500, conn=None,
                   verbose: bool = True) -> list:
    """
    Add/refresh autotask.customers from the Autotask API.

    Every active company whose name contains `name_filter` is upserted by name,
    so re-running keeps ids current without creating duplicates. Existing rows
    that a re-run does not match are left alone — deactivate those by hand.
    """
    client = AutotaskClient.from_env()
    response = client.query(
        "Companies",
        [
            {"op": "eq", "field": "IsActive", "value": True},
            {"op": "contains", "field": "CompanyName", "value": name_filter},
        ],
        include_fields=["id", "companyName", "isActive"],
        max_records=limit,
    )
    companies = response.get("items") or []

    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            for company in companies:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA_NAME}.customers
                        (customer_name, autotask_company_id, is_active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (customer_name) DO UPDATE
                       SET autotask_company_id = EXCLUDED.autotask_company_id,
                           is_active           = TRUE
                    """,
                    (company.get("companyName"), company.get("id")),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()

    if verbose:
        print(f"{len(companies)} companies matched '{name_filter}':")
        for company in companies:
            print(f"  {company.get('id'):>6}  {company.get('companyName')}")

    return companies


# ---------------------------------------------------------------------------
# Automated load, driven by autotask.customers
# ---------------------------------------------------------------------------

def load_active_customers(
    last_days: int = DEFAULT_DAYS,
    max_records: int = DEFAULT_MAX_TICKETS,
    timezone_name: str = DEFAULT_TIMEZONE,
    with_notes: bool = True,
    with_contacts: bool = True,
    notes: str = None,
    verbose: bool = True,
) -> dict:
    """
    Load every active customer in autotask.customers, one snapshot each.

    One API fetch and one snapshot per customer, so each gets its own history
    row and a customer that fails does not take the rest of the run with it.
    Customers with no tickets in the window are still snapshotted — an empty
    period is a real result for a ticket report, unlike a missing device list.

    Returns {"loaded": [...], "failed": [{...}]}.
    """
    conn = connect()
    try:
        names  = active_customer_names(conn)
        result = {"loaded": [], "failed": []}

        if verbose:
            print(f"{len(names)} active customers in {SCHEMA_NAME}.customers\n")

        # One client for the whole run so the zone URL is discovered once.
        client = AutotaskClient.from_env()

        for name in names:
            try:
                dataset = fetch_dataset(
                    name,
                    last_days=last_days,
                    max_records=max_records,
                    timezone_name=timezone_name,
                    with_notes=with_notes,
                    with_contacts=with_contacts,
                    client=client,
                )
                snapshot_id = save_dataset(dataset, conn, notes=notes)
            except Exception as exc:
                result["failed"].append({"customer": name, "error": str(exc)})
                if verbose:
                    print(f"  x  {name:<45} ERROR: {exc}")
                continue

            result["loaded"].append({
                "customer":    name,
                "snapshot_id": snapshot_id,
                "tickets":     len(dataset.ticket),
            })
            if verbose:
                print(f"  ok {name:<45} snapshot {snapshot_id:<6} "
                      f"{len(dataset.ticket):>4} tickets")

        if verbose:
            print(f"\nDone. {len(result['loaded'])} loaded, "
                  f"{len(result['failed'])} failed.")
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autotask PostgreSQL loader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("customers", help="List active rows in autotask.customers")

    sync = subparsers.add_parser(
        "sync-customers",
        help="Add/refresh autotask.customers from the Autotask API",
    )
    sync.add_argument("name", help="Company name fragment to match, e.g. \"IP Capital\"")
    sync.add_argument("--limit", type=int, default=500, help="Maximum companies to match")

    for name, help_text in (
        ("load", "Load one company"),
        ("load-active", "Load every active customer, one snapshot each"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        if name == "load":
            sub.add_argument("company", help="Company name, e.g. \"IP Capital: BITCO\"")
        sub.add_argument("--days", type=int, default=DEFAULT_DAYS,
                         help=f"How many days back to look (default {DEFAULT_DAYS})")
        sub.add_argument("--limit", type=int, default=DEFAULT_MAX_TICKETS,
                         help=f"Maximum tickets per company (default {DEFAULT_MAX_TICKETS})")
        sub.add_argument("--timezone", default=DEFAULT_TIMEZONE,
                         help=f"Report time zone (default {DEFAULT_TIMEZONE})")
        sub.add_argument("--no-notes", action="store_true",
                         help="Skip ticket notes (much faster — one less call per ticket)")
        sub.add_argument("--no-contacts", action="store_true",
                         help="Skip company contacts")
        sub.add_argument("--notes-text", help="Free-text note stored on the snapshot row")

    return parser


def _main(argv) -> int:
    args = _build_parser().parse_args(argv[1:])

    if args.command == "customers":
        for customer in fetch_active_customers():
            company_id = customer["autotask_company_id"]
            print(f"  {customer['id']:>5}  {str(company_id or '-'):>6}  "
                  f"{customer['customer_name']}")
        return 0

    if args.command == "sync-customers":
        sync_customers(args.name, limit=args.limit)
        return 0

    if args.command == "load":
        report = load_to_postgres(
            args.company,
            last_days=args.days,
            max_records=args.limit,
            timezone_name=args.timezone,
            with_notes=not args.no_notes,
            with_contacts=not args.no_contacts,
            notes=args.notes_text,
        )
        print(f"snapshot_id = {report['snapshot_id']}  ({report['captured_at']})")
        for table, count in report["row_counts"].items():
            print(f"  {table:<20} {count:>6} rows")
        return 0

    if args.command == "load-active":
        result = load_active_customers(
            last_days=args.days,
            max_records=args.limit,
            timezone_name=args.timezone,
            with_notes=not args.no_notes,
            with_contacts=not args.no_contacts,
            notes=args.notes_text,
        )
        return 1 if result["failed"] else 0

    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
