"""
N-central patch management collector — Windows and third-party.

Pulls the patch inventory that ncentral_client.get_device_patch_status already
exposes and persists it, so you get compliance now *and* a backlog that ages.

Run it daily, not hourly: patching is an inventory, not a state machine, and each
device costs one ~200 KB asset fetch.

What lands in the database, per device per run:
  counts   installed, missing, and missing split into security / Windows /
           third-party / Defender definitions
  detail   every patch entry with its WSUS classification and KB number
  backlog  per (device, patch), when it was first seen missing — the only way to
           age a missing patch, since the API puts no date on a "Not Installed"
           entry
  context  Patch Status v2 / PME / Reboot Required monitor states

Source and its limits. There is no patch endpoint on the API — /api/patches,
/api/patch-status and /api/devices/{id}/patch-status all 404 — so this reads the
patch section of /api/devices/{id}/assets. Consequences, all recorded explicitly
rather than papered over:
  * Windows only. Linux devices return an empty patch section  -> 'no-inventory'
  * Some devices 404 on /assets entirely                       -> 'unavailable'
  * It is the device's own update inventory, not a delta against your approval
    policy, so "0 missing" means "nothing pending in its inventory", not
    "compliant with policy"
  * installeddate is blank on a fair number of installed entries, so
    days_since_last_install is a floor; undated_installed_count says how many
    were blank

Connection comes from NCENTRAL_PG_DSN in .env. Everything lives in the `ncentral`
schema and every statement is schema-qualified.

CLI:
  python ncentral_patch.py init             create/upgrade the patch tables
  python ncentral_patch.py sync-customers   fill ncentral.customers
  python ncentral_patch.py collect          one run — the cron target (daily)
  python ncentral_patch.py collect --dry-run  fetch and print, write nothing
  python ncentral_patch.py status           current compliance
  python ncentral_patch.py backlog          what is outstanding, oldest first
  python ncentral_patch.py report           by type, plus the trend
"""

import argparse
import logging
import time
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

import ncentral_client as nc

SCHEMA_NAME = "ncentral"
LOGGER = logging.getLogger("ncentral.patch")


