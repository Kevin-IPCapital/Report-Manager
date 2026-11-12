"""
Cove Data Protection - Agent Interface

Use this module to answer questions about customer backup environments:
  - Backup health and session status per device and customer
  - Red flags: recurring failures, missed backups, errors
  - 28-day backup history analysis — chronic vs one-off failures
  - Server DR (Automated Recovery Testing) configuration gaps
  - Reporting setup gaps (customers not receiving scheduled reports)
  - M365 source coverage: Exchange / OneDrive / SharePoint / Teams
  - M365 sources not backing up (NoSelection, Failed, missing entirely)
  - Security posture: Anti-Crypto (ransomware protection), passphrase encryption
  - LSV (Local Speed Vault) health: offline, failed, out-of-sync
  - Cloud storage status per device
  - Session metrics: error counts, duration, data sent
  - Retention policy: flag dangerously short retention periods
  - Storage deduplication efficiency
  - OS end-of-life detection (Windows 7/8/Server 2008/2012)
  - Client version age — flag outdated backup agents
  - Storage location per device
  - Finance/billing reconciliation: device counts, M365 item counts

Entry points:
  customer_report(name)         Full device-by-device analysis for one customer
  m365_coverage_report()        M365 source coverage matrix across all customers
  security_report()             Anti-crypto, passphrase, DR, LSV, OS EOL across all devices
  all_customers_summary()       Health overview across all customers
  red_flags_report()            All devices/customers with active issues
  billing_report()              Device counts and M365 item counts per customer
  list_customers()              All customer names (use to resolve partial names)
  refresh_cache()               Drop cached data and re-query the API

Tabular export (see cove_dataset.py / cove_db.py):
  fetch_dataset(name)           Same data flattened into database-shaped tables
  load_to_postgres(name)        Fetch and insert a snapshot into PostgreSQL

Performance:
  Device data is fetched once and cached for 5 minutes, so an agent session that
  runs several reports back-to-back only hits the API a single time. Call
  refresh_cache() to force fresh data. Storage location names are only resolved
  for customer_report(), keeping the org-wide reports a round-trip lighter.
"""

import sys
import time
from datetime import datetime, timezone
from cove_client import CoveClient

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

SESSION_STATUS = {
    "0": "Unknown",    "1": "InProcess",  "2": "Failed",
    "3": "Aborted",    "4": "Completed",  "5": "Completed",
    "6": "Interrupted","7": "NotStarted", "8": "CompletedWithErrors",
    "9": "InProgressWithFaults", "10": "OverQuota",
    "11": "NoSelection", "12": "Restarted",
}

UNHEALTHY   = {"2", "3", "6", "9", "10"}
WARNING     = {"8", "12"}
MISSED      = {"0", "7"}          # no backup that day

OS_TYPE      = {"0": "Unknown", "1": "Workstation", "2": "Server"}
ACCOUNT_TYPE = {"0": "Unknown", "1": "Endpoint/Server", "2": "Microsoft 365"}

DR_TESTING = {
    "0": "Not Configured",
    "1": "Recovery Testing Enabled",
    "2": "Standby Image Enabled",
    "3": "Standby Image Enabled",
}

STORAGE_STATUS = {
    "-2": "Offline",  "-1": "Failed", "0": "Unknown",
    "50": "Running",  "100": "Synchronized",
}

# OS strings containing these substrings are end-of-life
EOL_OS_PATTERNS = [
    ("Windows 7",           "EOL Jan 2020"),
    ("Windows 8.1",         "EOL Jan 2023"),
    ("Windows 8",           "EOL Jan 2016"),
    ("Server 2008 R2",      "EOL Jan 2020"),
    ("Server 2008",         "EOL Jan 2020"),
    ("Server 2012 R2",      "EOL Oct 2023"),
    ("Server 2012",         "EOL Oct 2023"),
    ("Windows 10",          "EOL Oct 2025"),
    ("Server 2016",         "EOL Jan 2027"),   # approaching EOL
]

MIN_RETENTION_DAYS    = 30    # flag if retention < this
WARN_RETENTION_DAYS   = 90   # warn if retention < this
BACKUP_AGE_CRITICAL   = 7    # days — critical if no success in this long
BACKUP_AGE_WARNING    = 3    # days — warning if no success in this long

DATASOURCES = {
    "D01": "Files & Folders", "D02": "System State",   "D04": "Exchange (VSS)",
    "D05": "M365 SharePoint", "D06": "Network Shares",  "D08": "VMware",
    "D10": "SQL Server",      "D12": "Oracle",           "D14": "Hyper-V",
    "D15": "MySQL",           "D16": "Virtual DR",       "D19": "M365 Exchange",
    "D20": "M365 OneDrive",   "D23": "M365 Teams",
}

M365_STANDARD = {"D19": "Exchange", "D20": "OneDrive", "D05": "SharePoint", "D23": "Teams"}
M365_SOURCES  = set(M365_STANDARD.keys())

ALL_DS_CODES = ["D01","D02","D04","D05","D06","D08","D10","D12","D14","D15","D16","D19","D20","D23"]

