"""
SentinelOne EDR — PostgreSQL loader.

Insert-only, mirroring cove_db.py. The `sentinelone` schema is assumed to
already exist (see sql/schema.sql); this module neither creates nor migrates it,
with one exception: sync_customers() maintains sentinelone.customers, because
that roster is what drives per-customer runs and it has to come from somewhere.

Each run inserts one snapshot row and stamps every child row with that
snapshot_id, so loads accumulate history rather than overwrite it.

Connection comes from the first of these that is set:
    SENTINELONE_PG_DSN, S1_PG_DSN, IPC_PG_DSN, COVE_PG_DSN
(the Cove DSN is a deliberate fallback — same `ipcreporting` database, different
schema, so an existing install needs no new secret.)

Every statement names the `sentinelone` schema explicitly, so the code does not
depend on the connecting role's search_path.

Entry points:
  save_dataset(dataset)                Insert a Dataset, returns snapshot_id
  load_month(month, customer)          Fetch from API + insert in one call
  sync_customers()                     Refresh sentinelone.customers from /sites
  fetch_active_customers()             Read active rows from sentinelone.customers
  active_customer_names()              Same, names only
  load_active_customers(month)         One snapshot per active customer

The month argument is optional everywhere and defaults to the current month,
covering the 1st through the end of today — so a daily run takes no arguments.

CLI:
  python s1_db.py sync-customers               refresh the roster from the console
  python s1_db.py customers                    list active customers
  python s1_db.py load                         current month to date, whole estate
  python s1_db.py load "Acme"                  current month to date, one customer
  python s1_db.py load 2026-06                 a specific complete month
  python s1_db.py load 2026-06 "Acme"          one customer, specific month
  python s1_db.py load-active                  one snapshot per active customer
  python s1_db.py load-active 2026-06          same, for a specific month
"""

import os
import re

import psycopg2
from psycopg2.extras import execute_values

from s1_client import S1Client, load_env
from s1_dataset import (
    Dataset, INSERT_ORDER, current_month, fetch_dataset, resolve_site_id,
)

# Every table reference is schema-qualified with this rather than relying on
# search_path — a wrong search_path fails with a confusing "relation does not exist".
SCHEMA_NAME = "sentinelone"

DSN_KEYS = ["SENTINELONE_PG_DSN", "S1_PG_DSN", "IPC_PG_DSN", "COVE_PG_DSN"]

CUSTOMER_COLUMNS = ["id", "site_id", "customer_name", "created_at", "is_active"]


def _dsn() -> str:
    env = load_env()
    for key in DSN_KEYS:
        dsn = os.getenv(key) or env.get(key)
        if dsn:
            return dsn
    raise RuntimeError(
        "No database DSN found. Add one to .env, e.g.\n"
        "  SENTINELONE_PG_DSN=postgresql://user:password@host:5432/ipcreporting\n"
        f"(checked: {', '.join(DSN_KEYS)})"
    )


def connect():
    """Open a connection with search_path pointed at the sentinelone schema."""
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
    half-written snapshot behind. Tables go in INSERT_ORDER because every child
    table carries a foreign key to customer_month.
    """
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA_NAME}.snapshot
                    (captured_at, report_month, period_label,
                     month_start, month_end, is_partial_month, days_covered,
                     customer_filter, site_id_filter,
                     customer_count, threat_count, agent_count,
                     latest_agent_version, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING snapshot_id
                """,
                (dataset.captured_at, dataset.report_month, dataset.period_label,
                 dataset.month_start, dataset.month_end,
                 dataset.is_partial_month, dataset.days_covered,
                 dataset.customer_filter, dataset.site_id_filter,
                 len(dataset.customer_month), len(dataset.threat), len(dataset.agent),
                 dataset.latest_agent_version, notes),
            )
            snapshot_id = cur.fetchone()[0]

            for name in INSERT_ORDER:
                table = dataset[name]
                if not len(table):
                    continue
                cols = ["snapshot_id"] + table.columns
                rows = [(snapshot_id, *t) for t in table.tuples()]
                execute_values(
                    cur,
                    f'INSERT INTO {SCHEMA_NAME}.{name} ({", ".join(cols)}) VALUES %s',
                    rows,
                    page_size=500,
                )

        conn.commit()
        return snapshot_id
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def load_month(month: str = None, customer: str = None, site_id: str = None,
               notes: str = None, client: S1Client = None) -> dict:
    """
    Fetch a month from the API and insert a snapshot. Returns a load report.

    month defaults to the current month, covering the 1st through the end of
    today — so a daily run needs no arguments at all.
    """
    dataset = fetch_dataset(month, customer=customer, site_id=site_id, client=client)
    conn = connect()
    try:
        snapshot_id = save_dataset(dataset, conn, notes=notes)
    finally:
        conn.close()

    report = dataset.summary()
    report["snapshot_id"] = snapshot_id
    return report


