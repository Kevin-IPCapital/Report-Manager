"""
SentinelOne — per-customer metric computation.

Everything derived from raw threat/agent dicts lives here: classification and
engine counts, MITRE tactics, LOLBin matching, MTTR, endpoint health, the risk
score and its band. s1_dataset.py flattens the results into database rows.

The formulas mirror generate_monthly_reports.py section for section, including
its quirks — local-calendar-day bucketing for the daily trend, the 7-day MTTR
sanity window, case-insensitive NT AUTHORITY exclusion. If a weighting changes
in one place it must change in the other (and in Generate-MonthlyReports.ps1).

Entry points:
  compute_customer(site, threats, agents, ...)   all metrics for one customer
  split_by_site(threats, agents)                 group raw records per customer
  risk_band(score)                               score -> CRITICAL/HIGH/MEDIUM/LOW
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re

from s1_client import get

# Living-off-the-land binaries — matched as substrings of threatInfo.threatName.
LOL_BINS = [
    "powershell", "cmd.exe", "wscript", "cscript", "mshta", "rundll32",
    "regsvr32", "certutil", "bitsadmin", "wmic", "msiexec", "psexec",
    "reg.exe", "sc.exe", "net.exe", "osascript", "bash", "sh-thd", "curl",
    "wget", "wevtutil", "nltest", "dsquery", "klist", "mimikatz",
]

# Risk score weights — how many points each finding contributes.
RISK_WEIGHTS = {
    "not_mitigated":    10,
    "infected":         20,
    "ransomware":       15,
    "infostealer":      20,
    "backdoor":         25,
    "lateral_movement": 30,
    "detect_only":       3,
    "health_issues":     5,
    "outdated_agents":   2,
    "stale_agents":      3,
}

RISK_BANDS = [(50, "CRITICAL"), (20, "HIGH"), (5, "MEDIUM"), (0, "LOW")]

STALE_DAYS = 7
# Mitigations claiming to have taken longer than a week are data errors, not
# response times — the console backfills updatedAt on bulk operations.
MTTR_CEILING_MINUTES = 10080


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

def parse_dt(value):
    """Parse a SentinelOne ISO-8601 timestamp into an aware UTC datetime."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)   # Python tops out at microseconds
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def counter_sorted(items) -> list:
    """Group items and return [(name, count), ...] most frequent first."""
    return sorted(Counter(items).items(), key=lambda kv: kv[1], reverse=True)


def risk_band(score: int) -> str:
    for threshold, label in RISK_BANDS:
        if score >= threshold:
            return label
    return "LOW"


# ---------------------------------------------------------------------------
# Field accessors — one place per field, so a schema change is a one-line fix
# ---------------------------------------------------------------------------

def threat_site(t):        return get(t, "agentRealtimeInfo", "siteName")
def threat_site_id(t):     return get(t, "agentRealtimeInfo", "siteId")
def agent_site(a):         return a.get("siteName")
def agent_site_id(a):      return a.get("siteId")


def threat_tactics(threat) -> list:
    """MITRE tactic names across all indicators, duplicates included."""
    names = []
    for ind in get(threat, "indicators", default=[]) or []:
        for tac in (ind.get("tactics") or []):
            if tac.get("name"):
                names.append(tac["name"])
    return names


def is_lolbin(threat) -> bool:
    name = str(get(threat, "threatInfo", "threatName", default="")).lower()
    return any(lb in name for lb in LOL_BINS)


def is_lateral_movement(threat) -> bool:
    """Either the threat name says so, or a MITRE indicator does."""
    name = str(get(threat, "threatInfo", "threatName", default="")).lower()
    return "lateral" in name or "Lateral Movement" in threat_tactics(threat)


def threat_mttr(threat):
    """
    Minutes from detection to last update, for mitigated threats only.

    None when the threat was not mitigated, timestamps are unusable, or the
    interval falls outside the sanity window.
    """
    if get(threat, "threatInfo", "mitigationStatus") != "mitigated":
        return None
    created = parse_dt(get(threat, "threatInfo", "createdAt"))
    updated = parse_dt(get(threat, "threatInfo", "updatedAt"))
    if not (created and updated):
        return None
    minutes = (updated - created).total_seconds() / 60
    return round(minutes, 1) if 0 <= minutes < MTTR_CEILING_MINUTES else None


