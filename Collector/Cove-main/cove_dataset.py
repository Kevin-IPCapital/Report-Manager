"""
Cove Data Protection — tabular dataset builder.

Turns the nested dicts produced by cove_agent into flat, database-shaped tables.
This is the layer between the API and PostgreSQL: it does no I/O of its own
beyond the device fetch, so it can also be used to feed CSV/Excel/pandas.

Entry points:
  fetch_dataset(customer=None)   Build all tables from live API data
  build_dataset(devices, ...)    Build all tables from already-fetched devices
  DataTable                      Minimal list-of-dicts table (.rows/.columns/.to_csv/.to_dataframe)

Tables produced (names match the PostgreSQL tables in sql/schema.sql):
  device              one row per device
  datasource_session  one row per device per backup datasource
  m365_source         one row per device per M365 source (Exchange/OneDrive/...)
  red_flag            one row per red flag

Every table carries device_id; the loader stamps snapshot_id at insert time.
"""

from datetime import datetime, timezone

from cove_agent import (
    _fetch_devices,
    _get_locations,
    M365_STANDARD,
)

# ---------------------------------------------------------------------------
# Column order per table — also the INSERT column order used by cove_db
# ---------------------------------------------------------------------------

DEVICE_COLUMNS = [
    "device_id", "device_name", "device_alias", "customer", "contact_email", "hostname",
    "os", "os_type", "account_type", "client_version", "client_old", "os_eol",
    "is_server", "is_m365",
    "used_storage_bytes", "dedup_efficiency", "storage_location", "strict_location_policy",
    "reporting_enabled", "reporting_frequency", "dr_testing",
    "immutable_enabled", "fips_enabled", "passphrase_set",
    "lsv_enabled", "lsv_status", "cloud_status", "retention_days",
    "last_success", "days_since_success",
    "health", "red_flag_count", "active_datasources",
]

DATASOURCE_COLUMNS = [
    "device_id", "ds_code", "ds_name", "customer", "device_name",
    "status", "status_code", "error_count", "duration_seconds",
    "sent_bytes", "protected_bytes", "item_count", "retention_days",
    "last_success", "last_session",
    "colorbar_raw", "ok_days", "failed_days", "warning_days", "missed_days",
    "max_fail_streak", "recurring_failures", "chronic_warnings", "chronic_missed",
    "imm_active", "imm_last_status", "imm_last_status_code", "imm_last_at",
]

M365_COLUMNS = [
    "device_id", "source_label", "ds_code", "customer", "device_name",
    "enabled", "coverage", "status_code", "last_success",
    "item_count", "user_count", "ok_days", "failed_days", "missed_days",
]

RED_FLAG_COLUMNS = ["device_id", "customer", "device_name", "category", "flag"]

TABLE_COLUMNS = {
    "device":             DEVICE_COLUMNS,
    "datasource_session": DATASOURCE_COLUMNS,
    "m365_source":        M365_COLUMNS,
    "red_flag":           RED_FLAG_COLUMNS,
}

# Red-flag text → category bucket. First matching substring wins.
FLAG_CATEGORIES = [
    ("NO SUCCESSFUL BACKUP",  "backup_currency"),
    ("NEVER HAD",             "backup_currency"),
    ("RECURRING FAILURES",    "chronic_failure"),
    ("CHRONIC",               "chronic_failure"),
    ("NOT BACKING UP",        "chronic_failure"),
    ("M365",                  "m365_coverage"),
    ("NoSelection",           "m365_coverage"),
    ("DR TESTING",            "dr_readiness"),
    ("REPORTING",             "reporting"),
    ("PASSPHRASE",            "security"),
    ("IMMUTABLE",             "security"),
    ("END-OF-LIFE OS",        "lifecycle"),
    ("OUTDATED CLIENT",       "lifecycle"),
    ("RETENTION",             "retention"),
    ("LSV",                   "storage_health"),
    ("CLOUD STORAGE",         "storage_health"),
]


def _categorise(flag: str) -> str:
    for needle, category in FLAG_CATEGORIES:
        if needle.lower() in flag.lower():
            return category
    return "session_failure"


# ---------------------------------------------------------------------------
# DataTable
# ---------------------------------------------------------------------------

