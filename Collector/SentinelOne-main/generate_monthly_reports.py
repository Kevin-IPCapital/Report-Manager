#!/usr/bin/env python3
"""
SentinelOne Monthly Customer Security Report Generator
IP Dimension - MSP EDR Reporting

Covers: Threats, Classifications, MITRE ATT&CK, MTTR, Endpoint Health,
        Protection Mode, Agent Health Issues, LOLBins, Daily Trend, MoM, Users

Python port of Generate-MonthlyReports.ps1

Period: defaults to the current month, from the 1st through the end of today.
A run on 27 July covers 1-27 July and says so; it does not claim 31 days. Pass
--month for a specific (usually completed) month. Endpoint data is always
current whatever the month, because the API keeps no agent history.

Usage:
    python generate_monthly_reports.py                        # current month to date
    python generate_monthly_reports.py --month 2026-06        # a specific month
    python generate_monthly_reports.py --month 2026-05 --report-dir ./Reports

    # one customer only — filters the API server-side
    python generate_monthly_reports.py --month 2026-06 --customer "Acme Holdings"

    # also write everything to the `sentinelone` PostgreSQL schema
    python generate_monthly_reports.py --month 2026-06 --customer "Acme" --db

    # one run per active customer in sentinelone.customers
    python generate_monthly_reports.py --month 2026-06 --active --db

See README.md for the schema and the s1_client/s1_metrics/s1_dataset/s1_db layer.
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Window resolution is shared with the database layer so the .txt and the
# snapshot rows can never disagree about which days a report covers.
from s1_dataset import current_month, month_window

SCRIPT_DIR = Path(__file__).resolve().parent


# ── Helpers ──────────────────────────────────────────────────────────────────
def load_env(env_file):
    """Read a simple KEY=VALUE .env file (mirrors the PowerShell parsing)."""
    values = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip()
    return values


def add_months(dt, months):
    """Add (or subtract) whole months to a datetime, clamping the day."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month, day=1)


def parse_dt(value):
    """Parse a SentinelOne ISO-8601 timestamp into an aware UTC datetime."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim fractional seconds to 6 digits (Python's max) if longer
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get(obj, *path, default=None):
    """Safely walk a nested dict path."""
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur and cur[key] is not None:
            cur = cur[key]
        else:
            return default
    return cur


# ── SentinelOne API ────────────────────────────────────────────────────────────
class S1Client:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"ApiToken {token}",
                "Content-Type": "application/json",
            }
        )

    def _get(self, url):
        resp = self.session.get(url, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def get_paged(self, url):
        """Follow pagination cursors and collect all data records."""
        all_data = []
        cursor = None
        while True:
            u = f"{url}&cursor={cursor}" if cursor else url
            r = self._get(u)
            all_data.extend(r.get("data") or [])
            cursor = get(r, "pagination", "nextCursor")
            if not cursor:
                break
        return all_data

    def get_count(self, url):
        r = self._get(url)
        return get(r, "pagination", "totalItems", default=0)


# ── Table formatting (mirrors Format-Table -AutoSize) ──────────────────────────
def format_table(rows, columns):
    """Render a list of dict rows as an aligned text table."""
    if not rows:
        return ""
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    header = " ".join(c.ljust(widths[c]) for c in columns)
    underline = " ".join("-" * widths[c] for c in columns)
    lines = [header, underline]
    for row in rows:
        lines.append(" ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
    return "\n".join(lines)


def fmt_num(value):
    """Format a rounded number the way PowerShell does: 100.0 -> '100'."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def counter_sorted(items):
    """Group items and return [(name, count), ...] sorted by count desc."""
    counts = Counter(items)
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


# ── Main ────────────────────────────────────────────────────────────────────────
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="SentinelOne Monthly Customer Security Report Generator"
    )
    parser.add_argument(
        "--month",
        default=None,
        help="Report month, e.g. 2026-06. Defaults to the current month, covering "
             "the 1st through the end of today.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(SCRIPT_DIR / "Reports"),
        help="Output directory for reports",
    )

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--customer",
        help="Report on one customer only, by site name (exact or unique partial "
             "match). Filters the API server-side, so the run pulls only that "
             "customer's data.",
    )
    scope.add_argument(
        "--site-id",
        help="Report on one customer only, by SentinelOne site id. Skips name "
             "resolution.",
    )
    scope.add_argument(
        "--active",
        action="store_true",
        help="One run per active customer in sentinelone.customers (requires a DB "
             "connection). Reports and DB rows are produced per customer.",
    )

    parser.add_argument(
        "--db",
        action="store_true",
        help="Also write everything to the sentinelone PostgreSQL schema.",
    )
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Skip the .txt output — useful with --db when only the database matters.",
    )
    parser.add_argument("--notes", help="Free-text note stored on the snapshot row.")
    return parser.parse_args(argv)