def configure_file_logging(log_file=None):
    """Append collection events to a persistent log; create directories as needed."""
    destination = Path(log_file or os.getenv("NCENTRAL_PATCH_LOG_FILE") or
                       (Path(__file__).resolve().parent / "logs" / "ncentral_patch.log"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A handler only belongs to this command, not the entire Django process.
    # FileHandler's append mode creates the file if it does not yet exist.
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(destination, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    return destination


# Definition Updates are tracked but kept out of the security figure: Defender
# definitions turn over several times a day, so counting them makes every estate
# look permanently behind.
DEFINITION_CATEGORY = "Definition Updates"
THIRD_PARTY_CATEGORY = "Third-Party Software"

_KB_PATTERN = re.compile(r"\bKB(\d{6,7})\b", re.IGNORECASE)


def _kb(title: str):
    """KB number out of a patch title, when it has one."""
    match = _KB_PATTERN.search(title or "")
    return f"KB{match.group(1)}" if match else None


def _local(value):
    """Parse an API timestamp to naive local time, matching the schema."""
    parsed = nc._parse_api_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


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


def init_schema() -> None:
    # One schema file for both collectors — same file ncentral_performance.py
    # applies, so it does not matter which runs first.
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
                    (customer.get("orgUnitId"), customer.get("orgUnitName")))
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


def active_org_units(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(f"""SELECT org_unit_id, customer_name FROM {SCHEMA_NAME}.customers
                        WHERE is_active ORDER BY customer_name""")
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def _gather(org_unit_id, customer_name, captured_at, concurrency=1):
    """Fetch every device's patch position. Returns (device_rows, entry_rows, errors)."""
    devices = nc.get_devices_for_org_unit(org_unit_id)

    def load(device, _index):
        try:
            return {"device": device,
                    "patch": nc.get_device_patch_status(device["deviceId"]),
                    "error": None}
        except nc.NCentralError as error:
            # An inaccessible/missing device is a partial result, not a healthy
            # device. Rate limits, auth failures and server errors are different:
            # stop the collection instead of continuing to pressure the API.
            status = error.status
            if status in (401, 403, 429) or (status is not None and status >= 500):
                raise
            return {"device": device, "patch": None,
                    "error": str(error), "status": status}

    # Default to sequential requests: the assets endpoint can be expensive and
    # pool.map with five workers would keep issuing calls after a 429.
    results = nc.with_concurrency_limit(devices, load, limit=concurrency)

    device_rows, entry_rows, errors = [], [], []

    for result in results:
        device = result["device"]
        device_id = device.get("deviceId")
        patch = result["patch"]
        error = result["error"]

        if error:
            coverage = "unavailable"
            errors.append(f"device {device_id} ({device.get('longName')}): {error}")
        elif not patch or not patch["patch_entry_count"]:
            coverage = "no-inventory"
        else:
            coverage = "windows"

        missing = (patch or {}).get("missing") or []
        installed = (patch or {}).get("installed") or []

        def count(rows, predicate):
            return len([r for r in rows if predicate(r)])

        device_rows.append({
            "device_id": device_id,
            "device_name": device.get("longName"),
            "device_class": device.get("deviceClass"),
            "os": device.get("supportedOsLabel") or device.get("supportedOs"),
            "customer_name": customer_name,
            "site_name": device.get("siteName"),
            "coverage": coverage,
            "entry_count": (patch or {}).get("patch_entry_count", 0),
            "installed_count": len(installed),
            "missing_count": len(missing),
            "missing_security_count": count(missing, lambda r: r["is_security"]),
            # "Windows" = anything the vendor shipped through Windows Update, i.e.
            # everything that is not a third-party app version.
            "missing_windows_count": count(
                missing, lambda r: r["category"] != THIRD_PARTY_CATEGORY),
            "missing_thirdparty_count": count(
                missing, lambda r: r["category"] == THIRD_PARTY_CATEGORY),
            "missing_definition_count": count(
                missing, lambda r: r["category"] == DEFINITION_CATEGORY),
            "last_installed_at": (patch or {}).get("last_installed_at"),
            "days_since_last_install": (patch or {}).get("days_since_last_install"),
            "undated_installed_count": (patch or {}).get("undated_installed_count", 0),
            "patch_monitor_states": (patch or {}).get("patch_monitor_states"),
            "reboot_monitor_state": (patch or {}).get("reboot_monitor_state"),
            "error": error,
        })

        for row in missing + installed:
            title = row.get("title")
            if not title:
                continue    # title is the natural key; a nameless entry cannot be tracked
            entry_rows.append({
                "device_id": device_id,
                "device_name": device.get("longName"),
                "customer_name": customer_name,
                "title": title,
                "category": row.get("category"),
                "category_id": row.get("category_id"),
                "result": row.get("result"),
                "is_missing": row in missing,
                "is_security": bool(row.get("is_security")),
                "is_third_party": row.get("category") == THIRD_PARTY_CATEGORY,
                "installed_at": row.get("installed_at"),
                "kb": _kb(title),
            })

    return device_rows, entry_rows, errors



# Snapshot-to-snapshot change detection. Compare source-owned values, never
# run IDs, capture times or the age derived from current wall-clock time.
_DEVICE_FIELDS = (
    "device_name", "device_class", "os", "customer_name", "site_name",
    "coverage", "entry_count", "installed_count", "missing_count",
    "missing_security_count", "missing_windows_count",
    "missing_thirdparty_count", "missing_definition_count",
    "last_installed_at", "undated_installed_count", "patch_monitor_states",
    "reboot_monitor_state",
)
_ENTRY_FIELDS = (
    "category", "category_id", "result", "is_missing", "is_security",
    "is_third_party", "installed_at", "kb",
)


def _normalized(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _normalized(val)) for key, val in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normalized(x) for x in value)
    return value


def _signature(record, fields):
    return tuple(_normalized(record.get(field)) for field in fields)


def _previous_inventory(conn, device_ids, before_run_id=None):
    """Read last usable inventory for each device, not an arbitrary latest run.

    Unavailable devices in previous runs are skipped so a transient 404 does
    not make an otherwise unchanged device appear new on its next good run.
    """
    if not device_ids:
        return {}, {}
    params = [device_ids]
    run_clause = ""
    if before_run_id is not None:
        run_clause = "AND dp.run_id < %s"
        params.append(before_run_id)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT DISTINCT ON (dp.device_id) dp.*
            FROM {SCHEMA_NAME}.device_patch dp
            WHERE dp.device_id = ANY(%s) {run_clause}
              AND dp.coverage <> 'unavailable'
            ORDER BY dp.device_id, dp.run_id DESC
        """, params)
        names = [column[0] for column in cur.description]
        devices = {row["device_id"]: row for row in
                   (dict(zip(names, values)) for values in cur.fetchall())}
        if not devices:
            return devices, {}
        # Fetch only entries for the prior inventory selected for each device.
        # The tuple pairs prevent comparing against a different run's entries.
        pairs = [(record["run_id"], device_id)
                 for device_id, record in devices.items()]
        cur.execute(f"""
            SELECT pe.* FROM {SCHEMA_NAME}.patch_entry pe
            WHERE (pe.run_id, pe.device_id) IN %s
        """, (tuple(pairs),))
        names = [column[0] for column in cur.description]
        entries = {(row["device_id"], row["title"]): row for row in
                   (dict(zip(names, values)) for values in cur.fetchall())}
    return devices, entries


def _compare_inventory(device_rows, entry_rows, previous_devices, previous_entries):
    """Count source changes, excluding unavailable devices from comparisons.

    Device counts and patch-entry counts are separate. Entry removals matter
    even if a device's patch totals happen to stay the same.
    """
    counts = {"device_new": 0, "device_changed": 0, "device_unchanged": 0,
              "device_unavailable": 0, "entry_new": 0, "entry_changed": 0,
              "entry_unchanged": 0, "entry_removed": 0}
    entries_by_device = {}
    for entry in entry_rows:
        # The writer uses ON CONFLICT DO NOTHING for the same (device, title).
        # Match its first-wins behaviour for the comparison.
        entries_by_device.setdefault(entry["device_id"], {}).setdefault(entry["title"], entry)
    for device in device_rows:
        device_id = device["device_id"]
        if device["coverage"] == "unavailable":
            counts["device_unavailable"] += 1
            continue
        old_device = previous_devices.get(device_id)
        current_entries = entries_by_device.get(device_id, {})
        old_entries = {title: entry for (d_id, title), entry in previous_entries.items()
                       if d_id == device_id}
        if old_device is None:
            counts["device_new"] += 1
        else:
            device_changed = _signature(device, _DEVICE_FIELDS) != _signature(old_device, _DEVICE_FIELDS)
            # If patch detail changes with unchanged aggregate counts, the
            # device's inventory is still meaningfully changed.
            entry_changed = set(current_entries) != set(old_entries) or any(
                _signature(entry, _ENTRY_FIELDS) != _signature(old_entries[title], _ENTRY_FIELDS)
                for title, entry in current_entries.items() if title in old_entries)
            counts["device_changed" if device_changed or entry_changed else "device_unchanged"] += 1
        for title, entry in current_entries.items():
            prior = old_entries.get(title)
            if prior is None:
                counts["entry_new"] += 1
            elif _signature(entry, _ENTRY_FIELDS) != _signature(prior, _ENTRY_FIELDS):
                counts["entry_changed"] += 1
            else:
                counts["entry_unchanged"] += 1
        # Missing entries from a currently available device were removed from
        # its inventory. Do not infer source-side deletion for a 404 device.
        counts["entry_removed"] += len(old_entries.keys() - current_entries.keys())
    return counts


def _changes_text(counts):
    return (f"devices[new={counts['device_new']},changed={counts['device_changed']},"
            f"unchanged={counts['device_unchanged']},unavailable={counts['device_unavailable']}] "
            f"entries[new={counts['entry_new']},changed={counts['entry_changed']},"
            f"unchanged={counts['entry_unchanged']},removed={counts['entry_removed']}]")


def _add_changes(result, delta):
    for key, value in delta.items():
        result[key] = result.get(key, 0) + value


def _empty_changes():
    return {key: 0 for key in ("device_new", "device_changed", "device_unchanged",
                              "device_unavailable", "entry_new", "entry_changed",
                              "entry_unchanged", "entry_removed")}

# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def collect(dry_run: bool = False, verbose: bool = True,
            concurrency: int = 1) -> dict:
    if not 1 <= concurrency <= 5:
        raise ValueError("concurrency must be between 1 and 5")
    captured_at = datetime.now()

    if dry_run:
        customers = [(c.get("orgUnitId"), c.get("orgUnitName")) for c in nc.get_customers()]
        totals = {"devices": 0, "entries": 0, "missing": 0,
                  "unavailable": 0, "errors": [], "comparison_available": False,
                  **_empty_changes()}
        # Dry runs remain write-free. When the PG DSN is supplied, perform
        # read-only comparisons against the latest persisted device inventory.
        comparison_conn = connect() if os.getenv("NCENTRAL_PG_DSN") else None
        totals["comparison_available"] = comparison_conn is not None
        try:
            for org_unit_id, customer_name in customers:
                device_rows, entry_rows, errors = _gather(org_unit_id, customer_name, captured_at, concurrency)
                if comparison_conn is not None:
                    ids = [d["device_id"] for d in device_rows if d["coverage"] != "unavailable"]
                    prior_devices, prior_entries = _previous_inventory(comparison_conn, ids)
                    _add_changes(totals, _compare_inventory(
                        device_rows, entry_rows, prior_devices, prior_entries))
                missing = [e for e in entry_rows if e["is_missing"]]
                totals["devices"] += len(device_rows)
                totals["entries"] += len(entry_rows)
                totals["missing"] += len(missing)
                totals["unavailable"] += sum(d["coverage"] == "unavailable" for d in device_rows)
                totals["errors"].extend(errors)

                print(f"\n{customer_name} ({org_unit_id}): {len(device_rows)} devices, "
                      f"{len(entry_rows)} patch entries, {len(missing)} missing")
                print(f"  {'device':<26} {'coverage':<13} {'entries':>7} {'missing':>7} "
                      f"{'sec':>4} {'win':>4} {'3rd':>4} {'def':>4}  last install")
                for row in sorted(device_rows, key=lambda r: (r["coverage"],
                                                              -(r["missing_count"]),
                                                              r["device_name"] or "")):
                    last = ("-" if row["days_since_last_install"] is None
                            else f"{row['days_since_last_install']:.0f}d ago")
                    print(f"  {(row['device_name'] or '?')[:26]:<26} {row['coverage']:<13} "
                          f"{row['entry_count']:>7} {row['missing_count']:>7} "
                          f"{row['missing_security_count']:>4} {row['missing_windows_count']:>4} "
                          f"{row['missing_thirdparty_count']:>4} "
                          f"{row['missing_definition_count']:>4}  {last}")
                for entry in sorted(missing, key=lambda e: (not e["is_security"], e["title"])):
                    flag = "SEC" if entry["is_security"] else "   "
                    print(f"     {flag} {entry['category']:<22} {str(entry['kb'] or '-'):<10} "
                          f"{entry['device_name']}: {entry['title'][:60]}")
                for e in errors:
                    print(f"  ERROR {e}")

        finally:
            if comparison_conn is not None:
                comparison_conn.close()
        print(f"\ndry run — nothing written. {totals['devices']} devices, "
              f"{totals['entries']} entries, {totals['missing']} missing.")
        if totals["comparison_available"]:
            print("  change detection: " + _changes_text(totals))
        else:
            print("  change detection unavailable (set NCENTRAL_PG_DSN for read-only comparison)")
        return {"dry_run": True, **totals}

    conn = connect()
    result = {"devices": 0, "entries": 0, "missing": 0,
              "unavailable": 0, "errors": [], "comparison_available": True,
              **_empty_changes()}
    try:
        for org_unit_id, customer_name in active_org_units(conn):
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA_NAME}.patch_run (started_at, org_unit_id)
                        VALUES (%s, %s) RETURNING run_id""",
                    (captured_at, org_unit_id))
                run_id = cur.fetchone()[0]
            # Persist the run ID before collecting. Otherwise a rollback after
            # a failed write would also erase the run we wanted to mark failed.
            conn.commit()

            try:
                device_rows, entry_rows, errors = _gather(
                    org_unit_id, customer_name, captured_at, concurrency)
            except Exception as exc:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {SCHEMA_NAME}.patch_run
                            SET ok = FALSE, error = %s, finished_at = %s
                            WHERE run_id = %s""",
                        (str(exc), datetime.now(), run_id))
                conn.commit()
                result["errors"].append(f"{customer_name}: {exc}")
                if verbose:
                    print(f"  x  {customer_name}: {exc}")
                # A source-side 429 / 5xx / auth failure is systemic. Do not
                # continue making requests for subsequent customers.
                if isinstance(exc, nc.NCentralError) and (
                    exc.status in (401, 403, 429) or
                    (exc.status is not None and exc.status >= 500)
                ):
                    break
                continue

            try:
                ids = [d["device_id"] for d in device_rows if d["coverage"] != "unavailable"]
                prior_devices, prior_entries = _previous_inventory(conn, ids, before_run_id=run_id)
                changes = _compare_inventory(device_rows, entry_rows, prior_devices, prior_entries)
                _write(conn, run_id, captured_at, device_rows, entry_rows)
            except Exception as exc:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {SCHEMA_NAME}.patch_run
                            SET ok = FALSE, error = %s, finished_at = %s
                            WHERE run_id = %s""",
                        (f"Database write failed: {exc}", datetime.now(), run_id))
                conn.commit()
                result["errors"].append(f"{customer_name}: database write failed: {exc}")
                if verbose:
                    print(f"  x  {customer_name}: database write failed: {exc}")
                # DB failures may affect other customers; avoid cascading writes.
                break

            missing = [e for e in entry_rows if e["is_missing"]]
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {SCHEMA_NAME}.patch_run
                        SET finished_at = %s, device_count = %s,
                            patched_device_count = %s, entry_count = %s,
                            missing_count = %s, ok = %s, error = %s
                        WHERE run_id = %s""",
                    (datetime.now(), len(device_rows),
                     len([d for d in device_rows if d["coverage"] == "windows"]),
                     len(entry_rows), len(missing), not errors,
                     "; ".join(errors) or None, run_id))
            conn.commit()

            _add_changes(result, changes)
            result["unavailable"] += sum(
                d["coverage"] == "unavailable" for d in device_rows)
            result["devices"] += len(device_rows)
            result["entries"] += len(entry_rows)
            result["missing"] += len(missing)
            result["errors"].extend(errors)

            if verbose:
                print(f"  ok {customer_name:<26} {len(device_rows):>3} devices "
                      f"{len(entry_rows):>4} entries {len(missing):>3} missing "
                      f"({sum(d['missing_security_count'] for d in device_rows)} security)"
                     f" changes: {_changes_text(changes)}")
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
    time"), so every multi-row upsert has to be unique on its key before it is
    sent. Duplicates are real in this data, not hypothetical: the assets patch
    section can list one title twice for a device — the same KB reported under
    two categories — which is why patch_entry below can use DO NOTHING and
    patch_backlog cannot.
    """
    unique = {}
    for row in rows:
        unique[key(row)] = row
    return list(unique.values())