COLUMNS = [
    # Identity & type
    "I0", "I1", "I2", "I8", "I59",
    # Machine
    "I15", "I16", "I17", "I18", "I32",
    # Config & security
    "I11",                              # storage location
    "I22", "I35", "I36", "I37", "I80", "I82", "I86", "I88",
    # Storage
    "I14", "I26",                       # used storage, dedup efficiency %
    # Active datasources string
    "I78",
    # Files & Folders — status, protected, 28d bar, last ok, last session, errors, duration, sent, retention, immutable
    "D01F00","D01F05","D01F06","D01F07","D01F08","D01F09","D01F12","D01F14","D01F15","D01F16","D01F17","D01F18",
    # System State
    "D02F00","D02F06","D02F08","D02F09","D02F14","D02F15","D02F16","D02F17","D02F18",
    # VMware
    "D08F00","D08F06","D08F07","D08F08","D08F09","D08F14","D08F15","D08F16","D08F17","D08F18",
    # SQL Server
    "D10F00","D10F06","D10F07","D10F08","D10F09","D10F14","D10F15","D10F16","D10F17","D10F18",
    # Hyper-V
    "D14F00","D14F06","D14F07","D14F08","D14F09","D14F14","D14F15","D14F16","D14F17","D14F18",
    # M365 Exchange — F20 = licensed user count (billable seats)
    "D19F00","D19F01","D19F06","D19F07","D19F08","D19F09","D19F12","D19F14","D19F15","D19F20",
    # M365 OneDrive
    "D20F00","D20F01","D20F06","D20F07","D20F08","D20F09","D20F12","D20F14","D20F15",
    # M365 SharePoint
    "D05F00","D05F01","D05F06","D05F08","D05F09","D05F14","D05F15",
    # M365 Teams
    "D23F00","D23F01","D23F06","D23F08","D23F09","D23F14","D23F15",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

def _ts(val) -> str:
    try:
        if not val or str(val) in ("0", ""):
            return "-"
        return datetime.fromtimestamp(int(val), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(val)

def _days_ago(val):
    try:
        if not val or str(val) in ("0", ""):
            return None
        return (datetime.now(tz=timezone.utc) -
                datetime.fromtimestamp(int(val), tz=timezone.utc)).days
    except Exception:
        return None

def _gb(val) -> str:
    try:
        if not val or str(val) in ("0", ""):
            return "-"
        return f"{int(val)/1024**3:.2f} GB"
    except Exception:
        return str(val)

def _mb(val) -> str:
    try:
        if not val or str(val) in ("0", ""):
            return "-"
        return f"{int(val)/1024**2:.1f} MB"
    except Exception:
        return str(val)

def _duration(seconds_val) -> str:
    try:
        if not seconds_val or str(seconds_val) in ("0", ""):
            return "-"
        secs = int(seconds_val)
        h, m = divmod(secs // 60, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {secs%60:02d}s"
    except Exception:
        return str(seconds_val)

def _status_label(code) -> str:
    return SESSION_STATUS.get(str(code), f"Status({code})")

def _storage_status_label(code) -> str:
    return STORAGE_STATUS.get(str(code), f"Status({code})")

def _check_os_eol(os_str: str) -> str:
    """Return EOL notice string if OS is end-of-life, else empty string."""
    if not os_str:
        return ""
    for pattern, notice in EOL_OS_PATTERNS:
        if pattern.lower() in os_str.lower():
            return notice
    return ""

def _client_version_old(version_str: str) -> bool:
    """Flag client versions older than major version 25."""
    if not version_str:
        return False
    try:
        major = int(version_str.split(".")[0])
        return major < 25
    except Exception:
        return False

def _parse_sources(i78: str) -> list:
    if not i78:
        return []
    return [
        {"code": i78[i:i+3], "name": DATASOURCES.get(i78[i:i+3], i78[i:i+3])}
        for i in range(0, len(i78)-2, 3)
        if i78[i:i+3].startswith("D")
    ]

def _flatten(settings: list) -> dict:
    out = {}
    for entry in (settings or []):
        out.update(entry)
    return out

def _analyze_colorbar(bar: str) -> dict:
    """
    Parse the 28-day backup history string.
    Each char is a day's session status code (0-12).
    Returns counts and recurring-issue flags.
    """
    if not bar:
        return {}
    ok_days       = sum(1 for c in bar if c in ("4", "5"))
    failed_days   = sum(1 for c in bar if c in UNHEALTHY)
    warning_days  = sum(1 for c in bar if c in WARNING)
    missed_days   = sum(1 for c in bar if c in MISSED)
    # Longest streak of failures
    max_fail_streak = 0
    streak = 0
    for c in bar:
        if c in UNHEALTHY:
            streak += 1
            max_fail_streak = max(max_fail_streak, streak)
        else:
            streak = 0

    return {
        "ok_days":          ok_days,
        "failed_days":      failed_days,
        "warning_days":     warning_days,
        "missed_days":      missed_days,
        "max_fail_streak":  max_fail_streak,
        "recurring_failures": failed_days >= 3,
        "chronic_warnings":   warning_days >= 14,   # half the month with errors
        "chronic_missed":     missed_days >= 7,     # quarter of month no backup
        "raw":                bar,
    }

# ---------------------------------------------------------------------------
# Client + location cache
# ---------------------------------------------------------------------------

_client: CoveClient  = None
_partner_id: int     = None
_location_map: dict  = None        # id → name (lazy; only needed for display)
_device_cache: dict  = {}          # filter_str → (epoch, [devices])
CACHE_TTL_SECONDS    = 300         # serve cached devices for 5 minutes

def _get_client():
    global _client, _partner_id
    if _client is None or _client.visa is None:
        _client = CoveClient()
        r = _client.login()
        _partner_id = r["result"]["PartnerId"]
    return _client, _partner_id

def _get_locations() -> dict:
    """Location name → metadata dict. I11 returns the name directly so we key by name."""
    global _location_map
    if _location_map is None:
        client, _ = _get_client()
        try:
            locs = client.enumerate_locations()
            _location_map = {
                loc["Name"]: {
                    "name":          loc["Name"],
                    "strict_policy": "StrictLocationPolicy" in loc.get("Flags", []),
                }
                for loc in (locs or [])
            }
        except Exception:
            _location_map = {}
    return _location_map

def refresh_cache() -> None:
    """Clear cached device/location data so the next call re-queries the API."""
    global _location_map
    _device_cache.clear()
    _location_map = None

def _fetch_devices(filter_str: str = None, count: int = 5000, use_cache: bool = True) -> list:
    """
    Fetch and parse all devices (optionally server-filtered).

    Results are cached per filter for CACHE_TTL_SECONDS so an agent session that
    runs several reports only hits the API once. Locations are NOT resolved here
    (that costs a separate call); names are looked up lazily at display time.
    """
    key = filter_str or "__all__"
    now = time.time()
    if use_cache:
        hit = _device_cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]

    client, pid = _get_client()
    result = client.enumerate_account_statistics(
        pid, columns=COLUMNS, filter_str=filter_str, count=count
    )
    devices = [_parse_device(d) for d in (result.get("result") or [])]
    _device_cache[key] = (now, devices)
    return devices

# ---------------------------------------------------------------------------
# Device parser
# ---------------------------------------------------------------------------

def _parse_device(raw: dict) -> dict:
    s = _flatten(raw.get("Settings", []))

    account_type_code = str(s.get("I59", "0"))
    os_type_code      = str(s.get("I32", "0"))
    dr_code           = str(s.get("I80", "0"))
    reporting_raw     = s.get("I22", "0")
    active_sources    = _parse_sources(s.get("I78", ""))
    source_codes      = {src["code"] for src in active_sources}
    is_m365           = account_type_code == "2" or bool(source_codes & M365_SOURCES)
    is_server         = os_type_code == "2"
    immutable_enabled = s.get("I86") == "Enabled"
    fips_enabled      = s.get("I88") == "Enabled"
    location_id       = str(s.get("I11", ""))
    lsv_enabled       = s.get("I35") == "1"
    lsv_status_code   = str(s.get("I37", "0"))
    cloud_status_code = str(s.get("I36", "0"))
    passphrase_set    = s.get("I82") == "Yes"         # API returns "Yes"/"No"
    dedup_efficiency  = s.get("I26")                  # % integer
    os_str            = s.get("I16", "")
    os_eol            = _check_os_eol(os_str)
    client_ver        = s.get("I17", "")
    client_old        = _client_version_old(client_ver)
    # Retention from Files & Folders source (primary indicator)
    retention_days    = None
    try:
        rv = s.get("D01F14")
        if rv and str(rv) not in ("0", ""):
            retention_days = int(rv)
    except Exception:
        pass

    try:
        reporting_enabled = bool(reporting_raw and int(reporting_raw) != 0)
        reporting_raw_val = int(reporting_raw or 0)
    except Exception:
        reporting_enabled = False
        reporting_raw_val = 0

    # Datasource sessions — also capture retention per source
    ds_retention = {}  # code → days

    # Last successful backup across all datasources
    last_success_ts = max(
        (int(v) for k in ALL_DS_CODES
         for v in [s.get(f"{k}F09")] if v and str(v) not in ("0","")),
        default=None,
    )
    days_since_success = _days_ago(last_success_ts)

    # Datasource sessions
    datasource_sessions = {}
    for code in ALL_DS_CODES:
        status_val = s.get(f"{code}F00")
        if not status_val or str(status_val) in ("0", ""):
            continue
        bar          = s.get(f"{code}F08", "")
        bar_analysis = _analyze_colorbar(bar)
        ret_val      = s.get(f"{code}F14")
        ret_days     = None
        try:
            if ret_val and str(ret_val) not in ("0",""):
                ret_days = int(ret_val)
                ds_retention[code] = ret_days
        except Exception:
            pass
        imm_last_code = str(s.get(f"{code}F17") or "0")
        datasource_sessions[code] = {
            "code":                code,
            "name":                DATASOURCES.get(code, code),
            "status":              _status_label(status_val),
            "status_code":         str(status_val),
            "protected_gb":        _gb(s.get(f"{code}F07")),
            "protected_bytes":     _int(s.get(f"{code}F07"), None),
            "last_success":        _ts(s.get(f"{code}F09")),
            "last_success_ts":     _int(s.get(f"{code}F09"), None) or None,
            "last_session":        _ts(s.get(f"{code}F15")),
            "last_session_ts":     _int(s.get(f"{code}F15"), None) or None,
            "error_count":         s.get(f"{code}F06"),
            "duration":            _duration(s.get(f"{code}F12")),
            "duration_seconds":    _int(s.get(f"{code}F12"), None),
            "sent":                _mb(s.get(f"{code}F05")),
            "sent_bytes":          _int(s.get(f"{code}F05"), None),
            "item_count":          s.get(f"{code}F01"),
            "retention_days":      ret_days,
            "colorbar":            bar_analysis,
            "imm_active":          str(s.get(f"{code}F16") or "0") in ("4","5"),
            "imm_last_status_code":imm_last_code,
            "imm_last_status":     _status_label(imm_last_code),
            "imm_last_ts":         _ts(s.get(f"{code}F18")),
            "imm_last_ts_epoch":   _int(s.get(f"{code}F18"), None) or None,
        }
    # Use retention_days from primary source if not already set
    if retention_days is None and ds_retention:
        retention_days = min(ds_retention.values())

    # M365 coverage
    m365_coverage = {}
    if is_m365:
        for code, label in M365_STANDARD.items():
            in_i78      = code in source_codes
            session     = datasource_sessions.get(code)
            status_code = session["status_code"] if session else "0"
            if not in_i78:
                coverage = "NOT ENABLED"
            elif status_code == "11":
                coverage = "ENABLED — NoSelection (check licensing/permissions)"
            elif status_code in UNHEALTHY:
                coverage = f"ENABLED — {_status_label(status_code)}"
            elif status_code in WARNING:
                coverage = f"ENABLED — {_status_label(status_code)}"
            elif status_code in ("4", "5"):
                coverage = "OK"
            else:
                coverage = f"ENABLED — {_status_label(status_code)}"
            user_count = None
            try:
                raw_uc = s.get(f"{code}F20")
                if raw_uc and str(raw_uc) not in ("0", ""):
                    user_count = int(raw_uc)
            except Exception:
                pass
            m365_coverage[label] = {
                "code":         code,
                "enabled":      in_i78,
                "coverage":     coverage,
                "status_code":  status_code,
                "last_success": session["last_success"] if session else "-",
                "last_success_ts": session["last_success_ts"] if session else None,
                "item_count":   session["item_count"]   if session else None,
                "user_count":   user_count,
                "colorbar":     session["colorbar"]     if session else {},
            }

    # Red flags
    flags = []

    if days_since_success is not None and days_since_success > 3:
        flags.append(f"NO SUCCESSFUL BACKUP IN {days_since_success} DAYS")
    if last_success_ts is None and not is_m365:
        flags.append("NEVER HAD A SUCCESSFUL BACKUP")

    for code, sess in datasource_sessions.items():
        sc = sess["status_code"]
        cb = sess["colorbar"]
        name = sess["name"]
        if sc in UNHEALTHY:
            flags.append(f"{name}: {sess['status']}")
        elif sc in WARNING:
            flags.append(f"{name}: {sess['status']}")
        elif sc == "11" and code not in M365_SOURCES:
            flags.append(f"{name}: NoSelection")
        # Recurring / chronic from color bar
        if cb.get("recurring_failures"):
            flags.append(f"{name}: RECURRING FAILURES ({cb['failed_days']}/28 days failed)")
        if cb.get("chronic_warnings"):
            flags.append(f"{name}: CHRONIC ERRORS ({cb['warning_days']}/28 days with errors)")
        if cb.get("chronic_missed"):
            flags.append(f"{name}: CHRONIC MISSED BACKUPS ({cb['missed_days']}/28 days no backup)")

    if is_m365:
        for label, info in m365_coverage.items():
            if not info["enabled"]:
                flags.append(f"M365 {label}: NOT ENABLED")
            elif info["status_code"] == "11":
                flags.append(f"M365 {label}: NoSelection — check licensing/permissions")
            elif info["status_code"] in UNHEALTHY:
                flags.append(f"M365 {label}: {_status_label(info['status_code'])}")
            cb = info.get("colorbar", {})
            if cb.get("recurring_failures"):
                flags.append(f"M365 {label}: RECURRING FAILURES ({cb['failed_days']}/28 days)")
            if cb.get("chronic_missed"):
                flags.append(f"M365 {label}: NOT BACKING UP REGULARLY ({cb['missed_days']}/28 days no backup)")

    if is_server and dr_code == "0":
        flags.append("SERVER: AUTOMATED DR TESTING NOT CONFIGURED")
    if not reporting_enabled:
        flags.append("NOT CONFIGURED FOR SCHEDULED REPORTING")
    if not passphrase_set and not is_m365:
        flags.append("NO ENCRYPTION PASSPHRASE SET")
    if not is_m365:
        for code, sess in datasource_sessions.items():
            if sess["imm_active"] and sess["imm_last_status_code"] not in ("4", "5", "0", ""):
                flags.append(f"{sess['name']}: IMMUTABLE COPY FAILED ({sess['imm_last_status']})")

    # LSV health (only when LSV is enabled)
    if lsv_enabled:
        if lsv_status_code == "-2":
            flags.append("LSV: OFFLINE — local backup vault unreachable")
        elif lsv_status_code == "-1":
            flags.append("LSV: FAILED — local backup vault in error state")
        elif lsv_status_code not in ("50", "100"):
            flags.append(f"LSV: {_storage_status_label(lsv_status_code)} — check LSV health")

    # Cloud storage health (endpoint devices only — M365 shows 0 normally)
    if not is_m365 and cloud_status_code not in ("100", "50", "0"):
        flags.append(f"CLOUD STORAGE: {_storage_status_label(cloud_status_code)}")

    # Retention too short
    if retention_days is not None and retention_days < MIN_RETENTION_DAYS:
        flags.append(f"RETENTION CRITICALLY SHORT: {retention_days} days (minimum recommended: {MIN_RETENTION_DAYS})")
    elif retention_days is not None and retention_days < WARN_RETENTION_DAYS:
        flags.append(f"RETENTION SHORT: {retention_days} days (recommended: {WARN_RETENTION_DAYS}+)")

    # OS end-of-life
    if os_eol and not is_m365:
        flags.append(f"END-OF-LIFE OS: {os_eol} — {os_str[:60]}")

    # Old client version
    if client_old and not is_m365:
        flags.append(f"OUTDATED CLIENT: {client_ver}")

    return {
        "device_id":           s.get("I0"),
        "device_name":         s.get("I1"),
        "device_alias":        s.get("I2"),
        "customer":            s.get("I8"),
        "contact_email":       s.get("I15"),
        "hostname":            s.get("I18"),
        "os":                  s.get("I16"),
        "os_type":             OS_TYPE.get(os_type_code, os_type_code),
        "account_type":        ACCOUNT_TYPE.get(account_type_code, account_type_code),
        "client_version":      s.get("I17"),
        "used_storage":        _gb(s.get("I14")),
        "used_storage_bytes":  _int(s.get("I14")),
        "location_id":         location_id,
        "reporting_enabled":   reporting_enabled,
        "reporting_raw":       reporting_raw_val,
        "dr_testing":          DR_TESTING.get(dr_code, f"Unknown({dr_code})"),
        "immutable_enabled":   immutable_enabled,
        "fips_enabled":        fips_enabled,
        "lsv_enabled":         lsv_enabled,
        "lsv_status":          _storage_status_label(lsv_status_code),
        "cloud_status":        _storage_status_label(cloud_status_code),
        "passphrase_set":      passphrase_set,
        "dedup_efficiency":    dedup_efficiency,
        "retention_days":      retention_days,
        "os_eol":              os_eol,
        "client_old":          client_old,
        "is_server":           is_server,
        "is_m365":             is_m365,
        "active_datasources":  active_sources,
        "last_success":        _ts(last_success_ts),
        "days_since_success":  days_since_success,
        "datasource_sessions": datasource_sessions,
        "m365_coverage":       m365_coverage,
        "last_success_ts":     last_success_ts,
        "red_flags":           flags,
        "health": (
            "CRITICAL" if any(
                "NO SUCCESSFUL BACKUP" in f or ": Failed" in f
                or ": Aborted" in f or ": Interrupted" in f or "NEVER HAD" in f
                for f in flags
            ) else "WARNING" if flags else "OK"
        ),
    }

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_customers() -> list:
    return sorted({d["customer"] for d in _fetch_devices() if d["customer"]})


def customer_report(name: str) -> dict:
    """Full device-by-device analysis for one customer (partial name match)."""
    all_devices = _fetch_devices()
    devices     = [d for d in all_devices if d["customer"] and name.lower() in d["customer"].lower()]

    if not devices:
        return {"error": f"No customer matching '{name}'", "available": list_customers()}

    customer_name    = devices[0]["customer"]
    flags_all        = [f for d in devices for f in d["red_flags"]]
    servers          = [d for d in devices if d["is_server"]]
    m365_devices     = [d for d in devices if d["is_m365"]]
    endpoint_devices = [d for d in devices if not d["is_m365"]]
    unhealthy        = [d for d in devices if d["health"] != "OK"]
    locations        = _get_locations()   # resolve names only for this detailed view

    lines = [
        f"╔══ CUSTOMER REPORT: {customer_name} ══",
        f"  Total devices   : {len(devices)}",
        f"  Endpoint/Server : {len(endpoint_devices)}  (Servers: {len(servers)})",
        f"  Microsoft 365   : {len(m365_devices)}",
        f"  Healthy         : {len(devices) - len(unhealthy)}",
        f"  Needs attention : {len(unhealthy)}",
        "",
    ]

    for d in devices:
        icon = "✓" if d["health"] == "OK" else ("⚠" if d["health"] == "WARNING" else "✗")
        lines.append(f"  [{icon}] {d['device_name']}  ({d['hostname'] or d['device_name']})")
        lines.append(f"       Type          : {d['account_type']} / {d['os_type']}")
        if d["contact_email"]:
            lines.append(f"       Contact       : {d['contact_email']}")
        lines.append(f"       OS            : {d['os'] or '-'}")
        lines.append(f"       Client        : {d['client_version'] or '-'}")
        loc_info = locations.get(d["location_id"], {})
        loc_name = loc_info.get("name", d["location_id"]) or "-"
        loc_flag = "  [data sovereignty enforced]" if loc_info.get("strict_policy") else ""
        lines.append(f"       Storage used  : {d['used_storage']}")
        lines.append(f"       Backup storage: {loc_name}{loc_flag}")
        if d["days_since_success"] is not None:
            lines.append(f"       Last success  : {d['last_success']}  ({d['days_since_success']}d ago)")
        else:
            lines.append(f"       Last success  : {d['last_success']}")
        lines.append(f"       Reporting     : {'Yes (freq={})'.format(d['reporting_raw']) if d['reporting_enabled'] else '⚠ NOT SET UP'}")
        lines.append(f"       DR Testing    : {d['dr_testing']}")
        if not d["is_m365"]:
            lines.append(f"       Immutable     : {'Enabled' if d['immutable_enabled'] else '✗ Not enabled'}")
            lines.append(f"       Encryption    : {'FIPS 140-3' if d['fips_enabled'] else '-'}")
            lines.append(f"       Passphrase    : {'Set' if d['passphrase_set'] else '✗ Not set'}")
        if d["lsv_enabled"]:
            lines.append(f"       LSV           : Enabled — {d['lsv_status']}")
        else:
            lines.append(f"       LSV           : Disabled")
        if not d["is_m365"]:
            lines.append(f"       Cloud Storage : {d['cloud_status']}")
        if d["retention_days"] is not None:
            warn = " ⚠" if d["retention_days"] < WARN_RETENTION_DAYS else ""
            lines.append(f"       Retention     : {d['retention_days']} days{warn}")
        if d["dedup_efficiency"] and str(d["dedup_efficiency"]) not in ("0",""):
            lines.append(f"       Dedup Eff.    : {d['dedup_efficiency']}%")
        if d["os_eol"]:
            lines.append(f"       OS EOL        : ⚠ {d['os_eol']}")

        if d["is_m365"] and d["m365_coverage"]:
            lines.append(f"       M365 Sources  :")
            for src_label, info in d["m365_coverage"].items():
                src_icon = "✓" if info["coverage"] == "OK" else ("⚠" if "ENABLED" in info["coverage"] else "✗")
                items_str = f"  items: {info['item_count']}" if info["item_count"] else ""
                cb = info.get("colorbar", {})
                bar_note = f"  [28d: {cb.get('ok_days',0)}ok/{cb.get('failed_days',0)}fail/{cb.get('missed_days',0)}miss]" if cb else ""
                lines.append(f"         [{src_icon}] {src_label:<12} {info['coverage']:<40} last: {info['last_success']}{items_str}{bar_note}")

        if not d["is_m365"] and d["datasource_sessions"]:
            lines.append(f"       Datasources   :")
            for code, sess in d["datasource_sessions"].items():
                s_icon = "✓" if sess["status_code"] in ("4","5") else ("⚠" if sess["status_code"] in WARNING else "✗")
                cb = sess["colorbar"]
                bar_str = f"28d: {cb.get('ok_days',0)}ok/{cb.get('failed_days',0)}fail/{cb.get('missed_days',0)}miss" if cb else ""
                imm_str = ""
                if sess["imm_active"]:
                    imm_icon = "✓" if sess["imm_last_status_code"] in ("4","5") else "✗"
                    imm_str = f"  imm:[{imm_icon}]{sess['imm_last_status']} {sess['imm_last_ts']}"
                lines.append(
                    f"         [{s_icon}] {sess['name']:<22} {sess['status']:<22}"
                    f" errors:{sess['error_count'] or 0}  dur:{sess['duration']}  sent:{sess['sent']}{imm_str}"
                )
                if bar_str:
                    lines.append(f"              {bar_str}  protected:{sess['protected_gb']}")

        if d["red_flags"]:
            for f in d["red_flags"]:
                lines.append(f"       🚩 {f}")
        lines.append("")

    if flags_all:
        lines.append("  ── RED FLAGS SUMMARY ──")
        for f in sorted(set(flags_all)):
            lines.append(f"    🚩 {f}")

    return {
        "customer":        customer_name,
        "devices":         devices,
        "total_devices":   len(devices),
        "m365_devices":    len(m365_devices),
        "server_count":    len(servers),
        "unhealthy_count": len(unhealthy),
        "red_flags":       sorted(set(flags_all)),
        "summary":         "\n".join(lines),
    }


def m365_coverage_report() -> dict:
    """M365 source coverage matrix — which sources are enabled and healthy per customer."""
    devices   = _fetch_devices()
    m365_devs = [d for d in devices if d["is_m365"]]
    by_customer: dict = {}
    for d in m365_devs:
        by_customer.setdefault(d["customer"], []).append(d)

    header = f"{'CUSTOMER':<40} {'EXC':>5} {'OD':>5} {'SP':>5} {'TEAMS':>6}  {'REPORTING':<12}  ISSUES"
    lines = [
        "M365 COVERAGE REPORT",
        "EXC=Exchange  OD=OneDrive  SP=SharePoint  ✓=OK  ⚠=Issue  ✗=Not Enabled  ?=No Data",
        "", header, "─" * 115,
    ]
    rows = []
    for customer, devs in sorted(by_customer.items()):
        for d in devs:
            cov = d["m365_coverage"]
            def _icon(label):
                info = cov.get(label)
                if not info: return "?"
                if not info["enabled"]: return "✗"
                if info["coverage"] == "OK": return "✓"
                if info["status_code"] in UNHEALTHY: return "✗"
                return "⚠"
            exc=_icon("Exchange"); od=_icon("OneDrive")
            sp=_icon("SharePoint"); teams=_icon("Teams")
            rep = "✓ Yes" if d["reporting_enabled"] else "✗ NOT SET"
            issues = [f for f in d["red_flags"] if "M365" in f or "REPORTING" in f]
            lines.append(
                f"  {customer:<38} {exc:>5} {od:>5} {sp:>5} {teams:>6}  {rep:<12}  {'; '.join(issues)}"
            )
            rows.append({
                "customer":   customer, "device": d["device_name"],
                "exchange": exc, "onedrive": od, "sharepoint": sp, "teams": teams,
                "reporting": d["reporting_enabled"], "issues": issues,
            })

    total = len(rows)
    lines += [
        "─" * 115,
        f"  {'TOTALS':<38}"
        f" {sum(r['exchange']  =='✓' for r in rows):>4}/{total}"
        f" {sum(r['onedrive']  =='✓' for r in rows):>4}/{total}"
        f" {sum(r['sharepoint']=='✓' for r in rows):>4}/{total}"
        f" {sum(r['teams']     =='✓' for r in rows):>5}/{total}"
        f"  {sum(r['reporting'] for r in rows):>4}/{total} reporting",
    ]
    no_teams  = [r["customer"] for r in rows if r["teams"]     == "✗"]
    no_sp     = [r["customer"] for r in rows if r["sharepoint"]== "✗"]
    no_rep    = [r["customer"] for r in rows if not r["reporting"]]
    nosel     = [r["customer"] for r in rows if any("NoSelection" in i for i in r["issues"])]
    if no_teams: lines.append(f"\n  Teams NOT ENABLED ({len(no_teams)}): {', '.join(no_teams)}")
    if no_sp:    lines.append(f"\n  SharePoint NOT ENABLED ({len(no_sp)}): {', '.join(no_sp)}")
    if nosel:    lines.append(f"\n  NoSelection issues ({len(nosel)}): {', '.join(nosel)}")
    if no_rep:   lines.append(f"\n  No scheduled reporting ({len(no_rep)}): {', '.join(no_rep)}")

    return {
        "m365_customers": len(by_customer), "rows": rows,
        "no_teams": no_teams, "no_sharepoint": no_sp,
        "no_reporting": no_rep, "noselection": nosel,
        "summary": "\n".join(lines),
    }


def security_report() -> dict:
    """
    Security posture across all devices:
      - Anti-Crypto (ransomware protection) status
      - Passphrase encryption
      - DR testing gaps for servers
      - LSV health (where LSV is enabled)
      - Cloud storage status anomalies
      - OS end-of-life
      - Outdated client versions
      - Short retention policies
    """
    devices = _fetch_devices()
    locations = _get_locations()
    endpoint_devs = [d for d in devices if not d["is_m365"]]

    imm_failed = [d for d in endpoint_devs if any(
        sess["imm_active"] and sess["imm_last_status_code"] not in ("4","5","0","")
        for sess in d["datasource_sessions"].values()
    )]
    no_dr      = [d for d in endpoint_devs if d["is_server"] and "NOT CONFIGURED" in d["dr_testing"]]
    no_passphrase  = [d for d in endpoint_devs if not d["passphrase_set"]]
    lsv_list       = [d for d in endpoint_devs if d["lsv_enabled"]]
    lsv_unhealthy  = [d for d in lsv_list if d["lsv_status"] not in ("Synchronized", "Running")]
    cloud_degraded = [d for d in endpoint_devs if d["cloud_status"] not in ("Synchronized", "Running", "Unknown")]
    eol_devices    = [d for d in endpoint_devs if d["os_eol"]]
    old_clients    = [d for d in endpoint_devs if d["client_old"]]
    ret_critical   = [d for d in endpoint_devs if d["retention_days"] is not None and d["retention_days"] < MIN_RETENTION_DAYS]
    ret_warn       = [d for d in endpoint_devs if d["retention_days"] is not None
                      and MIN_RETENTION_DAYS <= d["retention_days"] < WARN_RETENTION_DAYS]

    # Backup storage location summary across all devices
    from collections import Counter
    loc_counts = Counter(d["location_id"] for d in devices)
    loc_summary = []
    for loc_name, count in loc_counts.most_common():
        info = locations.get(loc_name, {})
        policy = "  [data sovereignty enforced]" if info.get("strict_policy") else ""
        loc_summary.append(f"    {loc_name:<30} {count} devices{policy}")

    lines = [
        "SECURITY & HEALTH POSTURE REPORT",
        f"  Endpoint/server devices : {len(endpoint_devs)}",
        f"  Immutable copy failed   : {len(imm_failed)} / {len(endpoint_devs)}",
        f"  No passphrase set       : {len(no_passphrase)} / {len(endpoint_devs)}",
        f"  Servers without DR test : {len(no_dr)}",
        f"  LSV enabled             : {len(lsv_list)}  (unhealthy: {len(lsv_unhealthy)})",
        f"  Cloud storage degraded  : {len(cloud_degraded)}",
        f"  End-of-life OS          : {len(eol_devices)}",
        f"  Outdated client (< v25) : {len(old_clients)}",
        f"  Retention critical (<{MIN_RETENTION_DAYS}d) : {len(ret_critical)}",
        f"  Retention warning (<{WARN_RETENTION_DAYS}d)  : {len(ret_warn)}",
        "",
        "  BACKUP STORAGE LOCATIONS:",
        *loc_summary,
        "",
    ]

    if imm_failed:
        lines.append(f"  IMMUTABLE COPY FAILURES ({len(imm_failed)} devices):")
        for d in sorted(imm_failed, key=lambda x: x["customer"]):
            loc = locations.get(d["location_id"], {}).get("name", d["location_id"]) or "-"
            lines.append(f"    {d['customer']:<40} {d['device_name']}  [{loc}]")
        lines.append("")

    if no_passphrase:
        lines.append(f"  NO ENCRYPTION PASSPHRASE SET ({len(no_passphrase)}):")
        for d in sorted(no_passphrase, key=lambda x: x["customer"]):
            loc = locations.get(d["location_id"], {}).get("name", d["location_id"]) or "-"
            lines.append(f"    {d['customer']:<40} {d['device_name']}  [{loc}]")
        lines.append("")

    if no_dr:
        lines.append(f"  SERVERS WITHOUT AUTOMATED DR TESTING ({len(no_dr)}):")
        for d in sorted(no_dr, key=lambda x: x["customer"]):
            loc = locations.get(d["location_id"], {}).get("name", d["location_id"]) or "-"
            lines.append(f"    {d['customer']:<40} {d['device_name']}  ({d['os'] or 'Server'})  [{loc}]")
        lines.append("")

    if lsv_unhealthy:
        lines.append(f"  LSV UNHEALTHY ({len(lsv_unhealthy)} devices):")
        for d in sorted(lsv_unhealthy, key=lambda x: x["customer"]):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  LSV: {d['lsv_status']}")
        lines.append("")
    elif lsv_list:
        lines.append(f"  LSV: all {len(lsv_list)} enabled device(s) synchronized.")
        lines.append("")

    if cloud_degraded:
        lines.append(f"  CLOUD STORAGE DEGRADED ({len(cloud_degraded)} devices):")
        for d in sorted(cloud_degraded, key=lambda x: x["customer"]):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  Storage: {d['cloud_status']}")
        lines.append("")

    if eol_devices:
        lines.append(f"  END-OF-LIFE OPERATING SYSTEMS ({len(eol_devices)} devices):")
        for d in sorted(eol_devices, key=lambda x: x["customer"]):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  {d['os_eol']}  [{d['os'] or '-'}]")
        lines.append("")

    if old_clients:
        lines.append(f"  OUTDATED BACKUP CLIENTS ({len(old_clients)} devices):")
        for d in sorted(old_clients, key=lambda x: x["client_version"] or ""):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  v{d['client_version']}")
        lines.append("")

    if ret_critical:
        lines.append(f"  CRITICALLY SHORT RETENTION (< {MIN_RETENTION_DAYS} days, {len(ret_critical)} devices):")
        for d in sorted(ret_critical, key=lambda x: x["customer"]):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  {d['retention_days']} days")
        lines.append("")

    if ret_warn:
        lines.append(f"  SHORT RETENTION (< {WARN_RETENTION_DAYS} days, {len(ret_warn)} devices):")
        for d in sorted(ret_warn, key=lambda x: x["customer"]):
            lines.append(f"    {d['customer']:<40} {d['device_name']}  {d['retention_days']} days")

    return {
        "endpoint_devices":       len(endpoint_devs),
        "immutable_failed":       len(imm_failed),
        "no_dr_testing":          len(no_dr),
        "no_passphrase":          len(no_passphrase),
        "lsv_count":              len(lsv_list),
        "lsv_unhealthy":          len(lsv_unhealthy),
        "cloud_degraded":         len(cloud_degraded),
        "eol_devices":            len(eol_devices),
        "old_clients":            len(old_clients),
        "retention_critical":     len(ret_critical),
        "retention_warning":      len(ret_warn),
        "devices_imm_failed":     imm_failed,
        "devices_no_dr":          no_dr,
        "devices_eol":            eol_devices,
        "summary":                "\n".join(lines),
    }


def all_customers_summary() -> dict:
    """One-line health status per customer."""
    devices = _fetch_devices()
    by_customer: dict = {}
    for d in devices:
        by_customer.setdefault(d["customer"] or "Unknown", []).append(d)

    rows = []
    for customer, devs in sorted(by_customer.items()):
        flags = [f for d in devs for f in d["red_flags"]]
        health = (
            "CRITICAL" if any(d["health"] == "CRITICAL" for d in devs)
            else "WARNING" if any(d["health"] == "WARNING" for d in devs)
            else "OK"
        )
        rows.append({
            "customer": customer, "device_count": len(devs),
            "m365_count": sum(1 for d in devs if d["is_m365"]),
            "server_count": sum(1 for d in devs if d["is_server"]),
            "health": health, "flag_count": len(flags),
            "red_flags": sorted(set(flags)),
        })

    lines = [f"{'CUSTOMER':<40} {'DEVICES':>7} {'M365':>5} {'SERVERS':>8}  {'HEALTH':<10} {'FLAGS':>6}"]
    lines.append("─" * 84)
    for r in rows:
        icon = "✓" if r["health"] == "OK" else ("⚠" if r["health"] == "WARNING" else "✗")
        lines.append(
            f"{r['customer']:<40} {r['device_count']:>7} {r['m365_count']:>5}"
            f" {r['server_count']:>8}  {icon} {r['health']:<9} {r['flag_count']:>6}"
        )
    total_flags = sum(r["flag_count"] for r in rows)
    lines += [
        "─" * 84,
        f"{'TOTAL':<40} {len(devices):>7} {sum(r['m365_count'] for r in rows):>5}"
        f" {sum(r['server_count'] for r in rows):>8}  {total_flags:>17} flags",
    ]
    return {"customers": rows, "summary": "\n".join(lines)}


def red_flags_report() -> dict:
    """All devices with active issues across all customers."""
    devices = _fetch_devices()
    flagged = [d for d in devices if d["red_flags"]]
    by_customer: dict = {}
    for d in flagged:
        by_customer.setdefault(d["customer"], []).append(d)

    lines = [f"RED FLAGS REPORT  ({len(flagged)} devices across {len(by_customer)} customers)\n"]
    for customer, devs in sorted(by_customer.items()):
        lines.append(f"  {customer}")
        for d in devs:
            lines.append(f"    {d['device_name']}  [{d['account_type']} / {d['os_type']}]")
            for f in d["red_flags"]:
                lines.append(f"      🚩 {f}")
        lines.append("")

    return {
        "flagged_devices": len(flagged), "affected_customers": len(by_customer),
        "devices": flagged, "summary": "\n".join(lines),
    }


def billing_report() -> dict:
    """Device counts, M365 billable users, and recovery testing per customer."""
    devices = _fetch_devices()
    by_customer: dict = {}
    for d in devices:
        by_customer.setdefault(d["customer"] or "Unknown", []).append(d)

    rows = []
    for customer, devs in sorted(by_customer.items()):
        workstations = sum(1 for d in devs if not d["is_m365"] and not d["is_server"])
        servers      = sum(1 for d in devs if d["is_server"])
        m365         = sum(1 for d in devs if d["is_m365"])
        dr_enabled   = sum(1 for d in devs if d["is_server"] and "Enabled" in d["dr_testing"])
        # M365 billable users = Exchange mailbox count (D19F02)
        m365_users   = 0
        for d in devs:
            if d["is_m365"]:
                uc = (d["m365_coverage"].get("Exchange") or {}).get("user_count") or 0
                m365_users += uc
        rows.append({
            "customer":    customer,
            "workstations":workstations,
            "servers":     servers,
            "m365":        m365,
            "dr_enabled":  dr_enabled,
            "m365_users":  m365_users,
            "total":       len(devs),
        })

    # Totals
    t_wk  = sum(r["workstations"] for r in rows)
    t_srv = sum(r["servers"]      for r in rows)
    t_m365= sum(r["m365"]         for r in rows)
    t_dr  = sum(r["dr_enabled"]   for r in rows)
    t_usr = sum(r["m365_users"]   for r in rows)
    t_tot = sum(r["total"]        for r in rows)

    hdr = f"{'CUSTOMER':<42} {'WKSTN':>6} {'SRVR':>6} {'DR':>5} {'M365':>6} {'M365 USERS':>11} {'TOTAL':>7}"
    lines = [
        "BILLING SUMMARY REPORT",
        "  M365 Users = Exchange mailbox count (billable seats per tenant)",
        "  DR = servers with recovery testing enabled",
        "",
        hdr,
        "─" * len(hdr),
    ]
    for r in rows:
        dr_str   = f"{r['dr_enabled']}/{r['servers']}" if r["servers"] else "-"
        usr_str  = f"{r['m365_users']:,}" if r["m365_users"] else ("-" if not r["m365"] else "0")
        lines.append(
            f"{r['customer']:<42} {r['workstations']:>6} {r['servers']:>6} {dr_str:>5}"
            f" {r['m365']:>6} {usr_str:>11} {r['total']:>7}"
        )
    dr_total_str = f"{t_dr}/{t_srv}" if t_srv else "-"
    lines += [
        "─" * len(hdr),
        f"{'TOTAL':<42} {t_wk:>6} {t_srv:>6} {dr_total_str:>5} {t_m365:>6} {t_usr:>11,} {t_tot:>7}",
        "",
        f"  Workstations          : {t_wk}",
        f"  Servers               : {t_srv}  (recovery testing enabled: {t_dr} / {t_srv})",
        f"  M365 Tenants          : {t_m365}",
        f"  M365 Billable Users   : {t_usr:,}",
        f"  Total devices         : {t_tot}",
    ]
    return {
        "customers":      rows,
        "total_wkstn":    t_wk,
        "total_servers":  t_srv,
        "total_m365":     t_m365,
        "total_dr":       t_dr,
        "total_m365_users": t_usr,
        "summary":        "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# Reporting email update
# ---------------------------------------------------------------------------

def update_reporting_email(email: str, dry_run: bool = True) -> dict:
    """
    Set the recipient email (I15) on all endpoint/server devices that have
    reporting enabled (I22 != 0).  M365 tenants are skipped — they use a
    different notification path.

    dry_run=True  — print what would change, touch nothing.
    dry_run=False — apply the change via EditAccount.
    """
    client, _ = _get_client()
    devices = _fetch_devices()
    targets = [d for d in devices if d["reporting_enabled"] and not d["is_m365"]]

    results = {"updated": [], "failed": [], "skipped": [], "dry_run": dry_run}

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Setting reporting email → {email}")
    print(f"{prefix}Targets: {len(targets)} endpoint/server devices\n")

    for d in sorted(targets, key=lambda x: (x["customer"], x["device_name"])):
        dev_id   = int(d["device_id"])
        existing = d["contact_email"] or "(none)"
        if not dry_run:
            try:
                client.edit_account(dev_id, {"I15": email})
                results["updated"].append(d)
                print(f"  ✓  {d['customer']:<40} {d['device_name']}  [{existing} → {email}]")
            except Exception as exc:
                results["failed"].append({"device": d, "error": str(exc)})
                print(f"  ✗  {d['customer']:<40} {d['device_name']}  ERROR: {exc}")
        else:
            results["skipped"].append(d)
            print(f"  →  {d['customer']:<40} {d['device_name']}  [{existing} → {email}]")

    print(f"\n{prefix}Done. {len(results['updated'])} updated, {len(results['failed'])} failed, {len(results['skipped'])} skipped (dry run).")
    if dry_run:
        print("Re-run with dry_run=False to apply changes.")
    if results["failed"]:
        print("FAILURES:")
        for f in results["failed"]:
            print(f"  {f['device']['device_name']}: {f['error']}")

    refresh_cache()
    return results

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "customer" and len(sys.argv) > 2:
        r = customer_report(" ".join(sys.argv[2:]))
        print(r.get("summary") or r.get("error"))
    elif cmd == "m365":
        print(m365_coverage_report()["summary"])
    elif cmd == "security":
        print(security_report()["summary"])
    elif cmd == "summary":
        print(all_customers_summary()["summary"])
    elif cmd == "flags":
        print(red_flags_report()["summary"])
    elif cmd == "billing":
        print(billing_report()["summary"])
    elif cmd == "customers":
        for c in list_customers():
            print(c)
    elif cmd in ("db-load", "export"):
        # Name is optional — omit it to process the whole estate.
        name = " ".join(sys.argv[2:]) or None
        from cove_dataset import fetch_dataset
        dataset = fetch_dataset(name)

        if cmd == "export":
            out = f"export/{name or 'all'}".replace(" ", "_")
            for path in dataset.to_csv_dir(out):
                print(f"  wrote {path}")
        else:
            from cove_db import save_dataset
            snapshot_id = save_dataset(dataset)
            print(f"snapshot_id = {snapshot_id}  ({dataset.captured_at:%Y-%m-%d %H:%M UTC})")
            for table, count in dataset.summary()["row_counts"].items():
                print(f"  {table:<20} {count:>6} rows")
    elif cmd == "db-customers":
        # Active customers as recorded in PostgreSQL, not as seen by the API.
        from cove_db import fetch_active_customers
        rows = fetch_active_customers()
        for c in rows:
            print(f"  {c['id']:>5}  {c['customer_name']:<45} {c['created_at']:%Y-%m-%d}")
        print(f"\n{len(rows)} active customers")
    elif cmd == "db-load-active":
        # Unattended load: every active customer in cove.customers, one snapshot each.
        from cove_db import load_active_customers
        result = load_active_customers()
        if result["failed"]:
            sys.exit(1)
    elif cmd == "set-report-email" and len(sys.argv) > 2:
        email   = sys.argv[2]
        dry_run = "--apply" not in sys.argv
        update_reporting_email(email, dry_run=dry_run)
    else:
        print("Usage:")
        print("  python cove_agent.py customers                       list all customer names")
        print("  python cove_agent.py customer <name>                 full report for one customer")
        print("  python cove_agent.py m365                            M365 source coverage matrix")
        print("  python cove_agent.py security                        anti-crypto, DR, encryption gaps")
        print("  python cove_agent.py flags                           all red flags across all customers")
        print("  python cove_agent.py summary                         one-line health per customer")
        print("  python cove_agent.py billing                         device counts + M365 items")
        print("  python cove_agent.py db-customers                    list active customers from cove.customers")
        print("  python cove_agent.py db-load [name]                  save to PostgreSQL (all customers if no name)")
        print("  python cove_agent.py db-load-active                  load every active customer, one snapshot each") #use this one
        print("  python cove_agent.py export  [name]                  write the same tables to CSV")
        print("  python cove_agent.py set-report-email <email>        dry-run: show what would change")
        print("  python cove_agent.py set-report-email <email> --apply  apply recipient email to all devices")