def run_month(args, customer=None, site_id=None):
    """
    Generate reports for one month, optionally scoped to a single customer.

    Returns {"customers": n, "threats": n, "agents": n, "snapshot_id": int|None}.
    """
    report_dir = Path(args.report_dir)

    env = load_env(SCRIPT_DIR / ".env")
    token = env.get("SENTINELONE_API_TOKEN", "")
    base_url = env.get("SENTINELONE_BASE_URL", "")
    if not token or not base_url:
        sys.exit("ERROR: SENTINELONE_API_TOKEN and SENTINELONE_BASE_URL must be set in .env")

    # One `now` for the whole run: the window, the staleness cutoff and the
    # snapshot's captured_at all have to agree, or a long run drifts across days.
    now = datetime.now(timezone.utc)

    # Default to the month in progress, covering the 1st through the end of today.
    win = month_window(args.month or current_month(), as_of=now)
    month = win.start.strftime("%Y-%m")
    month_start, month_end = win.start_iso, win.end_iso
    month_label = win.label
    period_label = win.span_label

    # Month-over-month has to compare like with like. While a month is still
    # running, the earlier months are cut to the same day so 27 days of July is
    # measured against 27 days of June, not against all 30.
    through = win.through_day if win.is_partial else None
    month_first = win.start

    prev_win = month_window(
        add_months(month_first, -1).strftime("%Y-%m"), as_of=now, through_day=through
    )
    prev2_win = month_window(
        add_months(month_first, -2).strftime("%Y-%m"), as_of=now, through_day=through
    )

    report_dir.mkdir(parents=True, exist_ok=True)

    client = S1Client(base_url, token)

    # A site filter narrows every threat query, including the two month-over-month
    # counts, so a single-customer run's trend line is that customer's own trend.
    site_filter = f"&siteIds={site_id}" if site_id else ""
    scope_label = f" — {customer}" if customer else (f" — site {site_id}" if site_id else "")

    print(f"[{period_label}{scope_label}] Fetching data...")

    print("  Loading threats...")
    all_threats = client.get_paged(
        f"{base_url}/web/api/v2.1/threats?limit=100"
        f"&createdAt__gte={month_start}&createdAt__lte={month_end}"
        f"&sortBy=createdAt&sortOrder=desc{site_filter}"
    )

    print("  Loading agents...")
    all_agents = client.get_paged(
        f"{base_url}/web/api/v2.1/agents?limit=100{site_filter}"
    )

    print("  Getting latest agent version and MoM counts...")
    pkg_resp = client._get(
        f"{base_url}/web/api/v2.1/update/agent/packages?limit=1"
        f"&sortBy=version&sortOrder=desc&osTypes=windows&fileExtension=.msi&status=ga"
    )
    pkg_data = pkg_resp.get("data") or []
    latest_pkg = pkg_data[0].get("version") if pkg_data else "N/A"

    prev_count = client.get_count(
        f"{base_url}/web/api/v2.1/threats?limit=1"
        f"&createdAt__gte={prev_win.start_iso}&createdAt__lte={prev_win.end_iso}"
        f"{site_filter}"
    )
    prev2_count = client.get_count(
        f"{base_url}/web/api/v2.1/threats?limit=1"
        f"&createdAt__gte={prev2_win.start_iso}&createdAt__lte={prev2_win.end_iso}"
        f"{site_filter}"
    )

    print(
        f"  Done. Threats: {len(all_threats)}  Agents: {len(all_agents)}  "
        f"Latest: {latest_pkg}"
    )
    print()

    # LOLBins list
    lol_bin_names = [
        "powershell", "cmd.exe", "wscript", "cscript", "mshta", "rundll32",
        "regsvr32", "certutil", "bitsadmin", "wmic", "msiexec", "psexec",
        "reg.exe", "sc.exe", "net.exe", "osascript", "bash", "sh-thd", "curl",
        "wget", "wevtutil", "nltest", "dsquery", "klist", "mimikatz",
    ]

    # ── Per-customer reports ────────────────────────────────────────────────────
    agent_sites = {get(a, "siteName") for a in all_agents if get(a, "siteName")}
    threat_sites = {
        get(t, "agentRealtimeInfo", "siteName")
        for t in all_threats
        if get(t, "agentRealtimeInfo", "siteName")
    }
    # Case-insensitive ordering, matching PowerShell's Sort-Object -Unique.
    all_customers = sorted(agent_sites | threat_sites, key=str.lower)

    summary_rows = []
    report_paths = {}
    stale_cutoff = now - timedelta(days=7)

    for site in all_customers:
        st = [t for t in all_threats if get(t, "agentRealtimeInfo", "siteName") == site]
        ag = [a for a in all_agents if get(a, "siteName") == site]
        if not ag and not st:
            continue

        # ── Threat metrics ───────────────────────────────────────────────────────
        total = len(st)
        mitigated = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "mitigated")
        not_mitigated = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "not_mitigated")
        benign = sum(1 for t in st if get(t, "threatInfo", "mitigationStatus") == "marked_as_benign")
        malicious = sum(1 for t in st if get(t, "threatInfo", "confidenceLevel") == "malicious")
        suspicious = sum(1 for t in st if get(t, "threatInfo", "confidenceLevel") == "suspicious")
        mitig_rate = fmt_num(round(mitigated / total * 100, 1)) if total > 0 else "100"

        # ── Classification breakdown ──────────────────────────────────────────────
        class_break = counter_sorted(
            get(t, "threatInfo", "classification", default="") for t in st
        )
        class_break = [(n, c) for (n, c) in class_break if n]
        ransomware = sum(1 for t in st if get(t, "threatInfo", "classification") == "Ransomware")
        infostealer = sum(1 for t in st if get(t, "threatInfo", "classification") == "Infostealer")
        backdoor = sum(1 for t in st if get(t, "threatInfo", "classification") == "Backdoor")
        pua = sum(1 for t in st if get(t, "threatInfo", "classification") == "PUA")

        # ── Detection engines ─────────────────────────────────────────────────────
        behavioral = sum(1 for t in st if get(t, "threatInfo", "detectionType") == "dynamic")
        static = sum(1 for t in st if get(t, "threatInfo", "detectionType") == "static")

        # ── MITRE tactics ─────────────────────────────────────────────────────────
        def threat_tactic_names(threat):
            names = []
            for ind in get(threat, "indicators", default=[]) or []:
                for tac in (ind.get("tactics") or []):
                    if tac.get("name"):
                        names.append(tac["name"])
            return names

        tactics = sorted({name for t in st for name in threat_tactic_names(t)})

        lateral_mov = sum(
            1
            for t in st
            if (
                "lateral" in str(get(t, "threatInfo", "threatName", default="")).lower()
                or "Lateral Movement" in threat_tactic_names(t)
            )
        )

        # ── LOLBins / Living-off-the-Land ─────────────────────────────────────────
        def is_lolbin(threat):
            n = str(get(threat, "threatInfo", "threatName", default="")).lower()
            return any(lb in n for lb in lol_bin_names)

        lol_detections = [t for t in st if is_lolbin(t)]
        lol_count = len(lol_detections)
        lol_top = counter_sorted(
            get(t, "threatInfo", "threatName", default="") for t in lol_detections
        )[:5]

        # ── Top threats, devices, users ───────────────────────────────────────────
        top_threats = counter_sorted(
            get(t, "threatInfo", "threatName", default="") for t in st
        )[:8]
        top_devices = counter_sorted(
            get(t, "agentRealtimeInfo", "agentComputerName", default="") for t in st
        )[:8]
        # PowerShell's -notmatch is case-insensitive, so upper() before comparing.
        top_users = counter_sorted(
            get(t, "threatInfo", "processUser")
            for t in st
            if get(t, "threatInfo", "processUser")
            and not str(get(t, "threatInfo", "processUser")).upper().startswith("NT AUTHORITY")
        )[:5]
        unresolved = [t for t in st if get(t, "threatInfo", "mitigationStatus") == "not_mitigated"]

        # ── MTTR ──────────────────────────────────────────────────────────────────
        rtimes = []
        for t in st:
            if get(t, "threatInfo", "mitigationStatus") != "mitigated":
                continue
            created = parse_dt(get(t, "threatInfo", "createdAt"))
            updated = parse_dt(get(t, "threatInfo", "updatedAt"))
            if created and updated:
                diff = (updated - created).total_seconds() / 60
                if 0 <= diff < 10080:
                    rtimes.append(diff)
        avg_mttr = fmt_num(round(sum(rtimes) / len(rtimes), 1)) if rtimes else "N/A"

        # ── Daily trend ───────────────────────────────────────────────────────────
        # Bucket by local calendar day, as Get-Date -Format does: a 23:30Z
        # detection belongs to the next day for a UTC+2 customer.
        daily_counts = defaultdict(int)
        for t in st:
            created = parse_dt(get(t, "threatInfo", "createdAt"))
            if created:
                daily_counts[created.astimezone().strftime("%Y-%m-%d")] += 1
        daily_trend = sorted(daily_counts.items())

        # ── Endpoint health ───────────────────────────────────────────────────────
        total_ag = len(ag)
        infected_ag = sum(1 for a in ag if a.get("infected") is True)
        outdated_ag = sum(1 for a in ag if a.get("isUpToDate") is False)
        stale_ag = sum(
            1
            for a in ag
            if a.get("lastActiveDate")
            and parse_dt(a.get("lastActiveDate"))
            and parse_dt(a.get("lastActiveDate")) < stale_cutoff
        )
        pending_unin = sum(1 for a in ag if a.get("isPendingUninstall") is True)
        detect_only = sum(1 for a in ag if a.get("mitigationMode") == "detect")
        protect_mode = sum(1 for a in ag if a.get("mitigationMode") == "protect")
        health_issues = sum(1 for a in ag if a.get("userActionsNeeded"))
        scan_aborted = sum(1 for a in ag if a.get("scanStatus") == "aborted")
        # `or ""` not a default: the keys exist with null values on some agents,
        # and None breaks the aligned format specs below.
        os_spread = counter_sorted(a.get("osName") or "" for a in ag)
        version_spread = counter_sorted(a.get("agentVersion") or "" for a in ag)

        # ── Risk score ────────────────────────────────────────────────────────────
        risk_score = 0
        risk_score += not_mitigated * 10
        risk_score += infected_ag * 20
        risk_score += ransomware * 15
        risk_score += infostealer * 20
        risk_score += backdoor * 25
        risk_score += lateral_mov * 30
        risk_score += detect_only * 3
        risk_score += health_issues * 5
        risk_score += outdated_ag * 2
        risk_score += stale_ag * 3
        if risk_score >= 50:
            risk_level = "CRITICAL"
        elif risk_score >= 20:
            risk_level = "HIGH"
        elif risk_score >= 5:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        summary_rows.append(
            {
                "Customer": site,
                "Risk": risk_level,
                "Threats": total,
                "MitigRate": f"{mitig_rate}%",
                "Unresolved": not_mitigated,
                "Ransomware": ransomware,
                "Infostealer": infostealer,
                "LateralMov": lateral_mov,
                "Devices": total_ag,
                "Infected": infected_ag,
                "DetectOnly": detect_only,
                "HealthIssues": health_issues,
                "Outdated": outdated_ag,
                "Stale7d": stale_ag,
                "MTTR_min": avg_mttr,
            }
        )

        # ══════════════════════════════════════════════════════════════════════════
        # BUILD REPORT
        # ══════════════════════════════════════════════════════════════════════════
        L = []
        sep = "=" * 72
        div = "-" * 72

        L.append(sep)
        L.append("  IP DIMENSION - MONTHLY SECURITY REPORT")
        L.append(f"  Customer  : {site}")
        L.append(f"  Period    : {period_label}")
        L.append(f"  Generated : {datetime.now().strftime('%d %b %Y %H:%M')}")
        L.append(f"  Risk Level: {risk_level}")
        L.append(sep)
        L.append("")

        # 1. Executive Summary
        L.append("1. EXECUTIVE SUMMARY")
        L.append(div)
        if total == 0 and detect_only == 0 and health_issues == 0 and outdated_ag == 0:
            L.append("   No threats detected. All endpoints healthy. Security posture: GOOD.")
        else:
            if total > 0:
                L.append(
                    f"   {total} threat(s) detected this month. "
                    f"{mitigated} auto-mitigated ({mitig_rate}% rate)."
                )
            if not_mitigated > 0:
                L.append(f"   !! {not_mitigated} threat(s) UNRESOLVED - immediate action required.")
            if ransomware > 0:
                L.append(f"   !! {ransomware} Ransomware detection(s) detected.")
            if infostealer > 0:
                L.append(f"   !! {infostealer} Infostealer detection(s) - credential/data theft risk.")
            if backdoor > 0:
                L.append(f"   !! {backdoor} Backdoor detection(s) - possible persistent access.")
            if lateral_mov > 0:
                L.append(f"   !! {lateral_mov} Lateral Movement - attacker may be moving within network.")
            if infected_ag > 0:
                L.append(f"   !! {infected_ag} device(s) currently marked as infected.")
            if detect_only > 0:
                L.append(f"   !! {detect_only} device(s) in DETECT-ONLY mode - threats will NOT be blocked automatically.")
            if health_issues > 0:
                L.append(f"   !  {health_issues} device(s) have agent health issues (modules not running).")
            if outdated_ag > 0:
                L.append(f"   !  {outdated_ag} device(s) running outdated SentinelOne agent.")
        L.append("")

        # 2. Threat Statistics
        L.append("2. THREAT STATISTICS")
        L.append(div)
        L.append(f"   Total Detected            : {total}")
        L.append(f"   Auto-Mitigated            : {mitigated}  ({mitig_rate}%)")
        L.append(f"   Unresolved                : {not_mitigated}")
        L.append(f"   Marked as Benign          : {benign}")
        L.append(f"   Mean Time to Respond      : {avg_mttr} minutes (auto-mitigation avg)")
        L.append("")
        L.append("   By Confidence Level:")
        L.append(f"     Malicious               : {malicious}")
        L.append(f"     Suspicious              : {suspicious}")
        L.append("")
        L.append("   By Detection Engine:")
        L.append(f"     Behavioral AI (dynamic) : {behavioral}")
        L.append(f"     Static Analysis         : {static}")
        L.append("")
        if class_break:
            L.append("   By Classification:")
            for name, count in class_break:
                L.append(f"     {name:<28}: {count}")
            L.append("")

        # 3. High-Risk Threat Categories
        L.append("3. HIGH-RISK THREAT CATEGORIES")
        L.append(div)
        if ransomware > 0 or infostealer > 0 or backdoor > 0 or lateral_mov > 0 or lol_count > 0:
            if ransomware > 0:
                L.append(f"   [!!] RANSOMWARE       : {ransomware}  - verify backups, review affected devices")
            if infostealer > 0:
                L.append(f"   [!!] INFOSTEALER      : {infostealer}  - change passwords, check for data exfiltration")
            if backdoor > 0:
                L.append(f"   [!!] BACKDOOR         : {backdoor}  - verify all remote access tools are authorized")
            if lateral_mov > 0:
                L.append(f"   [!!] LATERAL MOVEMENT : {lateral_mov}  - review network segmentation")
            if lol_count > 0:
                L.append(f"   [!]  LOLBins/Built-in Tools: {lol_count} detection(s) using OS-native tools")
                for name, count in lol_top:
                    L.append(f"         {count:>4}x  {name}")
            if pua > 0:
                L.append(f"        PUA/Adware        : {pua}  - confirm user awareness")
        else:
            L.append("   No high-risk threat categories detected this period.")
        L.append("")

        # 4. MITRE ATT&CK
        L.append("4. MITRE ATT&CK TACTICS OBSERVED")
        L.append(div)
        if tactics:
            for t in tactics:
                L.append(f"   - {t}")
        else:
            L.append("   None detected.")
        L.append("")

        # 5. Top Threats
        L.append("5. TOP THREATS BY FREQUENCY")
        L.append(div)
        if top_threats:
            for name, count in top_threats:
                L.append(f"   {count:>5}x  {name}")
        else:
            L.append("   No threats this period.")
        L.append("")

        # 6. Most Affected Devices
        L.append("6. MOST AFFECTED DEVICES")
        L.append(div)
        if top_devices:
            for name, count in top_devices:
                L.append(f"   {count:>5}x  {name}")
        else:
            L.append("   No affected devices.")
        L.append("")

        # 7. User Accounts Involved
        if top_users:
            L.append("7. USER ACCOUNTS INVOLVED IN THREATS")
            L.append(div)
            for name, count in top_users:
                L.append(f"   {count:>5}x  {name}")
            L.append("")

        # 8. Daily Threat Trend
        if daily_trend:
            L.append(f"8. DAILY THREAT TREND - {month_label}")
            L.append(div)
            max_day = max(c for _, c in daily_trend)
            for date, count in daily_trend:
                if max_day > 0:
                    bar_len = min(int(count // max(max_day / 40, 1)), 40)
                else:
                    bar_len = 0
                bar = "#" * bar_len
                L.append(f"   {date}  {count:>4}  {bar}")
            L.append("")

        # 9. Endpoint Health
        L.append("9. ENDPOINT HEALTH & COVERAGE")
        L.append(div)
        L.append(f"   Total Managed Devices     : {total_ag}")
        coverage_pct = "100%" if total_ag > 0 else "0%"
        L.append(f"   Agent Coverage            : {coverage_pct}")
        L.append(f"   Protect Mode (auto-block) : {protect_mode}")
        detect_note = " !! Threats will NOT be blocked automatically" if detect_only > 0 else ""
        L.append(f"   Detect-Only Mode          : {detect_only}{detect_note}")
        L.append(f"   Infected Devices          : {infected_ag}")
        L.append(f"   Outdated Agent Version    : {outdated_ag}  (Latest: {latest_pkg})")
        L.append(f"   Agent Health Issues       : {health_issues}")
        L.append(f"   Not Seen in 7+ Days       : {stale_ag}")
        L.append(f"   Pending Uninstall         : {pending_unin}")
        L.append(f"   Scan Aborted              : {scan_aborted}")
        L.append("")

        if ag:
            L.append("   OS Distribution:")
            for name, count in os_spread:
                L.append(f"     {name:<42}: {count}")
            L.append("")
            L.append("   Agent Version Distribution:")
            for name, count in version_spread:
                update_note = (
                    "  [UPDATE AVAILABLE]"
                    if name != latest_pkg and str(name) < str(latest_pkg)
                    else ""
                )
                L.append(f"     {name:<22}: {count}{update_note}")
            L.append("")

        if detect_only > 0:
            L.append("   DETECT-ONLY DEVICES (no automatic blocking):")
            for a in ag:
                if a.get("mitigationMode") == "detect":
                    L.append(f"     [DETECT] {a.get('computerName')} [{a.get('osName')}]")
            L.append("")

        if health_issues > 0:
            L.append("   AGENT HEALTH ISSUES:")
            for a in ag:
                if a.get("userActionsNeeded"):
                    issues = ", ".join(str(x) for x in a.get("userActionsNeeded"))
                    L.append(f"     [HEALTH] {a.get('computerName')} - {issues}")
            L.append("")

        if outdated_ag > 0:
            L.append("   OUTDATED AGENT DEVICES:")
            for a in ag:
                if a.get("isUpToDate") is False:
                    L.append(
                        f"     [UPDATE] {a.get('computerName')} - "
                        f"v{a.get('agentVersion')} on {a.get('osName')}"
                    )
            L.append("")

        if infected_ag > 0:
            L.append("   INFECTED DEVICES:")
            for a in ag:
                if a.get("infected") is True:
                    L.append(
                        f"     [INFECTED] {a.get('computerName')} [{a.get('osName')}] "
                        f"last seen {a.get('lastActiveDate')}"
                    )
            L.append("")

        if stale_ag > 0:
            L.append("   DEVICES NOT REPORTING (>7 days):")
            stale_list = [
                a
                for a in ag
                if a.get("lastActiveDate")
                and parse_dt(a.get("lastActiveDate"))
                and parse_dt(a.get("lastActiveDate")) < stale_cutoff
            ]
            stale_list.sort(key=lambda a: parse_dt(a.get("lastActiveDate")))
            for a in stale_list:
                L.append(f"     [STALE] {a.get('computerName')} - last seen {a.get('lastActiveDate')}")
            L.append("")

        # 10. Unresolved Threats
        if unresolved:
            L.append("10. UNRESOLVED THREATS - IMMEDIATE ACTION REQUIRED")
            L.append(div)
            unresolved_sorted = sorted(
                unresolved,
                key=lambda t: str(get(t, "threatInfo", "createdAt", default="")),
                reverse=True,
            )
            for t in unresolved_sorted:
                created = str(get(t, "threatInfo", "createdAt", default=""))
                date = created.split("T")[0]
                name = get(t, "threatInfo", "threatName")
                dev = get(t, "agentRealtimeInfo", "agentComputerName")
                conf = get(t, "threatInfo", "confidenceLevel")
                cls = get(t, "threatInfo", "classification")
                user = get(t, "threatInfo", "processUser")
                L.append(f"   [{date}] [{conf}] {name}")
                L.append(f"            Device: {dev}  |  Class: {cls}  |  User: {user}")
            L.append("")

        # 11. Recommendations
        L.append("11. RECOMMENDATIONS & ACTION ITEMS")
        L.append(div)
        has_issue = False
        if not_mitigated > 0:
            L.append(f"   [URGENT] Investigate and resolve {not_mitigated} unresolved threat(s).")
            has_issue = True
        if infected_ag > 0:
            L.append(f"   [URGENT] Isolate and fully remediate {infected_ag} infected device(s).")
            has_issue = True
        if infostealer > 0:
            L.append(f"   [URGENT] {infostealer} Infostealer(s) found - change all account passwords, audit data access logs.")
            has_issue = True
        if ransomware > 0:
            L.append(f"   [HIGH]   {ransomware} Ransomware detection(s) - verify backups are complete and tested.")
            has_issue = True
        if lateral_mov > 0:
            L.append(f"   [HIGH]   {lateral_mov} Lateral Movement event(s) - segment network, audit admin accounts.")
            has_issue = True
        if backdoor > 0:
            L.append(f"   [HIGH]   {backdoor} Backdoor(s) detected - audit all remote access and admin tools.")
            has_issue = True
        if detect_only > 0:
            L.append(f"   [HIGH]   {detect_only} device(s) in detect-only mode - switch to protect mode to enable automatic blocking.")
            has_issue = True
        if health_issues > 0:
            L.append(f"   [HIGH]   {health_issues} device(s) have agent health issues (Network Extension/Content Filter not active).")
            has_issue = True
        if outdated_ag > 0:
            L.append(f"   [MEDIUM] Update SentinelOne agent on {outdated_ag} device(s) to latest version {latest_pkg}.")
            has_issue = True
        if stale_ag > 0:
            L.append(f"   [MEDIUM] {stale_ag} device(s) not reporting in 7+ days - verify powered on and connected.")
            has_issue = True
        if scan_aborted > 0:
            L.append(f"   [LOW]    {scan_aborted} device(s) with aborted scan - re-run scan.")
            has_issue = True
        if pua > 0:
            L.append(f"   [LOW]    {pua} PUA/Adware detection(s) - review with user, consider policy update.")
            has_issue = True
        if not has_issue:
            L.append("   [OK]     No issues detected. Security posture is healthy.")
        L.append("")

        L.append(sep)
        L.append("   Report generated by IP Dimension Managed Security Services")
        L.append("   Questions: security@ipdimension.co.za")
        L.append(sep)

        safe_name = re.sub(r'[\\/:*?"<>|]', "_", site)
        out_path = report_dir / f"{safe_name}_{month}.txt"
        if not args.no_reports:
            out_path.write_text("\n".join(L), encoding="utf-8")
        report_paths[site] = str(out_path)
        print(f"  [OK] {site.ljust(35)} Risk: {risk_level.ljust(8)} Threats: {total}")

    # ── Master summary ──────────────────────────────────────────────────────────
    # Each window states the days it covers when the comparison is not full
    # months, so a mid-month drop is not mistaken for an improvement.
    def trend_part(w, count):
        span = f" (1-{w.through_day})" if w.is_partial else ""
        return f"{w.label}{span} : {count}"

    mom_line = (
        f"  {trend_part(prev2_win, prev2_count)}  |  "
        f"{trend_part(prev_win, prev_count)}  |  "
        f"{trend_part(win, len(all_threats))}"
    )
    total_devices = len(all_agents)
    detect_total = sum(1 for a in all_agents if a.get("mitigationMode") == "detect")
    health_total = sum(1 for a in all_agents if a.get("userActionsNeeded"))

    risk_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    summary_sorted = sorted(
        summary_rows,
        key=lambda r: (risk_order.get(r["Risk"], 4), -r["Threats"]),
    )

    columns = [
        "Customer", "Risk", "Threats", "MitigRate", "Unresolved", "Ransomware",
        "Infostealer", "LateralMov", "Devices", "Infected", "DetectOnly",
        "HealthIssues", "Outdated", "Stale7d", "MTTR_min",
    ]
    table = format_table(summary_sorted, columns)

    print()
    print(f"MASTER SUMMARY - {period_label}")
    print(mom_line)
    print("=" * 120)
    print(table)

    master_lines = []
    master_lines.append("IP DIMENSION - MASTER SECURITY SUMMARY")
    master_lines.append(
        f"Period    : {period_label}  |  "
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"
    )
    master_lines.append(
        f"Portfolio : {len(all_customers)} customers  |  {total_devices} total devices  |  "
        f"{detect_total} in detect-only mode  |  {health_total} with health issues"
    )
    master_lines.append(f"Trend     : {mom_line}")
    master_lines.append("=" * 120)
    master_lines.append(table)
    # A scoped run must not overwrite the portfolio-wide summary with one row.
    if customer or site_id:
        scope_slug = re.sub(r'[\\/:*?"<>|]', "_", customer or f"site_{site_id}")
        master_path = report_dir / f"_SUMMARY_{scope_slug}_{month}.txt"
    else:
        master_path = report_dir / f"_MASTER_SUMMARY_{month}.txt"
    if not args.no_reports:
        master_path.write_text("\n".join(master_lines), encoding="utf-8")

    # ── Database ────────────────────────────────────────────────────────────────
    snapshot_id = None
    if args.db:
        # Imported here so the report generator keeps working without psycopg2
        # installed, for anyone who only wants the .txt files.
        import s1_db
        from s1_dataset import build_dataset

        print("\n  Writing to PostgreSQL...")
        # Same `win` the API was queried with, so snapshot.month_end records the
        # period actually covered rather than a freshly recomputed one.
        dataset = build_dataset(
            month, all_threats, all_agents,
            customer_filter=customer, site_id_filter=site_id,
            latest_agent_version=latest_pkg, captured_at=now, window=win,
        )
        for site, path in report_paths.items():
            dataset.set_report_path(site, path)

        snapshot_id = s1_db.save_dataset(dataset, notes=args.notes)
        counts = ", ".join(
            f"{n}={len(t)}" for n, t in dataset.tables.items() if len(t)
        )
        print(f"  [OK] snapshot {snapshot_id} -> {s1_db.SCHEMA_NAME}  "
              f"period {dataset.period_label}  ({counts})")

    print()
    if args.no_reports:
        print("Reports skipped (--no-reports).")
    else:
        print(f"Reports saved to: {report_dir}")
    print(f"Period covered: {period_label}")
    print("Run for other months: python generate_monthly_reports.py --month 2026-05")

    return {
        "customers":   len(summary_rows),
        "threats":     len(all_threats),
        "agents":      len(all_agents),
        "snapshot_id": snapshot_id,
    }


def main():
    args = parse_args()

    if args.active:
        # The roster in sentinelone.customers decides who gets a report, and each
        # customer is a separate run so one failure does not sink the rest.
        import s1_db

        customers = s1_db.fetch_active_customers()
        print(f"{len(customers)} active customers in {s1_db.SCHEMA_NAME}.customers\n")

        failed = []
        for c in customers:
            try:
                run_month(args, customer=c["customer_name"], site_id=c["site_id"])
            except Exception as exc:
                failed.append((c["customer_name"], exc))
                print(f"  [FAIL] {c['customer_name']}: {exc}")

        print(f"\nDone. {len(customers) - len(failed)} succeeded, {len(failed)} failed.")
        return 1 if failed else 0

    site_id = args.site_id
    customer = args.customer
    if customer and not site_id:
        # Resolve through the roster when the DB is in play, otherwise via /sites.
        if args.db:
            import s1_db
            site_id = s1_db.lookup_site_id(customer)
        else:
            from s1_dataset import resolve_site_id
            site_id = resolve_site_id(customer)

    run_month(args, customer=customer, site_id=site_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