class DataTable:
    """A named table of uniform rows. Dependency-free; pandas is optional."""

    def __init__(self, name: str, columns: list, rows: list = None):
        self.name    = name
        self.columns = list(columns)
        self.rows    = list(rows or [])

    def append(self, row: dict) -> None:
        self.rows.append({c: row.get(c) for c in self.columns})

    def tuples(self) -> list:
        """Rows as tuples in `columns` order — ready for executemany/copy."""
        return [tuple(r.get(c) for c in self.columns) for r in self.rows]

    def to_dataframe(self):
        import pandas as pd  # optional dependency
        return pd.DataFrame(self.rows, columns=self.columns)

    def to_csv(self, path: str) -> str:
        import csv
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self.columns)
            w.writeheader()
            w.writerows(self.rows)
        return path

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __repr__(self):
        return f"<DataTable {self.name}: {len(self.rows)} rows x {len(self.columns)} cols>"


class Dataset:
    """The four tables plus the metadata describing how they were captured."""

    def __init__(self, captured_at, customer_filter=None):
        self.captured_at     = captured_at
        self.customer_filter = customer_filter
        self.tables          = {
            name: DataTable(name, cols) for name, cols in TABLE_COLUMNS.items()
        }

    def __getitem__(self, name) -> DataTable:
        return self.tables[name]

    @property
    def device(self):             return self.tables["device"]
    @property
    def datasource_session(self): return self.tables["datasource_session"]
    @property
    def m365_source(self):        return self.tables["m365_source"]
    @property
    def red_flag(self):           return self.tables["red_flag"]

    def to_dataframes(self) -> dict:
        return {name: t.to_dataframe() for name, t in self.tables.items()}

    def to_csv_dir(self, directory: str) -> list:
        import os
        os.makedirs(directory, exist_ok=True)
        return [t.to_csv(os.path.join(directory, f"{name}.csv"))
                for name, t in self.tables.items()]

    def summary(self) -> dict:
        return {
            "captured_at":     self.captured_at.isoformat(),
            "customer_filter": self.customer_filter,
            "row_counts":      {n: len(t) for n, t in self.tables.items()},
        }

    def __repr__(self):
        counts = ", ".join(f"{n}={len(t)}" for n, t in self.tables.items())
        return f"<Dataset {self.captured_at:%Y-%m-%d %H:%M} {counts}>"


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _dt(epoch):
    """Epoch seconds → aware datetime, or None. DB columns are TIMESTAMPTZ."""
    if epoch in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _num(val):
    """Loose numeric → int, or None. The API returns numbers as strings."""
    if val in (None, "", "-"):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_dataset(devices: list, customer_filter: str = None,
                  locations: dict = None, captured_at=None) -> Dataset:
    """Flatten parsed device dicts (from cove_agent) into a Dataset."""
    locations = locations if locations is not None else {}
    ds = Dataset(captured_at or datetime.now(tz=timezone.utc), customer_filter)

    for d in devices:
        device_id   = _num(d.get("device_id"))
        if device_id is None:
            continue                      # cannot key a row without the account id
        customer    = d.get("customer")
        device_name = d.get("device_name")
        loc_info    = locations.get(d.get("location_id"), {})

        ds.device.append({
            "device_id":              device_id,
            "device_name":            device_name,
            "device_alias":           d.get("device_alias"),
            "customer":               customer,
            "contact_email":          d.get("contact_email"),
            "hostname":               d.get("hostname"),
            "os":                     d.get("os"),
            "os_type":                d.get("os_type"),
            "account_type":           d.get("account_type"),
            "client_version":         d.get("client_version"),
            "client_old":             bool(d.get("client_old")),
            "os_eol":                 d.get("os_eol") or None,
            "is_server":              bool(d.get("is_server")),
            "is_m365":                bool(d.get("is_m365")),
            "used_storage_bytes":     _num(d.get("used_storage_bytes")),
            "dedup_efficiency":       _num(d.get("dedup_efficiency")),
            "storage_location":       loc_info.get("name") or d.get("location_id") or None,
            "strict_location_policy": bool(loc_info.get("strict_policy")) if loc_info else None,
            "reporting_enabled":      bool(d.get("reporting_enabled")),
            "reporting_frequency":    _num(d.get("reporting_raw")),
            "dr_testing":             d.get("dr_testing"),
            "immutable_enabled":      bool(d.get("immutable_enabled")),
            "fips_enabled":           bool(d.get("fips_enabled")),
            "passphrase_set":         bool(d.get("passphrase_set")),
            "lsv_enabled":            bool(d.get("lsv_enabled")),
            "lsv_status":             d.get("lsv_status"),
            "cloud_status":           d.get("cloud_status"),
            "retention_days":         _num(d.get("retention_days")),
            "last_success":           _dt(d.get("last_success_ts")),
            "days_since_success":     _num(d.get("days_since_success")),
            "health":                 d.get("health") or "OK",
            "red_flag_count":         len(d.get("red_flags") or []),
            "active_datasources":     ", ".join(
                src["name"] for src in (d.get("active_datasources") or [])
            ) or None,
        })

        for code, sess in (d.get("datasource_sessions") or {}).items():
            cb = sess.get("colorbar") or {}
            ds.datasource_session.append({
                "device_id":            device_id,
                "ds_code":              code,
                "ds_name":              sess.get("name"),
                "customer":             customer,
                "device_name":          device_name,
                "status":               sess.get("status"),
                "status_code":          sess.get("status_code"),
                "error_count":          _num(sess.get("error_count")),
                "duration_seconds":     _num(sess.get("duration_seconds")),
                "sent_bytes":           _num(sess.get("sent_bytes")),
                "protected_bytes":      _num(sess.get("protected_bytes")),
                "item_count":           _num(sess.get("item_count")),
                "retention_days":       _num(sess.get("retention_days")),
                "last_success":         _dt(sess.get("last_success_ts")),
                "last_session":         _dt(sess.get("last_session_ts")),
                "colorbar_raw":         cb.get("raw"),
                "ok_days":              cb.get("ok_days"),
                "failed_days":          cb.get("failed_days"),
                "warning_days":         cb.get("warning_days"),
                "missed_days":          cb.get("missed_days"),
                "max_fail_streak":      cb.get("max_fail_streak"),
                "recurring_failures":   bool(cb.get("recurring_failures")),
                "chronic_warnings":     bool(cb.get("chronic_warnings")),
                "chronic_missed":       bool(cb.get("chronic_missed")),
                "imm_active":           bool(sess.get("imm_active")),
                "imm_last_status":      sess.get("imm_last_status"),
                "imm_last_status_code": sess.get("imm_last_status_code"),
                "imm_last_at":          _dt(sess.get("imm_last_ts_epoch")),
            })

        for label, info in (d.get("m365_coverage") or {}).items():
            cb = info.get("colorbar") or {}
            ds.m365_source.append({
                "device_id":    device_id,
                "source_label": label,
                "ds_code":      info.get("code") or M365_STANDARD.get(label),
                "customer":     customer,
                "device_name":  device_name,
                "enabled":      bool(info.get("enabled")),
                "coverage":     info.get("coverage"),
                "status_code":  info.get("status_code"),
                "last_success": _dt(info.get("last_success_ts")),
                "item_count":   _num(info.get("item_count")),
                "user_count":   _num(info.get("user_count")),
                "ok_days":      cb.get("ok_days"),
                "failed_days":  cb.get("failed_days"),
                "missed_days":  cb.get("missed_days"),
            })

        for flag in (d.get("red_flags") or []):
            ds.red_flag.append({
                "device_id":   device_id,
                "customer":    customer,
                "device_name": device_name,
                "category":    _categorise(flag),
                "flag":        flag,
            })

    return ds


def fetch_dataset(customer: str = None, resolve_locations: bool = True,
                  use_cache: bool = True) -> Dataset:
    """
    Fetch devices from the Cove API and return them as flat tables.

    customer          partial, case-insensitive customer name; None = whole estate
    resolve_locations one extra API call to turn location ids into names
    use_cache         reuse cove_agent's 5-minute device cache
    """
    devices = _fetch_devices(use_cache=use_cache)
    if customer:
        needle  = customer.lower()
        devices = [d for d in devices
                   if d.get("customer") and needle in d["customer"].lower()]

    locations = _get_locations() if resolve_locations else {}
    return build_dataset(devices, customer_filter=customer, locations=locations)


if __name__ == "__main__":
    import sys
    ds = fetch_dataset(" ".join(sys.argv[1:]) or None)
    print(ds)
    for name, table in ds.tables.items():
        print(f"  {name:<20} {len(table):>6} rows")