def detected_on(threat):
    """
    Local calendar day of detection, as a date.

    Local, not UTC: a 23:30Z detection belongs to the next day for a UTC+2
    customer, and the .txt trend has always been read that way.
    """
    created = parse_dt(get(threat, "threatInfo", "createdAt"))
    return created.astimezone().date() if created else None


def agent_health_codes(agent) -> list:
    actions = agent.get("userActionsNeeded") or []
    if isinstance(actions, str):
        return [actions]
    codes = []
    for a in actions:
        if isinstance(a, dict):
            # Newer consoles return [{"action": "...", ...}] instead of plain strings.
            codes.append(a.get("action") or a.get("name") or str(a))
        else:
            codes.append(str(a))
    return [c for c in codes if c]


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def split_by_site(threats: list, agents: list) -> dict:
    """
    Bucket raw records by customer (siteName).

    Returns {site_name: {"threats": [...], "agents": [...], "site_id": str|None}}
    for every site with at least one threat or one agent. Records whose siteName
    is missing are dropped — they cannot be attributed to a customer.
    """
    buckets = defaultdict(lambda: {"threats": [], "agents": [], "site_id": None})

    for t in threats:
        site = threat_site(t)
        if not site:
            continue
        buckets[site]["threats"].append(t)
        buckets[site]["site_id"] = buckets[site]["site_id"] or threat_site_id(t)

    for a in agents:
        site = agent_site(a)
        if not site:
            continue
        buckets[site]["agents"].append(a)
        buckets[site]["site_id"] = buckets[site]["site_id"] or agent_site_id(a)

    # Case-insensitive name order, matching the .txt report and the PowerShell.
    return {name: buckets[name] for name in sorted(buckets, key=str.lower)}


# ---------------------------------------------------------------------------
# Per-customer metrics
# ---------------------------------------------------------------------------

