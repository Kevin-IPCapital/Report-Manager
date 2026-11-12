"""
N-central site list, and the customer each site is allocated to.

Same source as ncentral_clients.py: GET /api/org-units returns service
organisations, customers and sites in one paginated list, told apart by
orgUnitType. This keeps the SITE rows and resolves each one's owning customer by
walking parentId up to the nearest CUSTOMER ancestor — normally the immediate
parent, but the walk means a site nested a level deeper still lands on the right
client instead of being reported as unallocated.

Sites with no CUSTOMER ancestor (parented straight to a service organisation, or
whose parent is not visible to this API token) are kept with customer_id NULL and
counted separately rather than dropped, so a permissions gap looks like one.

The list already excludes deleted/historical org units, so no active-only filter
is needed.

CLI:
  python ncentral_sites.py list                       site + its customer
  python ncentral_sites.py list --customer Acme       one customer (name or id)
  python ncentral_sites.py list --unallocated         sites with no customer
  python ncentral_sites.py list --json                JSON, for piping
  python ncentral_sites.py list --csv out.csv         write a CSV
  python ncentral_sites.py count                      sites per customer
  python ncentral_sites.py sync                       upsert into ncentral.sites
  python ncentral_sites.py sync --deactivate-missing
"""

import argparse
import csv
import json
import os
import sys

import ncentral_client as nc

SCHEMA_NAME = "ncentral"

FIELDS = ["siteId", "siteName", "parentId", "customerId", "customerName"]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch(customer: str = None, unallocated_only: bool = False) -> list:
    """
    Every site, each tagged with the customer it is allocated to.

    `customer` filters by orgUnitId if it is all digits, otherwise by a
    case-insensitive substring of the customer name — so "acme" matches
    "Acme Holdings" without needing the exact string.
    """
    units = nc.list_org_units()
    by_id = {u.get("orgUnitId"): u for u in units}

    sites = [_resolve(u, by_id) for u in units if u.get("orgUnitType") == "SITE"]

    if unallocated_only:
        sites = [s for s in sites if s["customerId"] is None]
    elif customer:
        sites = [s for s in sites if _matches(s, customer)]

    # Unallocated sites sort last, where they read as an exception list.
    return sorted(sites, key=lambda s: (s["customerName"] is None,
                                        (s["customerName"] or "").lower(),
                                        (s["siteName"] or "").lower()))


def _resolve(site: dict, by_id: dict) -> dict:
    """Site row plus its nearest CUSTOMER ancestor, found by walking parentId."""
    seen = set()
    node = by_id.get(site.get("parentId"))
    while node is not None and node.get("orgUnitId") not in seen:
        if node.get("orgUnitType") == "CUSTOMER":
            break
        seen.add(node.get("orgUnitId"))
        node = by_id.get(node.get("parentId"))
    else:
        # Loop exited without break: ran out of parents, or hit a cycle.
        node = None

    return {
        "siteId": site.get("orgUnitId"),
        "siteName": site.get("orgUnitName"),
        "parentId": site.get("parentId"),
        "customerId": node.get("orgUnitId") if node else None,
        "customerName": node.get("orgUnitName") if node else None,
    }


def _matches(site: dict, customer: str) -> bool:
    if customer.isdigit():
        return site["customerId"] == int(customer)
    return customer.lower() in (site["customerName"] or "").lower()


def group_by_customer(sites) -> dict:
    """{customer_name: [site, ...]} — unallocated sites under a "(none)" key."""
    grouped = {}
    for site in sites:
        grouped.setdefault(site["customerName"] or "(no customer)", []).append(site)
    return grouped


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_list(sites) -> None:
    for site in sites:
        print(f"  {site['siteId']:>7}  {site['siteName']}"
              f"  <- {site['customerName'] or '(no customer)'}")
    orphans = sum(1 for s in sites if s["customerId"] is None)
    print(f"\n{len(sites)} site(s)"
          + (f", {orphans} with no customer" if orphans else ""))


def print_counts(sites) -> None:
    grouped = group_by_customer(sites)
    for name in sorted(grouped, key=str.lower):
        print(f"  {len(grouped[name]):>4}  {name}")
    print(f"\n{len(sites)} site(s) across {len(grouped)} customer(s)")


def write_csv(sites, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sites)
    print(f"{len(sites)} row(s) written to {path}")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync(deactivate_missing: bool = False, verbose: bool = True) -> list:
    """
    Upsert sites into ncentral.sites.

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

    sites = fetch()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {SCHEMA_NAME}, public")
            cur.execute("SELECT to_regclass(%s)", (f"{SCHEMA_NAME}.sites",))
            if cur.fetchone()[0] is None:
                raise RuntimeError(
                    f"{SCHEMA_NAME}.sites does not exist. Apply the schema first:\n"
                    "  python ncentral_performance.py init"
                )

            for site in sites:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.sites
                            (site_id, site_name, parent_id, customer_id,
                             customer_name, is_active)
                        VALUES (%s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (site_id) DO UPDATE
                           SET site_name     = EXCLUDED.site_name,
                               parent_id     = EXCLUDED.parent_id,
                               customer_id   = EXCLUDED.customer_id,
                               customer_name = EXCLUDED.customer_name,
                               is_active     = TRUE""",
                    (site["siteId"], site["siteName"], site["parentId"],
                     site["customerId"], site["customerName"]))

            deactivated = 0
            if deactivate_missing and sites:
                # Off by default, same reasoning as ncentral_clients.py: a site
                # the API stops returning is usually a permissions or paging
                # problem, not a decommissioned site.
                cur.execute(
                    f"""UPDATE {SCHEMA_NAME}.sites
                        SET is_active = FALSE
                        WHERE is_active AND site_id <> ALL(%s)""",
                    ([s["siteId"] for s in sites],))
                deactivated = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if verbose:
        print_counts(sites)
        if deactivate_missing:
            print(f"{deactivated} deactivated")

    return sites


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="N-central sites, and the customer each is allocated to")
    subs = parser.add_subparsers(dest="command", required=True)

    list_parser = subs.add_parser("list", help="Print sites with their customer")
    list_parser.add_argument("--customer", metavar="NAME_OR_ID",
                             help="Only sites under this customer (orgUnitId, or "
                                  "part of the customer name)")
    list_parser.add_argument("--unallocated", action="store_true",
                             help="Only sites with no CUSTOMER parent")
    list_parser.add_argument("--json", action="store_true", help="JSON output")
    list_parser.add_argument("--csv", metavar="PATH", help="Write a CSV")

    count_parser = subs.add_parser("count", help="Site count per customer")
    count_parser.add_argument("--customer", metavar="NAME_OR_ID",
                              help="Only sites under this customer")

    sync_parser = subs.add_parser("sync", help="Upsert into ncentral.sites")
    sync_parser.add_argument("--deactivate-missing", action="store_true",
                             help="Mark sites the API no longer returns inactive")

    args = parser.parse_args(argv)

    if args.command == "list":
        sites = fetch(customer=args.customer, unallocated_only=args.unallocated)
        if args.csv:
            write_csv(sites, args.csv)
        elif args.json:
            print(json.dumps(sites, indent=2))
        else:
            print_list(sites)
        return 0

    if args.command == "count":
        print_counts(fetch(customer=args.customer))
        return 0

    if args.command == "sync":
        sync(deactivate_missing=args.deactivate_missing)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