def _write(conn, run_id, captured_at, device_rows, entry_rows) -> None:
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""INSERT INTO {SCHEMA_NAME}.device_patch
                    (run_id, device_id, captured_at, device_name, device_class, os,
                     customer_name, site_name, coverage, entry_count, installed_count,
                     missing_count, missing_security_count, missing_windows_count,
                     missing_thirdparty_count, missing_definition_count,
                     last_installed_at, days_since_last_install,
                     undated_installed_count, patch_monitor_states,
                     reboot_monitor_state, error)
                VALUES %s
                ON CONFLICT (run_id, device_id) DO NOTHING""",
            [(run_id, r["device_id"], captured_at, r["device_name"], r["device_class"],
              r["os"], r["customer_name"], r["site_name"], r["coverage"],
              r["entry_count"], r["installed_count"], r["missing_count"],
              r["missing_security_count"], r["missing_windows_count"],
              r["missing_thirdparty_count"], r["missing_definition_count"],
              r["last_installed_at"], r["days_since_last_install"],
              r["undated_installed_count"],
              Json(r["patch_monitor_states"]) if r["patch_monitor_states"] else None,
              r["reboot_monitor_state"], r["error"])
             for r in device_rows])

        if entry_rows:
            execute_values(
                cur,
                f"""INSERT INTO {SCHEMA_NAME}.patch_entry
                        (run_id, device_id, title, category, category_id, result,
                         is_missing, is_security, is_third_party, installed_at, kb)
                    VALUES %s
                    ON CONFLICT (run_id, device_id, title) DO NOTHING""",
                [(run_id, e["device_id"], e["title"], e["category"], e["category_id"],
                  e["result"], e["is_missing"], e["is_security"], e["is_third_party"],
                  e["installed_at"], e["kb"])
                 for e in entry_rows])

        # Deduped on the conflict key: one row per (device, title) per run. That
        # is also the correct grain for times_seen_missing below, which counts
        # runs a patch was missing in — a title listed twice by one inventory is
        # still one run, so collapsing it here keeps the count honest as well as
        # keeping the statement legal.
        missing = _dedupe([e for e in entry_rows if e["is_missing"]],
                          lambda e: (e["device_id"], e["title"]))
        if missing:
            # first_missing_at is preserved on conflict — that is what makes the
            # age accrue instead of resetting on every run.
            execute_values(
                cur,
                f"""INSERT INTO {SCHEMA_NAME}.patch_backlog
                        (device_id, title, device_name, customer_name, category,
                         is_security, is_third_party, kb, first_missing_at,
                         last_missing_at, times_seen_missing, resolved_at)
                    VALUES %s
                    ON CONFLICT (device_id, title) DO UPDATE
                       SET last_missing_at    = EXCLUDED.last_missing_at,
                           times_seen_missing = patch_backlog.times_seen_missing + 1,
                           device_name        = EXCLUDED.device_name,
                           category           = EXCLUDED.category,
                           kb                 = EXCLUDED.kb,
                           resolved_at        = NULL""",
                [(e["device_id"], e["title"], e["device_name"], e["customer_name"],
                  e["category"], e["is_security"], e["is_third_party"], e["kb"],
                  captured_at, captured_at, 1, None)
                 for e in missing])

        # Anything previously missing that this run saw as installed is resolved.
        # Scoped to devices that actually returned an inventory, so a device that
        # 404'd does not silently clear its own backlog.
        installed_now = [(e["device_id"], e["title"]) for e in entry_rows
                         if not e["is_missing"]]
        if installed_now:
            cur.execute(
                f"""UPDATE {SCHEMA_NAME}.patch_backlog
                    SET resolved_at = %s
                    WHERE resolved_at IS NULL AND (device_id, title) IN %s""",
                (captured_at, tuple(installed_now)))


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
            cur.execute(f"SELECT * FROM {SCHEMA_NAME}.v_patch_summary")
            _print_table(cur, "compliance by customer")

            # Single view on purpose: joining v_patch_by_type to
            # v_device_patch_current would be ambiguous — both expose coverage,
            # reboot_monitor_state and days_since_last_install.
            cur.execute(f"""SELECT device_name, coverage, entry_count, total_missing,
                                   security_missing, windows_missing,
                                   thirdparty_missing, definition_missing,
                                   reboot_monitor_state, days_since_last_install
                            FROM {SCHEMA_NAME}.v_patch_by_type
                            ORDER BY security_missing DESC, device_name""")
            _print_table(cur, "by device")

            cur.execute(f"SELECT * FROM {SCHEMA_NAME}.v_patch_collector_health")
            _print_table(cur, "collector health")
    finally:
        conn.close()


def backlog(security_only: bool = False) -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT device_name, category, kb, days_outstanding,
                                   times_seen_missing, title
                            FROM {SCHEMA_NAME}.v_patch_backlog_open
                            {"WHERE is_security" if security_only else ""}""")
            _print_table(cur, "outstanding patches, security first then oldest")
    finally:
        conn.close()