def compute_customer(site: str, threats: list, agents: list,
                     site_id: str = None, now=None) -> dict:
    """
    Every metric for one customer, as a flat dict plus ranked breakdown lists.

    `now` is injectable so a whole run shares one staleness cutoff instead of
    each customer drifting by however long the loop took.
    """
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_DAYS)

    st, ag = threats, agents

    # -- threat volume ------------------------------------------------------
    total = len(st)
    mitigated     = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "mitigated")
    not_mitigated = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "not_mitigated")
    benign        = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "marked_as_benign")
    malicious     = sum(1 for t in st if get(t, "threatInfo", "confidenceLevel") == "malicious")
    suspicious    = sum(1 for t in st if get(t, "threatInfo", "confidenceLevel") == "suspicious")
    # No threats is a clean month, not a divide-by-zero — the .txt reports 100%.
    mitig_rate = round(mitigated / total * 100, 1) if total else 100.0

    # -- classifications ----------------------------------------------------
    classes = counter_sorted(
        c for c in (get(t, "threatInfo", "classification", default="") for t in st) if c
    )
    by_class = dict(classes)

    # -- engines ------------------------------------------------------------
    behavioral = sum(1 for t in st if get(t, "threatInfo", "detectionType") == "dynamic")
    static     = sum(1 for t in st if get(t, "threatInfo", "detectionType") == "static")

    # -- MITRE --------------------------------------------------------------
    tactic_counts = Counter()
    for t in st:
        for name in set(threat_tactics(t)):     # count each threat once per tactic
            tactic_counts[name] += 1
    tactics = sorted(tactic_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    lateral = sum(1 for t in st if is_lateral_movement(t))

    # -- LOLBins ------------------------------------------------------------
    lol = [t for t in st if is_lolbin(t)]
    lol_top = counter_sorted(get(t, "threatInfo", "threatName", default="") for t in lol)[:5]

    # -- rankings -----------------------------------------------------------
    top_threats = counter_sorted(get(t, "threatInfo", "threatName", default="") for t in st)[:8]
    top_devices = counter_sorted(
        get(t, "agentRealtimeInfo", "agentComputerName", default="") for t in st
    )[:8]
    top_users = counter_sorted(
        u for u in (get(t, "threatInfo", "processUser") for t in st)
        if u and not str(u).upper().startswith("NT AUTHORITY")
    )[:5]

    # -- MTTR ---------------------------------------------------------------
    rtimes = [m for m in (threat_mttr(t) for t in st) if m is not None]
    avg_mttr = round(sum(rtimes) / len(rtimes), 1) if rtimes else None

    # -- daily trend --------------------------------------------------------
    daily = defaultdict(int)
    for t in st:
        day = detected_on(t)
        if day:
            daily[day] += 1
    daily_trend = sorted(daily.items())

    # -- endpoint health ----------------------------------------------------
    def last_active(a):
        return parse_dt(a.get("lastActiveDate"))

    devices        = len(ag)
    infected       = sum(1 for a in ag if a.get("infected") is True)
    outdated       = sum(1 for a in ag if a.get("isUpToDate") is False)
    stale          = sum(1 for a in ag if last_active(a) and last_active(a) < stale_cutoff)
    pending_unin   = sum(1 for a in ag if a.get("isPendingUninstall") is True)
    detect_only    = sum(1 for a in ag if a.get("mitigationMode") == "detect")
    protect_mode   = sum(1 for a in ag if a.get("mitigationMode") == "protect")
    health_issues  = sum(1 for a in ag if a.get("userActionsNeeded"))
    scan_aborted   = sum(1 for a in ag if a.get("scanStatus") == "aborted")
    os_spread      = counter_sorted(a.get("osName") or "" for a in ag)
    version_spread = counter_sorted(a.get("agentVersion") or "" for a in ag)

    # -- risk ---------------------------------------------------------------
    contributions = {
        "not_mitigated":    not_mitigated,
        "infected":         infected,
        "ransomware":       by_class.get("Ransomware", 0),
        "infostealer":      by_class.get("Infostealer", 0),
        "backdoor":         by_class.get("Backdoor", 0),
        "lateral_movement": lateral,
        "detect_only":      detect_only,
        "health_issues":    health_issues,
        "outdated_agents":  outdated,
        "stale_agents":     stale,
    }
    risk_score = sum(count * RISK_WEIGHTS[key] for key, count in contributions.items())

    return {
        "customer_name":     site,
        "site_id":           site_id,

        "threats":           total,
        "mitigated":         mitigated,
        "not_mitigated":     not_mitigated,
        "marked_benign":     benign,
        "malicious":         malicious,
        "suspicious":        suspicious,
        "mitigation_rate":   mitig_rate,

        "ransomware":        by_class.get("Ransomware", 0),
        "infostealer":       by_class.get("Infostealer", 0),
        "backdoor":          by_class.get("Backdoor", 0),
        "pua":               by_class.get("PUA", 0),
        "lateral_movement":  lateral,
        "lolbin_detections": len(lol),

        "behavioral_ai":     behavioral,
        "static_ai":         static,

        "avg_mttr_minutes":  avg_mttr,

        "devices":           devices,
        "infected":          infected,
        "outdated_agents":   outdated,
        "stale_agents":      stale,
        "pending_uninstall": pending_unin,
        "detect_only":       detect_only,
        "protect_mode":      protect_mode,
        "health_issues":     health_issues,
        "scan_aborted":      scan_aborted,

        "risk_score":        risk_score,
        "risk_level":        risk_band(risk_score),

        # ranked breakdowns — lists of (name, count)
        "classifications":   classes,
        "tactics":           tactics,
        "top_threats":       top_threats,
        "top_devices":       top_devices,
        "top_users":         top_users,
        "lolbin_top":        lol_top,
        "daily_trend":       daily_trend,
        "os_spread":         os_spread,
        "version_spread":    version_spread,
    }
