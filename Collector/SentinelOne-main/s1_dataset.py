"""
SentinelOne — tabular dataset builder.

Turns raw API records into flat, database-shaped tables. This is the layer
between the API and PostgreSQL: apart from the fetch in fetch_dataset() it does
no I/O, so it can equally feed CSV/Excel/pandas.

Entry points:
  fetch_dataset(month, customer=None)  Build all tables from live API data
  build_dataset(month, threats, agents, ...)  Build from already-fetched records
  DataTable                            Minimal table (.rows/.columns/.to_csv/.to_dataframe)

Tables produced (names match the PostgreSQL tables in sql/schema.sql):
  customer_month   one row per customer — the scorecard
  threat           one row per threat
  agent            one row per endpoint
  threat_class     classification breakdown
  mitre_tactic     MITRE ATT&CK tactics observed
  top_threat       ranked threat names
  top_device       ranked affected devices
  top_user         ranked process users
  lolbin           living-off-the-land detections
  daily_trend      detections per calendar day
  os_spread        endpoints per OS
  agent_version    endpoints per agent version

Every table carries customer_name; the loader stamps snapshot_id at insert time.
customer_month must be inserted first — the others reference it.
"""

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import s1_metrics as M
from s1_client import S1Client, get

# ---------------------------------------------------------------------------
# Column order per table — also the INSERT column order used by s1_db
# ---------------------------------------------------------------------------

CUSTOMER_MONTH_COLUMNS = [
    "customer_name", "site_id", "report_month",
    "threats", "mitigated", "not_mitigated", "marked_benign",
    "malicious", "suspicious", "mitigation_rate",
    "ransomware", "infostealer", "backdoor", "pua",
    "lateral_movement", "lolbin_detections",
    "behavioral_ai", "static_ai",
    "avg_mttr_minutes",
    "devices", "infected", "outdated_agents", "stale_agents", "pending_uninstall",
    "detect_only", "protect_mode", "health_issues", "scan_aborted",
    "risk_score", "risk_level", "report_path",
]

THREAT_COLUMNS = [
    "threat_id", "customer_name", "site_id",
    "threat_name", "classification", "confidence_level", "mitigation_status",
    "detection_type", "analyst_verdict", "incident_status",
    "created_at", "updated_at", "mttr_minutes", "detected_on",
    "process_user", "file_path", "sha1", "storyline",
    "device_name", "agent_id", "agent_os",
    "is_lolbin", "is_lateral_movement", "mitre_tactics",
]

AGENT_COLUMNS = [
    "agent_id", "customer_name", "site_id",
    "computer_name", "domain", "machine_type", "group_name",
    "os_name", "os_type", "agent_version", "is_up_to_date",
    "mitigation_mode", "infected", "network_status", "scan_status",
    "is_pending_uninstall", "is_active", "is_decommissioned",
    "last_active", "days_since_active", "is_stale",
    "user_actions_needed", "health_issue_count",
]

THREAT_CLASS_COLUMNS  = ["customer_name", "classification", "threat_count"]
MITRE_TACTIC_COLUMNS  = ["customer_name", "tactic", "threat_count"]
TOP_THREAT_COLUMNS    = ["customer_name", "threat_name", "detections", "rank"]
TOP_DEVICE_COLUMNS    = ["customer_name", "device_name", "detections", "rank"]
TOP_USER_COLUMNS      = ["customer_name", "process_user", "detections", "rank"]
LOLBIN_COLUMNS        = ["customer_name", "threat_name", "detections", "rank"]
DAILY_TREND_COLUMNS   = ["customer_name", "trend_date", "threat_count"]
OS_SPREAD_COLUMNS     = ["customer_name", "os_name", "device_count"]
AGENT_VERSION_COLUMNS = ["customer_name", "agent_version", "device_count"]

TABLE_COLUMNS = {
    "customer_month": CUSTOMER_MONTH_COLUMNS,
    "threat":         THREAT_COLUMNS,
    "agent":          AGENT_COLUMNS,
    "threat_class":   THREAT_CLASS_COLUMNS,
    "mitre_tactic":   MITRE_TACTIC_COLUMNS,
    "top_threat":     TOP_THREAT_COLUMNS,
    "top_device":     TOP_DEVICE_COLUMNS,
    "top_user":       TOP_USER_COLUMNS,
    "lolbin":         LOLBIN_COLUMNS,
    "daily_trend":    DAILY_TREND_COLUMNS,
    "os_spread":      OS_SPREAD_COLUMNS,
    "agent_version":  AGENT_VERSION_COLUMNS,
}

# Insert order. customer_month first — every other table has a foreign key to it.
INSERT_ORDER = [
    "customer_month", "threat", "agent",
    "threat_class", "mitre_tactic", "top_threat", "top_device", "top_user",
    "lolbin", "daily_trend", "os_spread", "agent_version",
]