def report() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {SCHEMA_NAME}.v_patch_by_type")
            _print_table(cur, "Windows vs third-party, by device")

            cur.execute(f"""SELECT * FROM {SCHEMA_NAME}.v_patch_trend LIMIT 30""")
            _print_table(cur, "backlog trend, most recent 30 days")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="N-central patch collector, Windows + third-party (daily cron target)")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init", help="Create/upgrade the patch tables")
    subs.add_parser("sync-customers", help="Fill ncentral.customers from the API")
    subs.add_parser("status", help="Current compliance")
    subs.add_parser("report", help="Windows vs third-party, plus the trend")

    backlog_parser = subs.add_parser("backlog", help="Outstanding patches, oldest first")
    backlog_parser.add_argument("--security-only", action="store_true",
                                help="Only Critical/Security classifications")

    collect_parser = subs.add_parser("collect", help="One run — the cron target")
    collect_parser.add_argument("--dry-run", action="store_true",
                                help="Fetch and print; compare read-only if NCENTRAL_PG_DSN is set")
    collect_parser.add_argument("--quiet", action="store_true",
                                help="Only print on error — kinder to cron")
    collect_parser.add_argument("--concurrency", type=int, default=1,
                                choices=range(1, 6), metavar="1..5",
                                help="Maximum parallel device requests (default: 1)")
    collect_parser.add_argument("--log-file", default=None,
                                help="Append logs to this path (default: logs/ncentral_patch.log)")
    return parser


