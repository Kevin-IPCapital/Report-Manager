"""
N-central measured-metrics collector — CPU %, RAM %, disk free %.

Everything else in this project records monitor STATE, because that is all the
REST API exposes. Confirmed on 2026.3.1.10: every service-monitor row has
lastDataId and lastUpdate null, /api/devices/{id}/assets carries inventory only
(CPU model, core count, OS build), and the device resource advertises just
`assets` and `scheduled-tasks`. /api/devices/{id}/performance, /monitoring,
/tasks/{id} and /service-items/{id} all 404.

Custom properties, however, ARE exposed. So the numbers come the other way
round: an Automation Manager policy samples the agent and writes one JSON
reading into a device custom property (see amp_perf_sample.ps1), and this reads
that property back out and stores it.

    AMP (on the agent, your schedule)  ->  custom property  ->  this  ->  Postgres

WHAT THIS CANNOT DO. Resolution is the AMP schedule, not this collector's. A
custom property holds exactly one value — the last one written — so running this
more often than the AMP samples yields the same reading, not more data. Rows are
keyed on the AMP's own timestamp, so re-reading an unchanged property inserts
nothing and a stopped policy leaves a visible gap rather than a flat line.

Scheduled separately from the performance collector on purpose: sampling cadence
is yours to choose, and a slow metrics pass must not delay the hourly poll.

Connection comes from NCENTRAL_PG_DSN in .env. The property name defaults to
"IPC Perf Sample" and can be overridden with NCENTRAL_PERF_PROPERTY.

CLI:
  python ncentral_metrics.py properties        what properties exist, per device
  python ncentral_metrics.py collect           read the property, store readings
  python ncentral_metrics.py collect --dry-run read and print, write nothing
  python ncentral_metrics.py status            latest reading per device
  python ncentral_metrics.py report            7-day CPU/RAM pressure
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import Json, execute_values

import ncentral_client as nc

SCHEMA_NAME = "ncentral"

DEFAULT_PROPERTY_NAME = "IPC Perf Sample"

# A reading older than this is recorded but flagged. It is not dropped: knowing
# a device last reported six hours ago is the information, and silently
# discarding it would make a dead AMP look like a device that never existed.
STALE_AFTER_MINUTES = 180


def _property_name() -> str:
    return os.getenv("NCENTRAL_PERF_PROPERTY", DEFAULT_PROPERTY_NAME)


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


def active_org_units(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(f"""SELECT org_unit_id, customer_name FROM {SCHEMA_NAME}.customers
                        WHERE is_active ORDER BY customer_name""")
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_reading(raw: str):
    """
    The JSON amp_perf_sample.ps1 writes, or None if there is nothing usable.

    Returns None rather than raising for anything unparseable. An empty property
    is the normal state for a device the AMP has not run on yet, and one device
    with a mangled value must not abort the whole pass.
    """
    if not raw or not raw.strip():
        return None
    try:
        reading = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(reading, dict):
        return None

    sampled_at = _parse_ts(reading.get("ts"))
    if sampled_at is None:
        # Without the AMP's own timestamp there is no dedup key and no way to
        # tell a fresh reading from one written months ago, so it is unusable.
        return None

    return {
        "sampled_at": sampled_at,
        "cpu_pct": _number(reading.get("cpu_pct")),
        "memory_pct": _number(reading.get("mem_pct")),
        "memory_total_mb": _integer(reading.get("mem_total_mb")),
        "memory_used_mb": _integer(reading.get("mem_used_mb")),
        "disks": reading.get("disks") if isinstance(reading.get("disks"), dict) else None,
        "payload": reading,
    }


def _parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("T", " ")
    # Naive local throughout, matching the rest of the schema. A zone suffix is
    # dropped rather than converted, because the AMP writes local time and
    # reinterpreting it as UTC would shift every reading.
    for suffix in ("Z", "+00:00"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _number(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _integer(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def _gather(org_unit_id, customer_name, collected_at):
    """Read the metrics property off every device. Returns (rows, stats, errors)."""
    devices = nc.get_devices_for_org_unit(org_unit_id)
    wanted = _property_name().strip().lower()

    def load(device, _index):
        try:
            return {"device": device,
                    "properties": nc.get_device_custom_properties(device["deviceId"]),
                    "error": None}
        except nc.NCentralError as error:
            if error.status == 429 or (error.status is not None and error.status >= 500):
                raise  # Stop the collection on rate limits / upstream failure.
            return {"device": device, "properties": None, "error": str(error)}

    results = nc.with_concurrency_limit(devices, load, limit=1)

    rows, errors = [], []
    stats = {"devices": len(devices), "samples": 0, "stale": 0, "missing": 0}

    for result in results:
        device = result["device"]
        device_id = device.get("deviceId")

        if result["error"]:
            errors.append(f"device {device_id} ({device.get('longName')}): {result['error']}")
            stats["missing"] += 1
            continue

        raw = next((p.get("value") for p in (result["properties"] or [])
                    if (p.get("propertyName") or "").strip().lower() == wanted), None)
        reading = _parse_reading(raw)
        if reading is None:
            # No AMP run on this device yet, or a value we cannot read. Counted,
            # not warned about per device — on a fresh rollout that would be
            # every device.
            stats["missing"] += 1
            continue

        stats["samples"] += 1
        if reading["sampled_at"] < collected_at - timedelta(minutes=STALE_AFTER_MINUTES):
            stats["stale"] += 1

        rows.append({
            "device_id": device_id,
            "sampled_at": reading["sampled_at"],
            "device_name": device.get("longName"),
            "customer_name": customer_name,
            "site_id": device.get("siteId"),
            "site_name": device.get("siteName"),
            "cpu_pct": reading["cpu_pct"],
            "memory_pct": reading["memory_pct"],
            "memory_total_mb": reading["memory_total_mb"],
            "memory_used_mb": reading["memory_used_mb"],
            "disks": reading["disks"],
            "payload": reading["payload"],
        })

    return rows, stats, errors


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _write(conn, run_id, collected_at, rows) -> int:
    """Insert readings. Returns how many were new."""
    if not rows:
        return 0

    # Deduped on the primary key before sending: two devices cannot collide, but
    # a device returned twice in one pull would, and Postgres rejects a
    # multi-row upsert that proposes the same key twice.
    unique = {}
    for row in rows:
        unique[(row["device_id"], row["sampled_at"])] = row

    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""INSERT INTO {SCHEMA_NAME}.perf_sample
                    (device_id, sampled_at, collected_at, run_id, device_name,
                     customer_name, site_id, site_name, cpu_pct, memory_pct,
                     memory_total_mb, memory_used_mb, disks, payload)
                VALUES %s
                ON CONFLICT (device_id, sampled_at) DO NOTHING""",
            [(r["device_id"], r["sampled_at"], collected_at, run_id, r["device_name"],
              r["customer_name"], r["site_id"], r["site_name"], r["cpu_pct"],
              r["memory_pct"], r["memory_total_mb"], r["memory_used_mb"],
              Json(r["disks"]) if r["disks"] else None,
              Json(r["payload"]) if r["payload"] else None)
             for r in unique.values()])
        # DO NOTHING means rowcount is the number of genuinely new readings —
        # re-reading an unchanged property contributes nothing, which is the
        # point.
        return cur.rowcount


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def collect(dry_run: bool = False, verbose: bool = True) -> dict:
    collected_at = datetime.now()
    prop = _property_name()

    if dry_run:
        totals = {"devices": 0, "samples": 0, "stale": 0, "missing": 0}
        customers = [(c.get("orgUnitId"), c.get("orgUnitName")) for c in nc.get_customers()]
        for org_unit_id, customer_name in customers:
            rows, stats, errors = _gather(org_unit_id, customer_name, collected_at)
            for key in totals:
                totals[key] += stats[key]

            print(f"\n{customer_name} ({org_unit_id}): {stats['samples']} reading(s) "
                  f"from {stats['devices']} device(s), {stats['missing']} without one")
            print(f"  {'device':<26} {'sampled':<20} {'cpu%':>6} {'mem%':>6}  disks")
            for row in sorted(rows, key=lambda r: -(r["cpu_pct"] or 0)):
                disks = ", ".join(f"{k} {v}% free" for k, v in (row["disks"] or {}).items())
                print(f"  {(row['device_name'] or '?')[:26]:<26} "
                      f"{row['sampled_at']:%Y-%m-%d %H:%M:%S}  "
                      f"{_fmt(row['cpu_pct']):>6} {_fmt(row['memory_pct']):>6}  {disks}")
            for error in errors:
                print(f"  ERROR {error}")

        print(f"\ndry run — nothing written. property '{prop}': "
              f"{totals['samples']} reading(s), {totals['missing']} device(s) without one, "
              f"{totals['stale']} stale.")
        if not totals["samples"]:
            print("\n  No readings at all. Check the property name matches the one the AMP\n"
                  "  writes to, then run:  python ncentral_metrics.py properties")
        return {"dry_run": True, **totals}

    conn = connect()
    result = {"devices": 0, "samples": 0, "new": 0, "stale": 0, "missing": 0, "errors": []}
    try:
        for org_unit_id, customer_name in active_org_units(conn):
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.metric_run (started_at, org_unit_id)
                        VALUES (%s, %s) RETURNING run_id""",
                    (collected_at, org_unit_id))
                run_id = cur.fetchone()[0]

            try:
                rows, stats, errors = _gather(org_unit_id, customer_name, collected_at)
            except Exception as exc:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {SCHEMA_NAME}.metric_run
                            SET ok = FALSE, error = %s, finished_at = %s
                            WHERE run_id = %s""",
                        (str(exc), datetime.now(), run_id))
                conn.commit()
                result["errors"].append(f"{customer_name}: {exc}")
                if verbose:
                    print(f"  x  {customer_name}: {exc}")
                continue

            new_count = _write(conn, run_id, collected_at, rows)

            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {SCHEMA_NAME}.metric_run
                        SET finished_at = %s, device_count = %s, sample_count = %s,
                            new_count = %s, stale_count = %s, missing_count = %s,
                            ok = %s, error = %s
                        WHERE run_id = %s""",
                    (datetime.now(), stats["devices"], stats["samples"], new_count,
                     stats["stale"], stats["missing"], not errors,
                     "; ".join(errors) or None, run_id))
            conn.commit()

            result["devices"] += stats["devices"]
            result["samples"] += stats["samples"]
            result["new"] += new_count
            result["stale"] += stats["stale"]
            result["missing"] += stats["missing"]
            result["errors"].extend(errors)

            if verbose:
                print(f"  ok {customer_name:<26} {stats['samples']:>3} reading(s), "
                      f"{new_count:>3} new, {stats['missing']:>3} without one"
                      + (f", {stats['stale']} stale" if stats["stale"] else ""))
    finally:
        conn.close()

    if verbose:
        for error in result["errors"]:
            print(f"     warn: {error}")
    return result


def _fmt(value):
    return "-" if value is None else f"{value:.1f}"


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
            cur.execute(f"""SELECT device_name, customer_name, sampled_at,
                                   sample_age_mins, cpu_pct, memory_pct, disks
                            FROM {SCHEMA_NAME}.v_perf_sample_current
                            ORDER BY cpu_pct DESC NULLS LAST""")
            _print_table(cur, "latest reading per device")

            cur.execute(f"""SELECT device_name, volume, free_pct, sampled_at
                            FROM {SCHEMA_NAME}.v_disk_free_current
                            ORDER BY free_pct""")
            _print_table(cur, "free disk space per volume")

            cur.execute(f"SELECT * FROM {SCHEMA_NAME}.v_metric_collector_health")
            _print_table(cur, "metric collector health")
    finally:
        conn.close()


def report() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT device_name, customer_name, samples, cpu_avg, cpu_max,
                                   pct_time_cpu_over_90, memory_avg, memory_max,
                                   pct_time_memory_over_90
                            FROM {SCHEMA_NAME}.v_perf_pressure_7d""")
            _print_table(cur, "7 days — CPU and memory pressure")
    finally:
        conn.close()


