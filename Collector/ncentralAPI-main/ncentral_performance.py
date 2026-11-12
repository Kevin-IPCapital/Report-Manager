"""
N-central server performance collector — hourly cron target.

Fetches the performance monitors (CPU, Memory, Disk, Disk I/O, Uptime, hardware
health) for every device under every active customer and records two things:

  1. per device, per poll — was it reporting          -> uptime, hourly resolution
  2. per monitor, per state change — what state, when -> exact time in breach

Availability falls out of (1) rather than needing its own feed: if a device's
monitors scanned recently then the agent reported in, so the device and the path
to it were up. (1) exists because the reverse is silent — a dead server returns
no performance data at all, so without an explicit per-poll row its downtime
would look like missing data instead of an outage.

WHAT THIS CANNOT DO: N-central exposes monitor *state*, never a value. Verified
against REST (?includeData / ?details / ?expand ignored; lastDataId null on every
monitor) and the legacy SOAP deviceGetStatus (same 19 fields). So you get
"Disk C: was Warning for 14 hours", never "C: is 8% free". If you need real
percentages, that needs an Automation Manager script writing them into a custom
property on the N-central side.

Connection comes from NCENTRAL_PG_DSN in .env, e.g.
    NCENTRAL_PG_DSN=postgresql://user:password@host:5432/dbname

Everything lives in the `ncentral` schema; every statement is schema-qualified,
so a wrong search_path cannot write elsewhere.

CLI:
  python ncentral_performance.py init             create/upgrade the schema
  python ncentral_performance.py sync-customers   fill ncentral.customers
  python ncentral_performance.py sync-sites       fill ncentral.sites (site names)
  python ncentral_performance.py collect          one poll — the cron target
  python ncentral_performance.py collect --dry-run  poll and print, write nothing
  python ncentral_performance.py status           current state
  python ncentral_performance.py report           30-day uptime + time in breach
  python ncentral_performance.py report --by-site same, rolled up per site
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

import ncentral_client as nc

SCHEMA_NAME = "ncentral"

# A device counts as reporting if its freshest monitor scan is newer than this.
# Sized against the poll interval, not the monitors: scan intervals vary from 30
# to 4320 minutes, so a short window would call a healthy device dead just
# because its quickest monitor happens to be slow. 180 min tolerates a missed
# scan or two while still catching a machine that stopped talking hours ago.
REPORTING_WINDOW_MINS = 180

# stateStatus -> state_class for monitors NOT in active-issues. Anything in
# active-issues is 'breached' regardless of what its own row says.
STATE_CLASSES = {
    "normal": "ok",
    "warning": "degraded",
    "failed": "breached",
    "no data": "unknown",
    "misconfigured": "unknown",
    "stale": "unknown",
    "disconnected": "unknown",
}


def _local(value):
    """
    Parse an API timestamp to NAIVE LOCAL time.

    The API mixes shapes on the same record: lastScanTime carries an offset
    (+0200) while transitionTime does not. The schema is naive local throughout,
    so aware values are converted to local and stripped rather than stored
    alongside naive ones — mixing the two raises on the first subtraction.
    """
    parsed = nc._parse_api_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def is_performance_module(module: str) -> bool:
    """Category comes from the client so it cannot drift from its health view."""
    return nc._bucket_for(module) == "performance"


def classify(state: str, in_active_issue: bool) -> str:
    if in_active_issue:
        return "breached"
    return STATE_CLASSES.get((state or "").strip().lower(), "unknown")


def _dsn() -> str:
    dsn = os.getenv("NCENTRAL_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "NCENTRAL_PG_DSN is not set. Add it to .env, e.g.\n"
            "  NCENTRAL_PG_DSN=postgresql://user:password@host:5432/dbname"
        )
    return dsn


def connect():
    conn = psycopg2.connect(_dsn())
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {SCHEMA_NAME}, public")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Schema / customers
# ---------------------------------------------------------------------------

def init_schema() -> None:
    sql_path = Path(__file__).with_name("sql") / "schema.sql"
    conn = psycopg2.connect(_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(sql_path.read_text(encoding="utf-8"))
        conn.commit()
        print(f"schema applied from {sql_path}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_customers(verbose: bool = True) -> list:
    customers = nc.get_customers()
    conn = connect()
    try:
        with conn.cursor() as cur:
            for customer in customers:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.customers
                            (org_unit_id, customer_name, is_active)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (org_unit_id) DO UPDATE
                           SET customer_name = EXCLUDED.customer_name,
                               is_active     = TRUE""",
                    (customer.get("orgUnitId"), customer.get("orgUnitName")),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if verbose:
        for customer in customers:
            print(f"  {customer.get('orgUnitId'):>6}  {customer.get('orgUnitName')}")
    return customers


