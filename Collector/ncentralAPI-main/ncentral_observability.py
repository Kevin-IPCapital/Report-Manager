"""Safe, scheduled-task-friendly wrapper for existing N-central monitoring/metrics collectors.

Import-safe: no API or database access occurs unless run() is called.
Requires original ncentral_client.py, ncentral_performance.py and ncentral_metrics.py.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time
from datetime import datetime

import ncentral_client as nc
import ncentral_performance as monitoring
import ncentral_metrics as metrics

DEFAULT_LOG = Path(__file__).resolve().parent / "logs" / "ncentral_observability.log"
LOCK_KEYS = {"monitoring": 731002, "metrics": 731003}


def make_logger(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ncentral.observability")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    close_logger(logger)
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.flush()
        handler.close()


def _lock(conn, mode: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEYS[mode],))
        if not cur.fetchone()[0]:
            raise RuntimeError(f"Another {mode} collector is running")
    conn.commit()


def _unlock(conn, mode: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEYS[mode],))
    conn.commit()


def _customers(dry_run: bool, conn, customer_id: int | None):
    if dry_run:
        all_customers = [(int(c["orgUnitId"]), c.get("orgUnitName") or str(c["orgUnitId"]))
                         for c in nc.get_customers()]
    else:
        all_customers = [(int(row[0]), row[1]) for row in monitoring.active_org_units(conn)]
    result = [(id_, name) for id_, name in all_customers
              if customer_id is None or id_ == customer_id]
    if not result:
        raise RuntimeError("No customers matched; refusing an empty run")
    return result


def _start_run(conn, mode: str, org_id: int, started: datetime) -> int:
    table, col = ("poll_run", "poll_id") if mode == "monitoring" else ("metric_run", "run_id")
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO ncentral.{table} (started_at, org_unit_id) VALUES (%s, %s) RETURNING {col}",
                    (started, org_id))
        run_id = cur.fetchone()[0]
    conn.commit()  # durable run header before calls begin
    return run_id


def _finish_run(conn, mode: str, run_id: int, counts: dict, errors: list[str]) -> None:
    if mode == "monitoring":
        sql = ("UPDATE ncentral.poll_run SET finished_at=%s, device_count=%s, "
               "check_count=%s, event_count=%s, ok=%s, error=%s WHERE poll_id=%s")
        args = (datetime.now(), counts["devices"], counts["checks"], counts["events"],
                not errors, "; ".join(errors)[:4000] or None, run_id)
    else:
        sql = ("UPDATE ncentral.metric_run SET finished_at=%s, device_count=%s, "
               "sample_count=%s, new_count=%s, stale_count=%s, missing_count=%s, "
               "ok=%s, error=%s WHERE run_id=%s")
        args = (datetime.now(), counts["devices"], counts["samples"], counts["new"],
                counts["stale"], counts["missing"], not errors,
                "; ".join(errors)[:4000] or None, run_id)
    with conn.cursor() as cur:
        cur.execute(sql, args)
    conn.commit()


def _collect_customer(mode: str, org_id: int, name: str, at: datetime, conn, run_id):
    if mode == "monitoring":
        devices, samples, checks, observations, errors = monitoring._gather(org_id, name, at)
        counts = {"devices": len(devices), "checks": len(checks), "events": 0,
                  "unknown": sum(s["reporting"] is None for s in samples)}
        if conn is not None and devices:
            counts["events"] = monitoring._write(conn, run_id, at, devices, samples,
                                                   checks, observations)
    else:
        rows, stats, errors = metrics._gather(org_id, name, at)
        counts = {**stats, "new": 0}
        if conn is not None:
            counts["new"] = metrics._write(conn, run_id, at, rows)
    return counts, errors


def run(*, mode: str, dry_run: bool = True, customer_id: int | None = None,
        delay_seconds: float = 1.0, log_file: Path = DEFAULT_LOG,
        connect=None) -> dict:
    if mode not in LOCK_KEYS:
        raise ValueError("mode must be monitoring or metrics")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    log = make_logger(Path(log_file))
    start = time.monotonic()
    conn = None
    locked = False
    totals = {"customers": 0, "failed_customers": 0, "devices": 0,
              "checks": 0, "events": 0, "unknown": 0,
              "samples": 0, "new": 0, "stale": 0, "missing": 0}
    status = "FAILED"
    log.info("START mode=%s dry_run=%s customer_id=%s", mode, dry_run, customer_id)
    try:
        if not dry_run:
            conn = (connect or monitoring.connect)()
            _lock(conn, mode)
            locked = True
        customers = _customers(dry_run, conn, customer_id)
        for index, (org_id, name) in enumerate(customers):
            if index and delay_seconds:
                time.sleep(delay_seconds)
            now = datetime.now()
            run_id = None if dry_run else _start_run(conn, mode, org_id, now)
            try:
                counts, errors = _collect_customer(mode, org_id, name, now, conn, run_id)
                if conn is not None:
                    _finish_run(conn, mode, run_id, counts, errors)
                totals["customers"] += 1
                for key, value in counts.items():
                    totals[key] += value
                if errors:
                    totals["failed_customers"] += 1
                    log.warning("CUSTOMER_PARTIAL mode=%s customer_id=%s counts=%s errors=%s",
                                mode, org_id, counts, "; ".join(errors)[:1000])
                else:
                    log.info("CUSTOMER_SUCCESS mode=%s customer_id=%s counts=%s",
                             mode, org_id, counts)
            except Exception as exc:
                totals["failed_customers"] += 1
                if conn is not None:
                    conn.rollback()
                    try:
                        _finish_run(conn, mode, run_id, {k: 0 for k in totals}, [str(exc)])
                    except Exception:
                        conn.rollback()
                        log.exception("Could not persist failed run metadata customer_id=%s", org_id)
                log.exception("CUSTOMER_FAILED mode=%s customer_id=%s", mode, org_id)
                # Stop on all exceptions: conservatively avoids hammering a degraded source.
                raise
        status = "PARTIAL" if totals["failed_customers"] else "SUCCESS"
        if mode == "metrics" and totals["samples"] == 0:
            status = "NO_DATA" if status == "SUCCESS" else status
            log.warning("NO_METRICS_AVAILABLE: No usable AMP custom-property readings. "
                        "Check property configuration and Automation Manager deployment.")
    except Exception:
        status = "FAILED"
        raise
    finally:
        elapsed = time.monotonic() - start
        log.info("%s mode=%s dry_run=%s duration_seconds=%.3f totals=%s",
                 status, mode, dry_run, elapsed, totals)
        try:
            if conn is not None:
                try:
                    if locked:
                        _unlock(conn, mode)
                finally:
                    conn.close()
        finally:
            close_logger(log)
    return {**totals, "status": status, "dry_run": dry_run, "duration_seconds": elapsed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="N-central monitoring and measured-metrics collection")
    parser.add_argument("mode", choices=("monitoring", "metrics"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--customer-id", type=int)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    try:
        result = run(mode=args.mode, dry_run=args.dry_run,
                     customer_id=args.customer_id, delay_seconds=args.delay_seconds,
                     log_file=args.log_file)
    except Exception as exc:
        print(f"FAILED: {exc}. See {args.log_file}", file=sys.stderr)
        return 1
    print(f"{result['status']}: mode={args.mode} customers={result['customers']} "
          f"devices={result['devices']} checks={result['checks']} "
          f"events={result['events']} samples={result['samples']} "
          f"new={result['new']} missing={result['missing']} "
          f"duration={result['duration_seconds']:.2f}s dry_run={args.dry_run}")
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    sys.exit(main())