# ---------------------------------------------------------------------------
# Month helpers
# ---------------------------------------------------------------------------

def add_months(dt, months):
    """Add (or subtract) whole months, snapping to the first of the month."""
    index = dt.month - 1 + months
    return dt.replace(year=dt.year + index // 12, month=index % 12 + 1, day=1)


def current_month() -> str:
    """'YYYY-MM' for today — the default reporting period."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


class MonthWindow(NamedTuple):
    """A resolved reporting window. `end` never runs past the end of today."""
    start:       datetime
    end:         datetime
    month_end:   datetime      # last second of the calendar month
    start_iso:   str           # API-shaped, for createdAt__gte
    end_iso:     str           # API-shaped, for createdAt__lte
    label:       str           # 'July 2026'
    is_partial:  bool          # True when the month is still in progress
    through_day: int           # last calendar day covered
    days_covered: int

    @property
    def span_label(self) -> str:
        """'July 2026' when complete, 'July 2026 (1-27, month to date)' when not."""
        if not self.is_partial:
            return self.label
        return f"{self.label} (1-{self.through_day}, month to date)"


def month_window(month: str, as_of=None, through_day: int = None) -> MonthWindow:
    """
    Resolve 'YYYY-MM' into a query window.

    The end is the last second of the month for a month that has finished, but
    the end of *today* for the month in progress — a run on the 27th should not
    claim to cover 31 days. The API's __lte is inclusive, hence 23:59:59.

    through_day forces the window to stop at that day of the month. That is how
    a partial current month is compared against the same span of earlier months:
    27 days of July against 27 days of June, not against all of June.
    """
    first = datetime.strptime(f"{month}-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    month_end = add_months(first, 1) - timedelta(seconds=1)

    if through_day is not None:
        # Clamp to the month's length — asking for the 31st of February gives the 28th.
        day = min(through_day, month_end.day)
        end = first.replace(day=day, hour=23, minute=59, second=59)
    else:
        as_of = as_of or datetime.now(timezone.utc)
        end = month_end
        if as_of < month_end:
            end = as_of.replace(hour=23, minute=59, second=59, microsecond=0)

    # A month that has not started yet collapses to an empty window rather than
    # producing an end before its start.
    end = min(max(end, first), month_end)

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return MonthWindow(
        start=first,
        end=end,
        month_end=month_end,
        start_iso=first.strftime(fmt),
        end_iso=end.strftime(fmt),
        label=first.strftime("%B %Y"),
        is_partial=end < month_end,
        through_day=end.day,
        days_covered=end.day,
    )


# ---------------------------------------------------------------------------
# DataTable / Dataset
# ---------------------------------------------------------------------------

class DataTable:
    """A named table of uniform rows. Dependency-free; pandas is optional."""

    def __init__(self, name: str, columns: list, rows: list = None):
        self.name = name
        self.columns = list(columns)
        self.rows = list(rows or [])

    def append(self, row: dict) -> None:
        self.rows.append({c: row.get(c) for c in self.columns})

    def tuples(self) -> list:
        """Rows as tuples in `columns` order — ready for execute_values."""
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
    """The tables plus the metadata describing how they were captured."""

    def __init__(self, window: "MonthWindow",
                 captured_at=None, customer_filter=None, site_id_filter=None,
                 latest_agent_version=None):
        self.window = window
        # The period actually covered, recorded so a reader never has to guess
        # whether a July row means 31 days or the 27 that had happened.
        self.report_month = window.start.strftime("%Y-%m")
        self.month_start = window.start
        self.month_end = window.end
        self.period_label = window.span_label
        self.is_partial_month = window.is_partial
        self.days_covered = window.days_covered
        self.captured_at = captured_at or datetime.now(tz=timezone.utc)
        self.customer_filter = customer_filter
        self.site_id_filter = site_id_filter
        self.latest_agent_version = latest_agent_version
        self.tables = {name: DataTable(name, cols) for name, cols in TABLE_COLUMNS.items()}

    def __getitem__(self, name) -> DataTable:
        return self.tables[name]

    @property
    def customer_month(self): return self.tables["customer_month"]
    @property
    def threat(self):         return self.tables["threat"]
    @property
    def agent(self):          return self.tables["agent"]

    @property
    def customers(self) -> list:
        """Customer names present in this dataset, in insert order."""
        return [r["customer_name"] for r in self.customer_month.rows]

    def to_dataframes(self) -> dict:
        return {name: t.to_dataframe() for name, t in self.tables.items()}

    def to_csv_dir(self, directory: str) -> list:
        import os
        os.makedirs(directory, exist_ok=True)
        return [t.to_csv(os.path.join(directory, f"{name}.csv"))
                for name, t in self.tables.items()]

    def set_report_path(self, customer_name: str, path: str) -> None:
        """Record which .txt was written for a customer, before the DB insert."""
        for row in self.customer_month.rows:
            if row["customer_name"] == customer_name:
                row["report_path"] = str(path)
                return

    def summary(self) -> dict:
        return {
            "captured_at":     self.captured_at.isoformat(),
            "report_month":    self.report_month,
            "period":          self.period_label,
            "period_start":    self.month_start.isoformat(),
            "period_end":      self.month_end.isoformat(),
            "days_covered":    self.days_covered,
            "is_partial":      self.is_partial_month,
            "customer_filter": self.customer_filter,
            "row_counts":      {n: len(t) for n, t in self.tables.items()},
        }

    def __repr__(self):
        return (f"<Dataset {self.period_label} "
                f"customers={len(self.customer_month)} "
                f"threats={len(self.threat)} agents={len(self.agent)}>")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _ranked(table, customer, rows, name_key):
    """Append a ranked (name, count) list, 1-based, skipping blank names."""
    rank = 0
    seen = set()
    for name, count in rows:
        if not name or name in seen:
            continue
        seen.add(name)
        rank += 1
        table.append({
            "customer_name": customer,
            name_key: name,
            "detections": count,
            "rank": rank,
        })


def build_dataset(month: str, threats: list, agents: list,
                  customer_filter: str = None, site_id_filter: str = None,
                  latest_agent_version: str = None, captured_at=None,
                  window: MonthWindow = None) -> Dataset:
    """
    Flatten raw API records into a Dataset for one report month.

    Pass `window` when the caller already resolved it, so the period recorded on
    the snapshot is exactly the period that was queried.
    """
    captured_at = captured_at or datetime.now(tz=timezone.utc)
    window = window or month_window(month, as_of=captured_at)

    ds = Dataset(window, captured_at=captured_at,
                 customer_filter=customer_filter, site_id_filter=site_id_filter,
                 latest_agent_version=latest_agent_version)

    stale_cutoff = captured_at - timedelta(days=M.STALE_DAYS)
    buckets = M.split_by_site(threats, agents)

    for site, bucket in buckets.items():
        metrics = M.compute_customer(
            site, bucket["threats"], bucket["agents"],
            site_id=bucket["site_id"], now=captured_at,
        )
        site_id = metrics["site_id"]

        row = {c: metrics.get(c) for c in CUSTOMER_MONTH_COLUMNS}
        row["report_month"] = month
        row["report_path"] = None
        ds.customer_month.append(row)

        # -- threats --------------------------------------------------------
        for t in bucket["threats"]:
            threat_id = t.get("id") or get(t, "threatInfo", "threatId")
            if not threat_id:
                continue          # cannot key a row without the console's id
            tactics = sorted(set(M.threat_tactics(t)))
            ds.threat.append({
                "threat_id":           str(threat_id),
                "customer_name":       site,
                "site_id":             M.threat_site_id(t) or site_id,
                "threat_name":         get(t, "threatInfo", "threatName"),
                "classification":      get(t, "threatInfo", "classification"),
                "confidence_level":    get(t, "threatInfo", "confidenceLevel"),
                "mitigation_status":   get(t, "threatInfo", "mitigationStatus"),
                "detection_type":      get(t, "threatInfo", "detectionType"),
                "analyst_verdict":     get(t, "threatInfo", "analystVerdict"),
                "incident_status":     get(t, "threatInfo", "incidentStatus"),
                "created_at":          M.parse_dt(get(t, "threatInfo", "createdAt")),
                "updated_at":          M.parse_dt(get(t, "threatInfo", "updatedAt")),
                "mttr_minutes":        M.threat_mttr(t),
                "detected_on":         M.detected_on(t),
                "process_user":        get(t, "threatInfo", "processUser"),
                "file_path":           get(t, "threatInfo", "filePath"),
                "sha1":                get(t, "threatInfo", "sha1"),
                "storyline":           get(t, "threatInfo", "storyline"),
                "device_name":         get(t, "agentRealtimeInfo", "agentComputerName"),
                "agent_id":            get(t, "agentRealtimeInfo", "agentId"),
                "agent_os":            get(t, "agentRealtimeInfo", "agentOsName"),
                "is_lolbin":           M.is_lolbin(t),
                "is_lateral_movement": M.is_lateral_movement(t),
                "mitre_tactics":       ", ".join(tactics) or None,
            })

        # -- agents ---------------------------------------------------------
        for a in bucket["agents"]:
            agent_id = a.get("id")
            if not agent_id:
                continue
            seen_at = M.parse_dt(a.get("lastActiveDate"))
            codes = M.agent_health_codes(a)
            ds.agent.append({
                "agent_id":             str(agent_id),
                "customer_name":        site,
                "site_id":              a.get("siteId") or site_id,
                "computer_name":        a.get("computerName"),
                "domain":               a.get("domain"),
                "machine_type":         a.get("machineType"),
                "group_name":           a.get("groupName"),
                "os_name":              a.get("osName"),
                "os_type":              a.get("osType"),
                "agent_version":        a.get("agentVersion"),
                "is_up_to_date":        a.get("isUpToDate"),
                "mitigation_mode":      a.get("mitigationMode"),
                "infected":             a.get("infected"),
                "network_status":       a.get("networkStatus"),
                "scan_status":          a.get("scanStatus"),
                "is_pending_uninstall": a.get("isPendingUninstall"),
                "is_active":            a.get("isActive"),
                "is_decommissioned":    a.get("isDecommissioned"),
                "last_active":          seen_at,
                "days_since_active":    (captured_at - seen_at).days if seen_at else None,
                "is_stale":             bool(seen_at and seen_at < stale_cutoff),
                "user_actions_needed":  ", ".join(codes) or None,
                "health_issue_count":   len(codes),
            })

        # -- breakdowns -----------------------------------------------------
        for classification, count in metrics["classifications"]:
            ds["threat_class"].append({
                "customer_name": site,
                "classification": classification,
                "threat_count": count,
            })

        for tactic, count in metrics["tactics"]:
            ds["mitre_tactic"].append({
                "customer_name": site, "tactic": tactic, "threat_count": count,
            })

        _ranked(ds["top_threat"], site, metrics["top_threats"], "threat_name")
        _ranked(ds["top_device"], site, metrics["top_devices"], "device_name")
        _ranked(ds["top_user"],   site, metrics["top_users"],   "process_user")
        _ranked(ds["lolbin"],     site, metrics["lolbin_top"],  "threat_name")

        for day, count in metrics["daily_trend"]:
            ds["daily_trend"].append({
                "customer_name": site, "trend_date": day, "threat_count": count,
            })

        for os_name, count in metrics["os_spread"]:
            if os_name:
                ds["os_spread"].append({
                    "customer_name": site, "os_name": os_name, "device_count": count,
                })

        for version, count in metrics["version_spread"]:
            if version:
                ds["agent_version"].append({
                    "customer_name": site, "agent_version": version, "device_count": count,
                })

    return ds


def fetch_dataset(month: str = None, customer: str = None, site_id: str = None,
                  client: S1Client = None) -> Dataset:
    """
    Fetch a month from the SentinelOne API and return it as flat tables.

    month     'YYYY-MM'; defaults to the current month, covering the 1st through
              the end of today. Agent state is always current regardless — the
              API keeps no endpoint history.
    customer  exact or partial siteName; resolved to a site id via /sites so the
              API filters server-side. None = whole estate.
    site_id   skip name resolution and filter by this site id directly.
    """
    client = client or S1Client.from_env()
    captured_at = datetime.now(tz=timezone.utc)
    window = month_window(month or current_month(), as_of=captured_at)

    if customer and not site_id:
        site_id = resolve_site_id(customer, client=client)

    threats = client.get_threats(window.start_iso, window.end_iso, site_ids=site_id)
    agents = client.get_agents(site_ids=site_id)

    return build_dataset(
        window.start.strftime("%Y-%m"), threats, agents,
        customer_filter=customer, site_id_filter=site_id,
        latest_agent_version=client.latest_agent_version(),
        captured_at=captured_at, window=window,
    )


def resolve_site_id(customer: str, client: S1Client = None) -> str:
    """
    Turn a customer name into a SentinelOne site id.

    Exact (case-insensitive) match wins; otherwise a unique substring match is
    accepted. An ambiguous name is an error rather than a silent guess — loading
    the wrong customer's data into a report is worse than failing.
    """
    client = client or S1Client.from_env()
    sites = client.get_sites()
    needle = customer.strip().lower()

    exact = [s for s in sites if (s.get("name") or "").strip().lower() == needle]
    if len(exact) == 1:
        return str(exact[0]["id"])

    partial = [s for s in sites if needle in (s.get("name") or "").lower()]
    if len(partial) == 1:
        return str(partial[0]["id"])
    if len(partial) > 1:
        names = ", ".join(sorted(s.get("name") or "?" for s in partial))
        raise ValueError(f'"{customer}" matches {len(partial)} sites: {names}')
    raise ValueError(f'No SentinelOne site matches "{customer}"')


if __name__ == "__main__":
    import sys
    month = sys.argv[1] if len(sys.argv) > 1 else current_month()
    customer = " ".join(sys.argv[2:]) or None
    ds = fetch_dataset(month, customer)
    print(ds)
    for name, table in ds.tables.items():
        print(f"  {name:<18} {len(table):>6} rows")