def sync_sites(verbose: bool = True) -> list:
    """Fill ncentral.sites — names the site_id every device row now carries.

    Delegates to ncentral_sites rather than repeating the upsert, so the
    parentId-to-customer resolution has one definition. Not required for
    collection: the collector writes site_id from the device payload either way,
    and this only supplies the names v_site_perf_30d prefers.
    """
    import ncentral_sites

    return ncentral_sites.sync(verbose=verbose)


def active_org_units(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(f"""SELECT org_unit_id, customer_name FROM {SCHEMA_NAME}.customers
                        WHERE is_active ORDER BY customer_name""")
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

def _gather(org_unit_id, customer_name, polled_at):
    """Poll one org unit. Returns (devices, device_samples, checks, observations)."""
    devices = nc.get_devices_for_org_unit(org_unit_id)
    issues = nc.get_active_issues(org_unit_id)
    issue_tasks = {(i.get("deviceId"), i.get("taskId")) for i in issues}

    def load(device, _index):
        try:
            # use_cache=False: a cached read carries an old transitionTime and
            # would skip whatever changed inside the cache window.
            return {"device": device,
                    "monitors": nc.get_service_monitor_status(device["deviceId"],
                                                              use_cache=False),
                    "error": None}
        except nc.NCentralError as error:
            if error.status == 429 or (error.status is not None and error.status >= 500):
                raise  # Stop the collection on rate limits / upstream failure.
            return {"device": device, "monitors": [], "error": str(error)}

    results = nc.with_concurrency_limit(devices, load, limit=1)

    device_rows, samples, check_rows, observations, errors = [], [], [], [], []

    for result in results:
        device = result["device"]
        device_id = device.get("deviceId")
        monitors = result["monitors"]
        error = result["error"]
        if error:
            errors.append(f"device {device_id}: {error}")

        perf = [m for m in monitors if is_performance_module(m.get("moduleName"))]

        scans = [_local(m.get("lastScanTime")) for m in monitors]
        last_scan = max([s for s in scans if s], default=None)
        scan_age_mins = None
        if last_scan is not None:
            scan_age_mins = round(max(0.0, (polled_at - last_scan).total_seconds() / 60.0), 1)

        # Liveness is deliberately three-valued. NULL means "cannot tell", which
        # keeps such polls out of the uptime denominator instead of counting them
        # as downtime: an API failure is our problem, and a device with no
        # monitors at all (the billing-only switch) is unmonitored, not down.
        if error or not monitors:
            reporting = None
        elif scan_age_mins is None:
            reporting = False
        else:
            reporting = scan_age_mins <= REPORTING_WINDOW_MINS

        breach_count = 0
        for monitor in perf:
            task_id = monitor.get("taskId")
            module = (monitor.get("moduleName") or "").strip()
            transition = _local(monitor.get("transitionTime"))
            in_issue = (device_id, task_id) in issue_tasks
            state_class = classify(monitor.get("stateStatus"), in_issue)
            if state_class in ("degraded", "breached"):
                breach_count += 1

            if task_id is None or transition is None:
                # No task id = no check identity; no transition time = no interval
                # boundary. Either way it cannot join the event log. It still
                # counted toward breach_count above.
                continue

            check_rows.append((device_id, task_id, module, monitor.get("taskIdent"),
                               monitor.get("serviceId"), monitor.get("timeToStale")))
            observations.append({
                "device_id": device_id,
                "task_id": task_id,
                "transition_time": transition,
                "state": monitor.get("stateStatus"),
                "state_class": state_class,
                "in_active_issue": in_issue,
                "module": module,
                "instance": monitor.get("taskIdent"),
                "device_name": device.get("longName"),
            })

        device_rows.append((
            device_id, device.get("longName"), device.get("deviceClass"),
            device.get("supportedOsLabel") or device.get("supportedOs"),
            # siteId comes free on the device row — the org-unit devices endpoint
            # tags every device with the site it actually sits in, so no per-site
            # call is needed to attribute it.
            device.get("siteId"), device.get("siteName"),
            customer_name, org_unit_id, device.get("uri"),
            _local(device.get("lastApplianceCheckinTime")),
            last_scan, len(monitors), len(perf),
        ))
        samples.append({
            "device_id": device_id,
            "device_name": device.get("longName"),
            "reporting": reporting,
            "last_scan": last_scan,
            "scan_age_mins": scan_age_mins,
            "monitor_count": len(monitors),
            "breach_count": breach_count,
            "error": error,
        })

    return device_rows, samples, check_rows, observations, errors


def collect(dry_run: bool = False, verbose: bool = True) -> dict:
    polled_at = datetime.now()

    if dry_run:
        customers = [(c.get("orgUnitId"), c.get("orgUnitName")) for c in nc.get_customers()]
        totals = {"devices": 0, "checks": 0}
        for org_unit_id, customer_name in customers:
            devices, samples, checks, observations, errors = _gather(
                org_unit_id, customer_name, polled_at)
            totals["devices"] += len(devices)
            totals["checks"] += len(checks)

            print(f"\n{customer_name} ({org_unit_id}): {len(devices)} devices, "
                  f"{len(checks)} performance checks")
            print(f"  {'device':<26} {'reporting':<10} {'scan age':>9}  {'monitors':>8} {'breaches':>8}")
            for s in sorted(samples, key=lambda s: (s["reporting"] is True,
                                                    s["device_name"] or "")):
                age = "-" if s["scan_age_mins"] is None else f"{s['scan_age_mins']:.0f}m"
                print(f"  {(s['device_name'] or '?')[:26]:<26} "
                      f"{str(s['reporting']):<10} {age:>9}  "
                      f"{s['monitor_count']:>8} {s['breach_count']:>8}")
            for obs in (o for o in observations if o["state_class"] != "ok"):
                print(f"     {obs['state_class']:<9} {obs['module']:<34} "
                      f"{str(obs['instance'] or '-'):<8} {obs['device_name']} "
                      f"since {obs['transition_time']}")
            for e in errors:
                print(f"  ERROR {e}")

        print(f"\ndry run — nothing written. {totals['devices']} devices, "
              f"{totals['checks']} performance checks.")
        return {"dry_run": True, **totals}

    conn = connect()
    result = {"devices": 0, "checks": 0, "events": 0, "errors": []}
    try:
        for org_unit_id, customer_name in active_org_units(conn):
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.poll_run (started_at, org_unit_id)
                        VALUES (%s, %s) RETURNING poll_id""",
                    (polled_at, org_unit_id))
                poll_id = cur.fetchone()[0]

            try:
                devices, samples, checks, observations, errors = _gather(
                    org_unit_id, customer_name, polled_at)
            except Exception as exc:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {SCHEMA_NAME}.poll_run
                            SET ok = FALSE, error = %s, finished_at = %s
                            WHERE poll_id = %s""",
                        (str(exc), datetime.now(), poll_id))
                conn.commit()
                result["errors"].append(f"{customer_name}: {exc}")
                if verbose:
                    print(f"  x  {customer_name}: {exc}")
                continue

            events = _write(conn, poll_id, polled_at, devices, samples,
                            checks, observations)

            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {SCHEMA_NAME}.poll_run
                        SET finished_at = %s, device_count = %s, check_count = %s,
                            event_count = %s, ok = %s, error = %s
                        WHERE poll_id = %s""",
                    (datetime.now(), len(devices), len(checks), events,
                     not errors, "; ".join(errors) or None, poll_id))
            conn.commit()

            result["devices"] += len(devices)
            result["checks"] += len(checks)
            result["events"] += events
            result["errors"].extend(errors)

            down = [s["device_name"] for s in samples if s["reporting"] is not True]
            if verbose:
                print(f"  ok {customer_name:<26} {len(devices):>3} devices "
                      f"{len(checks):>4} checks {events:>3} changes"
                      + (f"   not reporting: {', '.join(d or '?' for d in down)}"
                         if down else ""))
    finally:
        conn.close()

    if verbose:
        for error in result["errors"]:
            print(f"     warn: {error}")
    return result


def _dedupe(rows, key):
    """Last row wins per key.

    Postgres rejects an ON CONFLICT DO UPDATE whose single statement proposes the
    same conflict key twice (CardinalityViolation: "cannot affect row a second
    time"), so the two DO UPDATE upserts below have to be unique on their key
    before they are sent. The DO NOTHING ones tolerate duplicates by themselves
    and are left alone.
    """
    unique = {}
    for row in rows:
        unique[key(row)] = row
    return list(unique.values())


def _write(conn, poll_id, polled_at, device_rows, samples, check_rows, observations) -> int:
    now = datetime.now()
    # One row per device_id / per (device_id, task_id) — a device reachable under
    # two org units in the same pull would otherwise abort the whole poll.
    device_rows = _dedupe(device_rows, lambda row: row[0])
    check_rows = _dedupe(check_rows, lambda row: (row[0], row[1]))
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""INSERT INTO {SCHEMA_NAME}.device
                    (device_id, device_name, device_class, os, site_id, site_name,
                     customer_name, org_unit_id, device_uri, last_checkin,
                     last_scan, monitor_count, perf_check_count, first_seen, last_seen)
                VALUES %s
                ON CONFLICT (device_id) DO UPDATE
                   SET device_name      = EXCLUDED.device_name,
                       device_class     = EXCLUDED.device_class,
                       os               = EXCLUDED.os,
                       site_id          = EXCLUDED.site_id,
                       site_name        = EXCLUDED.site_name,
                       customer_name    = EXCLUDED.customer_name,
                       org_unit_id      = EXCLUDED.org_unit_id,
                       device_uri       = EXCLUDED.device_uri,
                       last_checkin     = EXCLUDED.last_checkin,
                       last_scan        = EXCLUDED.last_scan,
                       monitor_count    = EXCLUDED.monitor_count,
                       perf_check_count = EXCLUDED.perf_check_count,
                       last_seen        = EXCLUDED.last_seen""",
            [(*row, now, now) for row in device_rows])

        execute_values(
            cur,
            f"""INSERT INTO {SCHEMA_NAME}.device_poll
                    (poll_id, device_id, polled_at, reporting, last_scan,
                     scan_age_mins, monitor_count, breach_count, error)
                VALUES %s
                ON CONFLICT (poll_id, device_id) DO NOTHING""",
            [(poll_id, s["device_id"], polled_at, s["reporting"], s["last_scan"],
              s["scan_age_mins"], s["monitor_count"], s["breach_count"], s["error"])
             for s in samples])

        if check_rows:
            execute_values(
                cur,
                f"""INSERT INTO {SCHEMA_NAME}.perf_check
                        (device_id, task_id, module, task_ident, service_id,
                         stale_after_mins, first_seen, last_seen)
                    VALUES %s
                    ON CONFLICT (device_id, task_id) DO UPDATE
                       SET module           = EXCLUDED.module,
                           task_ident       = coalesce(EXCLUDED.task_ident,
                                                       perf_check.task_ident),
                           service_id       = EXCLUDED.service_id,
                           stale_after_mins = EXCLUDED.stale_after_mins,
                           last_seen        = EXCLUDED.last_seen""",
                [(*row, now, now) for row in check_rows])

        if not observations:
            return 0

        cur.execute(
            f"""SELECT DISTINCT ON (device_id, task_id) device_id, task_id, state_class
                FROM {SCHEMA_NAME}.perf_event
                WHERE (device_id, task_id) IN %s
                ORDER BY device_id, task_id, transition_time DESC""",
            (tuple((o["device_id"], o["task_id"]) for o in observations),))
        previous = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        execute_values(
            cur,
            f"""INSERT INTO {SCHEMA_NAME}.perf_event
                    (device_id, task_id, transition_time, state, state_class,
                     in_active_issue, flap_suspected, first_seen_poll, recorded_at)
                VALUES %s
                ON CONFLICT (device_id, task_id, transition_time) DO NOTHING""",
            [(o["device_id"], o["task_id"], o["transition_time"], o["state"],
              o["state_class"], o["in_active_issue"],
              previous.get((o["device_id"], o["task_id"])) == o["state_class"],
              poll_id, now)
             for o in observations])
        return cur.rowcount


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _print_table(cur, title):
    rows = cur.fetchall()
    names = [d[0] for d in cur.description]
    print(f"\n=== {title} ===")
    if not rows:
        print("  (no rows)")
        return
    widths = [max(len(names[i]), *(len(str(r[i] if r[i] is not None else "-"))
                                   for r in rows)) for i in range(len(names))]
    print("  " + "  ".join(n.ljust(widths[i]) for i, n in enumerate(names)))
    print("  " + "  ".join("-" * widths[i] for i in range(len(names))))
    for row in rows:
        print("  " + "  ".join(str(row[i] if row[i] is not None else "-").ljust(widths[i])
                               for i in range(len(names))))


