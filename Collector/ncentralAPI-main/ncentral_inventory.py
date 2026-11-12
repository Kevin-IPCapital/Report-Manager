"""Low-impact N-central device inventory collector.

Only GET /api/org-units and GET /api/org-units/{orgUnitId}/devices are used.
Does not call monitor or asset endpoints. Does not initialize or rebuild schema.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import ncentral_client as nc

DEFAULT_LOG = Path(__file__).resolve().parent / "logs" / "ncentral_inventory.log"


def make_logger(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ncentral.inventory")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Explicitly close old handlers, important for repeated runs/tests on Windows.
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


def normalize_device(device: dict[str, Any], org_id: int, customer: str) -> tuple:
    if device.get("deviceId") is None:
        raise ValueError("Device response lacks deviceId")
    return (
        int(device["deviceId"]), device.get("longName"), device.get("deviceClass"),
        device.get("supportedOsLabel") or device.get("supportedOs"),
        device.get("siteId"), device.get("siteName"), customer, org_id,
        device.get("uri"), parse_local(device.get("lastApplianceCheckinTime")),
    )


def parse_local(value):
    parsed = nc._parse_api_datetime(value)
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def persist(conn, org_id: int, customer: str, rows: list[tuple]) -> None:
    """One transaction per customer. No deletes or deactivations."""
    from psycopg2.extras import execute_values

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ncentral.customers (org_unit_id, customer_name, is_active) "
                "VALUES (%s, %s, TRUE) ON CONFLICT (org_unit_id) DO UPDATE "
                "SET customer_name = EXCLUDED.customer_name",
                (org_id, customer),
            )
            if rows:
                # Can be listed twice within one customer; last response wins.
                unique_rows = list({row[0]: row for row in rows}.values())
                execute_values(cur, """
                    INSERT INTO ncentral.device (
                        device_id, device_name, device_class, os, site_id, site_name,
                        customer_name, org_unit_id, device_uri, last_checkin,
                        first_seen, last_seen
                    ) VALUES %s
                    ON CONFLICT (device_id) DO UPDATE SET
                        device_name = EXCLUDED.device_name,
                        device_class = EXCLUDED.device_class,
                        os = EXCLUDED.os,
                        site_id = EXCLUDED.site_id,
                        site_name = EXCLUDED.site_name,
                        customer_name = EXCLUDED.customer_name,
                        org_unit_id = EXCLUDED.org_unit_id,
                        device_uri = EXCLUDED.device_uri,
                        last_checkin = EXCLUDED.last_checkin,
                        last_seen = EXCLUDED.last_seen
                """, [(*row, datetime.now(), datetime.now()) for row in unique_rows], page_size=100)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def run(*, dry_run: bool, log_file: Path = DEFAULT_LOG, customer_id: int | None = None,
        delay_seconds: float = 1.0, api=None, connect=None) -> dict:
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be >= 0")
    api = api or nc
    logger = make_logger(Path(log_file))
    start = time.monotonic()
    counts = {"customers": 0, "devices": 0, "written": 0, "failed_customers": 0}
    conn = None
    status = "SUCCESS"
    logger.info("START mode=%s customer_id=%s", "DRY_RUN" if dry_run else "LIVE", customer_id)
    try:
        all_customers = api.get_customers()
        customers = [c for c in all_customers if customer_id is None or int(c["orgUnitId"]) == customer_id]
        if customer_id is not None and not customers:
            raise ValueError(f"Customer orgUnitId {customer_id} not returned by API")
        # Do not allow an empty unfiltered response to silently signal a healthy run.
        if not customers:
            raise RuntimeError("No customer records returned by API; refusing empty sync")
        if not dry_run:
            if connect is None:
                import psycopg2
                dsn = os.environ.get("NCENTRAL_PG_DSN")
                if not dsn:
                    raise RuntimeError("NCENTRAL_PG_DSN is required for live collection")
                connect = lambda: psycopg2.connect(dsn)
            conn = connect()
        for i, customer in enumerate(customers):
            if i and delay_seconds:
                time.sleep(delay_seconds)
            org_id = int(customer["orgUnitId"])
            name = customer.get("orgUnitName") or f"Customer {org_id}"
            try:
                devices = api.get_devices_for_org_unit(org_id)
                if not isinstance(devices, list):
                    raise ValueError("Device endpoint did not return a list")
                rows = [normalize_device(d, org_id, name) for d in devices]
                if conn is not None:
                    persist(conn, org_id, name, rows)
                    counts["written"] += len({r[0] for r in rows})
                counts["customers"] += 1
                counts["devices"] += len(rows)
                logger.info("CUSTOMER_SUCCESS org_unit_id=%s devices=%s mode=%s", org_id, len(rows),
                            "DRY_RUN" if dry_run else "LIVE")
            except Exception as exc:
                counts["failed_customers"] += 1
                logger.error("CUSTOMER_FAILED org_unit_id=%s error=%s", org_id, exc)
                # Do not continue after any systemic issue, including HTTP 429 / 5xx.
                # A one-customer failure can be retried with --customer-id.
                raise
    except Exception as exc:
        status = "FAILED"
        logger.exception("COLLECTION_FAILED error=%s", exc)
        raise
    finally:
        duration = time.monotonic() - start
        logger.info("%s mode=%s duration_seconds=%.3f customers=%s devices=%s "
                    "rows_written=%s failed_customers=%s", status,
                    "DRY_RUN" if dry_run else "LIVE", duration, counts["customers"],
                    counts["devices"], counts["written"], counts["failed_customers"])
        if conn is not None:
            conn.close()
        close_logger(logger)
    return {**counts, "dry_run": dry_run, "duration_seconds": duration, "status": status}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lightweight N-central inventory synchronization")
    parser.add_argument("--dry-run", action="store_true", help="Fetch API data without DB writes")
    parser.add_argument("--customer-id", type=int, help="Limit to one N-central customer")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--delay-seconds", type=float, default=1.0,
                        help="Pause between customer requests (default 1 second)")
    args = parser.parse_args(argv)
    try:
        result = run(dry_run=args.dry_run, log_file=args.log_file,
                     customer_id=args.customer_id, delay_seconds=args.delay_seconds)
    except Exception as exc:
        print(f"Collection failed: {exc}. See {args.log_file}", file=sys.stderr)
        return 1
    print(f"{result['status']}: customers={result['customers']} devices={result['devices']} "
          f"rows_written={result['written']} elapsed={result['duration_seconds']:.2f}s "
          f"dry_run={result['dry_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
