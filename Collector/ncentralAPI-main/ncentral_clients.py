"""
N-central client (customer) list.

There is no customers endpoint — GET /api/org-units returns service
organisations, customers and sites in one paginated list, told apart by
orgUnitType. This wraps that: list them, see the SO -> customer -> site tree, or
push the customer rows into ncentral.customers, which is the driver table both
collectors read.

The list already excludes deleted/historical org units, so no active-only filter
is needed.

CLI:
  python ncentral_clients.py list                 customers, one per line
  python ncentral_clients.py list --all           every org unit, with its type
  python ncentral_clients.py list --json          JSON, for piping
  python ncentral_clients.py list --csv out.csv   write a CSV
  python ncentral_clients.py tree                 SO -> customer -> site
  python ncentral_clients.py sync                 upsert into ncentral.customers
  python ncentral_clients.py sync --deactivate-missing
"""

import argparse
import csv
import json
import os
import sys

import ncentral_client as nc

SCHEMA_NAME = "ncentral"

FIELDS = ["orgUnitId", "orgUnitName", "orgUnitType", "parentId"]


def fetch(include_all: bool = False) -> list:
    units = nc.list_org_units()
    if not include_all:
        units = [u for u in units if u.get("orgUnitType") == "CUSTOMER"]
    return sorted(units, key=lambda u: (u.get("orgUnitType") or "",
                                        u.get("orgUnitName") or ""))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_list(units, include_all: bool) -> None:
    if include_all:
        for unit in units:
            print(f"  {unit.get('orgUnitId'):>7}  {str(unit.get('orgUnitType') or '-'):<9} "
                  f"{unit.get('orgUnitName')}")
    else:
        for unit in units:
            print(f"  {unit.get('orgUnitId'):>7}  {unit.get('orgUnitName')}")
    print(f"\n{len(units)} org unit(s)" if include_all else f"\n{len(units)} customer(s)")


def print_tree(units) -> None:
    """SO -> customer -> site, built from parentId."""
    by_parent = {}
    for unit in units:
        by_parent.setdefault(unit.get("parentId"), []).append(unit)

    known_ids = {u.get("orgUnitId") for u in units}
    # Roots are units whose parent is not itself in the list (or is null).
    roots = [u for u in units if u.get("parentId") not in known_ids]

    def walk(unit, depth):
        print(f"{'    ' * depth}{unit.get('orgUnitName')}  "
              f"[{unit.get('orgUnitType')} {unit.get('orgUnitId')}]")
        for child in sorted(by_parent.get(unit.get("orgUnitId"), []),
                            key=lambda c: c.get("orgUnitName") or ""):
            walk(child, depth + 1)

    for root in sorted(roots, key=lambda r: r.get("orgUnitName") or ""):
        walk(root, 0)


def write_csv(units, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(units)
    print(f"{len(units)} row(s) written to {path}")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync(deactivate_missing: bool = False, verbose: bool = True) -> list:
    """
    Upsert customers into ncentral.customers.

    The table is defined once, in sql/schema.sql — apply it first with
    `python ncentral_performance.py init` (or ncentral_patch.py init; both run the
    same file). This script deliberately does not create it, so there is only one
    definition to keep correct.
    """
    import psycopg2

    dsn = os.getenv("NCENTRAL_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "NCENTRAL_PG_DSN is not set. Add it to .env, e.g.\n"
            "  NCENTRAL_PG_DSN=postgresql://user:password@host:5432/dbname"
        )

    customers = fetch()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {SCHEMA_NAME}, public")
            cur.execute("SELECT to_regclass(%s)", (f"{SCHEMA_NAME}.customers",))
            if cur.fetchone()[0] is None:
                raise RuntimeError(
                    f"{SCHEMA_NAME}.customers does not exist. Apply the schema first:\n"
                    "  python ncentral_performance.py init"
                )

            for customer in customers:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.customers
                            (org_unit_id, customer_name, is_active)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (org_unit_id) DO UPDATE
                           SET customer_name = EXCLUDED.customer_name,
                               is_active     = TRUE""",
                    (customer.get("orgUnitId"), customer.get("orgUnitName")))

            deactivated = 0
            if deactivate_missing and customers:
                # Off by default: a customer the API stops returning is usually a
                # permissions or paging problem, not a closed account, and
                # deactivating stops collection for it.
                cur.execute(
                    f"""UPDATE {SCHEMA_NAME}.customers
                        SET is_active = FALSE
                        WHERE is_active AND org_unit_id <> ALL(%s)""",
                    ([c.get("orgUnitId") for c in customers],))
                deactivated = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if verbose:
        for customer in customers:
            print(f"  {customer.get('orgUnitId'):>7}  {customer.get('orgUnitName')}")
        print(f"\n{len(customers)} customer(s) synced"
              + (f", {deactivated} deactivated" if deactivate_missing else ""))
    return customers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="N-central client/customer list")
    subs = parser.add_subparsers(dest="command", required=True)

    list_parser = subs.add_parser("list", help="Print customers")
    list_parser.add_argument("--all", action="store_true",
                             help="Every org unit (SOs and sites too), with its type")
    list_parser.add_argument("--json", action="store_true", help="JSON output")
    list_parser.add_argument("--csv", metavar="PATH", help="Write a CSV")

    subs.add_parser("tree", help="SO -> customer -> site hierarchy")

    sync_parser = subs.add_parser("sync", help="Upsert into ncentral.customers")
    sync_parser.add_argument("--deactivate-missing", action="store_true",
                             help="Mark customers the API no longer returns inactive")

    args = parser.parse_args(argv)

    if args.command == "list":
        units = fetch(include_all=args.all)
        if args.csv:
            write_csv(units, args.csv)
        elif args.json:
            print(json.dumps([{k: u.get(k) for k in FIELDS} for u in units], indent=2))
        else:
            print_list(units, args.all)
        return 0

    if args.command == "tree":
        print_tree(fetch(include_all=True))
        return 0

    if args.command == "sync":
        sync(deactivate_missing=args.deactivate_missing)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