def status() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT device_name, status, scan_age_mins, monitor_count,
                                   breach_count, polled_at
                            FROM {SCHEMA_NAME}.v_device_current
                            ORDER BY reporting NULLS FIRST, breach_count DESC, device_name""")
            _print_table(cur, "devices right now")

            cur.execute(f"""SELECT device_name, module, instance, state, state_class,
                                   days_in_state
                            FROM {SCHEMA_NAME}.v_perf_current
                            WHERE state_class <> 'ok'
                            ORDER BY (state_class = 'breached') DESC,
                                     days_in_state DESC NULLS LAST""")
            _print_table(cur, "performance monitors not OK")

            cur.execute(f"SELECT * FROM {SCHEMA_NAME}.v_collector_health")
            _print_table(cur, "collector health")
    finally:
        conn.close()


def report_by_site() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT customer_name, site_name, devices,
                                   devices_with_downtime, uptime_pct, polls_down,
                                   trouble_hours, worst_breach_pct, breach_events
                            FROM {SCHEMA_NAME}.v_site_perf_30d
                            ORDER BY uptime_pct NULLS LAST, trouble_hours DESC""")
            _print_table(cur, "30 days — uptime and trouble by site")

            # Devices with no site_id group under a single NULL site row. Worth
            # naming, because that row is not an error: N-central attaches plenty
            # of devices straight to the customer with siteId null (13 of 22 sit
            # in a site on this estate, 9 do not). Only rows collected before
            # site_id existed are stale, and those clear on the next poll.
            cur.execute(f"""SELECT count(*) AS devices_without_a_site
                            FROM {SCHEMA_NAME}.device WHERE site_id IS NULL""")
            unattributed = cur.fetchone()[0]
            if unattributed:
                print(f"\n  note: {unattributed} device(s) have no site — they are "
                      "the blank site row above. The API reports siteId null for "
                      "devices attached directly to the customer, so this is "
                      "expected rather than missing data.")
    finally:
        conn.close()