def properties() -> None:
    """
    Every custom property on every device, so the metrics property can be found
    by name. The first thing to run when a collect returns no readings.
    """
    wanted = _property_name().strip().lower()
    print(f"looking for property: {_property_name()!r}\n")

    for customer in nc.get_customers():
        org_unit_id = customer.get("orgUnitId")
        devices = nc.get_devices_for_org_unit(org_unit_id)

        def load(device, _index):
            try:
                return device, nc.get_device_custom_properties(device["deviceId"])
            except nc.NCentralError as error:
                return device, error

        print(f"{customer.get('orgUnitName')} ({org_unit_id}): {len(devices)} device(s)")
        seen = {}
        for device, props in nc.with_concurrency_limit(devices, load):
            if isinstance(props, Exception):
                print(f"  ERROR {device.get('longName')}: {props}")
                continue
            for prop in props or []:
                name = prop.get("propertyName")
                entry = seen.setdefault(name, {"count": 0, "set": 0, "type": prop.get("propertyType")})
                entry["count"] += 1
                if (prop.get("value") or "").strip():
                    entry["set"] += 1

        for name in sorted(seen, key=str.lower):
            entry = seen[name]
            marker = " <-- MATCH" if (name or "").strip().lower() == wanted else ""
            print(f"  {entry['type'] or '?':<12} {name!r:<44} "
                  f"on {entry['count']:>3} device(s), {entry['set']:>3} with a value{marker}")

        if not any((n or "").strip().lower() == wanted for n in seen):
            print(f"\n  '{_property_name()}' not found. Create it as a TEXT device custom\n"
                  "  property in N-central, or point NCENTRAL_PERF_PROPERTY at an existing one.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="N-central measured metrics (CPU/RAM) via Automation Manager")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("properties", help="List custom properties, to find the metrics one")
    collect_parser = subs.add_parser("collect", help="Read readings and store them")
    collect_parser.add_argument("--dry-run", action="store_true",
                                help="Read and print, write nothing (no DB needed)")
    collect_parser.add_argument("--quiet", action="store_true",
                                help="Only print on error — kinder to cron")
    subs.add_parser("status", help="Latest reading per device")
    subs.add_parser("report", help="7-day CPU and memory pressure")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "properties":
        properties()
        return 0
    if args.command == "status":
        status()
        return 0
    if args.command == "report":
        report()
        return 0
    if args.command == "collect":
        result = collect(dry_run=args.dry_run, verbose=not args.quiet)
        if result.get("dry_run"):
            return 0
        if not args.quiet:
            print(f"\n{result['samples']} reading(s), {result['new']} new, "
                  f"{result['missing']} device(s) without one")
        return 1 if result["errors"] else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