# ---------------------------------------------------------------------------
# Customers roster
# ---------------------------------------------------------------------------

def sync_customers(conn=None, client: S1Client = None, verbose: bool = True) -> dict:
    """
    Refresh sentinelone.customers from /web/api/v2.1/sites.

    Upsert by site_id, so a renamed site keeps its row (and its history stays
    joinable on site_id). is_active is only *set* on insert — once an operator
    has deactivated a customer here, a sync will not silently re-enable it; the
    console's own state lands in s1_state instead.

    Returns {"inserted": n, "updated": n, "total": n}.
    """
    own = conn is None
    conn = conn or connect()
    client = client or S1Client.from_env()
    try:
        sites = client.get_sites()
        rows = [
            (
                str(s.get("id")),
                s.get("name"),
                s.get("state"),
                s.get("siteType"),
                s.get("totalLicenses"),
                s.get("activeLicenses"),
                s.get("expiration"),
                # Only sites the console still calls active default to active here.
                (s.get("state") or "").lower() == "active",
            )
            for s in sites if s.get("id") and s.get("name")
        ]

        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {SCHEMA_NAME}.customers")
            before = cur.fetchone()[0]

            execute_values(
                cur,
                f"""
                INSERT INTO {SCHEMA_NAME}.customers
                    (site_id, customer_name, s1_state, site_type,
                     total_licenses, active_licenses, expiration, is_active)
                VALUES %s
                ON CONFLICT (site_id) DO UPDATE SET
                    customer_name   = EXCLUDED.customer_name,
                    s1_state        = EXCLUDED.s1_state,
                    site_type       = EXCLUDED.site_type,
                    total_licenses  = EXCLUDED.total_licenses,
                    active_licenses = EXCLUDED.active_licenses,
                    expiration      = EXCLUDED.expiration,
                    last_synced_at  = now()
                """,
                rows,
            )

            cur.execute(f"SELECT count(*) FROM {SCHEMA_NAME}.customers")
            after = cur.fetchone()[0]

            # last_synced_at is left NULL by the INSERT branch; fill it in.
            cur.execute(
                f"UPDATE {SCHEMA_NAME}.customers SET last_synced_at = now() "
                f"WHERE last_synced_at IS NULL"
            )
        conn.commit()

        result = {
            "inserted": after - before,
            "updated":  len(rows) - (after - before),
            "total":    after,
        }
        if verbose:
            print(f"{SCHEMA_NAME}.customers: {result['inserted']} new, "
                  f"{result['updated']} updated, {result['total']} total")
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def fetch_active_customers(conn=None) -> list:
    """
    Return the active rows of sentinelone.customers as dicts, name-sorted.

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


def lookup_site_id(customer: str, conn=None) -> str:
    """
    Resolve a customer name to a site id using the roster, falling back to /sites.

    Preferring the table avoids an API round-trip on every single-customer run.
    """
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT site_id, customer_name FROM {SCHEMA_NAME}.customers
                WHERE lower(customer_name) = lower(%s)
                """,
                (customer.strip(),),
            )
            hit = cur.fetchall()
            if len(hit) == 1:
                return hit[0][0]

            cur.execute(
                f"""
                SELECT site_id, customer_name FROM {SCHEMA_NAME}.customers
                WHERE customer_name ILIKE %s
                """,
                (f"%{customer.strip()}%",),
            )
            hits = cur.fetchall()
        if len(hits) == 1:
            return hits[0][0]
        if len(hits) > 1:
            names = ", ".join(sorted(h[1] for h in hits))
            raise ValueError(f'"{customer}" matches {len(hits)} customers: {names}')
    finally:
        if own:
            conn.close()

    # Not in the roster — the console is the authority, and it may be a new site.
    return resolve_site_id(customer)