def report() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT device_name, uptime_pct, polls, polls_down,
                                   trouble_hours, worst_breach_pct, breach_events
                            FROM {SCHEMA_NAME}.v_device_perf_30d
                            ORDER BY uptime_pct NULLS LAST, trouble_hours DESC""")
            _print_table(cur, "30 days — uptime and trouble by device")

            cur.execute(f"""SELECT device_name, module, instance, warning_hours,
                                   breach_hours, breach_pct, breach_events,
                                   longest_breach_hours, unmeasured_hours
                            FROM {SCHEMA_NAME}.v_perf_30d
                            WHERE warning_hours > 0 OR breach_hours > 0
                            ORDER BY (warning_hours + breach_hours) DESC""")
            _print_table(cur, "30 days — time in breach by monitor")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="N-central performance collector (hourly cron target: collect)")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init", help="Create/upgrade the ncentral schema")
    subs.add_parser("sync-customers", help="Fill ncentral.customers from the API")
    subs.add_parser("sync-sites", help="Fill ncentral.sites from the API")
    subs.add_parser("status", help="Current state")
    report_parser = subs.add_parser("report", help="30-day uptime and time in breach")
    report_parser.add_argument("--by-site", action="store_true",
                               help="Roll up per site instead of per device/monitor")
    collect_parser = subs.add_parser("collect", help="One poll — the cron target")
    collect_parser.add_argument("--dry-run", action="store_true",
                                help="Poll and print, write nothing (no DB needed)")
    collect_parser.add_argument("--quiet", action="store_true",
                                help="Only print on error — kinder to cron")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "init":
        init_schema()
        return 0
    if args.command == "sync-customers":
        sync_customers()
        return 0
    if args.command == "sync-sites":
        sync_sites()
        return 0
    if args.command == "status":
        status()
        return 0
    if args.command == "report":
        report_by_site() if args.by_site else report()
        return 0
    if args.command == "collect":
        result = collect(dry_run=args.dry_run, verbose=not args.quiet)
        if result.get("dry_run"):
            return 0
        if not args.quiet:
            print(f"\n{result['devices']} devices, {result['checks']} checks, "
                  f"{result['events']} new state changes")
        return 1 if result["errors"] else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