def run_collection_with_logging(*, dry_run, quiet, concurrency, log_file=None):
    """Collect, recording success, partial completion, failure and duration."""
    configure_file_logging(log_file)
    # Windows prevents TemporaryDirectory cleanup while FileHandler is open.
    # Close the handler at the end of this command rather than retaining it
    # until a later invocation or process exit.
    owned_handlers = tuple(LOGGER.handlers)
    try:
        start = time.perf_counter()
        mode = "DRY_RUN" if dry_run else "LIVE"
        LOGGER.info("START mode=%s concurrency=%d", mode, concurrency)
        try:
            result = collect(dry_run=dry_run, verbose=not quiet,
                             concurrency=concurrency)
        except Exception:
            LOGGER.exception("FAILED mode=%s duration_seconds=%.3f", mode,
                             time.perf_counter() - start)
            raise
        duration = time.perf_counter() - start
        errors = result.get("errors") or []
        unavailable = result.get("unavailable", 0)
        outcome = "PARTIAL" if errors else "SUCCESS"
        LOGGER.log(logging.WARNING if errors else logging.INFO,
                   "%s mode=%s duration_seconds=%.3f devices=%d entries=%d "
                   "missing=%d unavailable=%d error_count=%d",
                   outcome, mode, duration, result.get("devices", 0),
                   result.get("entries", 0), result.get("missing", 0),
                   unavailable, len(errors))
        for error in errors:
            LOGGER.warning("COLLECTION_ERROR %s", error)
        if result.get("comparison_available"):
            LOGGER.info("CHANGE_SUMMARY mode=%s %s", mode, _changes_text(result))
        else:
            LOGGER.info("CHANGE_SUMMARY mode=%s unavailable=no_database_connection", mode)
        if dry_run:
            LOGGER.info("DRY_RUN database_writes=0")
        return result
    finally:
        for handler in owned_handlers:
            LOGGER.removeHandler(handler)
            handler.flush()
            handler.close()


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "init":
        init_schema()
        return 0
    if args.command == "sync-customers":
        sync_customers()
        return 0
    if args.command == "status":
        status()
        return 0
    if args.command == "backlog":
        backlog(security_only=args.security_only)
        return 0
    if args.command == "report":
        report()
        return 0
    if args.command == "collect":
        result = run_collection_with_logging(
            dry_run=args.dry_run, quiet=args.quiet,
            concurrency=args.concurrency, log_file=args.log_file)
        if result.get("dry_run"):
            return 1 if result.get("errors") else 0
        if not args.quiet:
            print(f"\n{result['devices']} devices, {result['entries']} entries, "
                  f"{result['missing']} missing, {result['unavailable']} unavailable")
        return 1 if result["errors"] else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