# ---------------------------------------------------------------------------
# Automated load, driven by sentinelone.customers
# ---------------------------------------------------------------------------

def load_active_customers(month: str = None, notes: str = None,
                          verbose: bool = True) -> dict:
    """
    Load every active customer in sentinelone.customers, one snapshot each.

    One API fetch and one snapshot per customer, so each gets its own history row
    and a customer that fails does not take the rest of the run with it.
    Customers the API returns nothing for are reported as misses and skipped —
    they get no empty snapshot.

    Returns {"loaded": [...], "empty": [name], "failed": [{...}]}.
    """
    month = month or current_month()
    client = S1Client.from_env()
    conn = connect()
    try:
        customers = fetch_active_customers(conn)
        result = {"loaded": [], "empty": [], "failed": []}

        if verbose:
            print(f"{len(customers)} active customers in {SCHEMA_NAME}.customers "
                  f"— loading {month}\n")

        for c in customers:
            name, site_id = c["customer_name"], c["site_id"]
            try:
                dataset = fetch_dataset(month, customer=name, site_id=site_id,
                                        client=client)
            except Exception as exc:
                result["failed"].append({"customer": name, "error": str(exc)})
                if verbose:
                    print(f"  x  {name:<45} ERROR: {exc}")
                continue

            if not len(dataset.customer_month):
                result["empty"].append(name)
                if verbose:
                    print(f"  !  {name:<45} no threats or agents returned — skipped")
                continue

            try:
                snapshot_id = save_dataset(dataset, conn, notes=notes)
            except Exception as exc:
                result["failed"].append({"customer": name, "error": str(exc)})
                if verbose:
                    print(f"  x  {name:<45} ERROR: {exc}")
                continue

            result["loaded"].append({
                "customer":    name,
                "snapshot_id": snapshot_id,
                "threats":     len(dataset.threat),
                "agents":      len(dataset.agent),
            })
            if verbose:
                print(f"  OK {name:<45} snapshot {snapshot_id:<6} "
                      f"{len(dataset.threat):>5} threats  {len(dataset.agent):>4} agents")

        if verbose:
            print(f"\nDone. {len(result['loaded'])} loaded, "
                  f"{len(result['empty'])} empty, {len(result['failed'])} failed.")
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _is_month(token: str) -> bool:
    """True for a 'YYYY-MM' argument, so month and customer name cannot be confused."""
    return bool(re.fullmatch(r"\d{4}-\d{2}", token or ""))


def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""

    if cmd == "sync-customers":
        sync_customers()
        return 0

    if cmd == "customers":
        rows = fetch_active_customers()
        for c in rows:
            print(f"  {c['id']:>5}  {c['site_id']:<22} {c['customer_name']}")
        print(f"\n{len(rows)} active")
        return 0

    if cmd == "load":
        # A leading YYYY-MM is optional; anything else is treated as a customer
        # name, so `load` and `load "Acme"` both mean "the current month".
        rest = argv[2:]
        month = None
        if rest and _is_month(rest[0]):
            month, rest = rest[0], rest[1:]
        customer = " ".join(rest) or None
        site_id = lookup_site_id(customer) if customer else None
        report = load_month(month, customer=customer, site_id=site_id)
        print(f"snapshot_id = {report['snapshot_id']}  "
              f"({report['period']}, captured {report['captured_at']})")
        for table, count in report["row_counts"].items():
            if count:
                print(f"  {table:<18} {count:>6} rows")
        return 0

    if cmd == "load-active":
        month = argv[2] if len(argv) > 2 and _is_month(argv[2]) else None
        result = load_active_customers(month)
        # Misses are a warning, not a failure — only real errors set exit 1.
        return 1 if result["failed"] else 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
