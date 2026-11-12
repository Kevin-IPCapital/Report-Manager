# Copilot Instructions — SentinelOne MSP Report Generator
# IP Dimension (PTY) LTD — Managed Security Services

## What this project is
This is a PowerShell-based monthly EDR security report generator for IP Dimension, a South African MSP.
It connects to a SentinelOne management console, pulls threat and agent data via REST API, and produces
per-customer security reports in a consistent plain-text format.

## Environment
- SentinelOne instance: https://euce1-swprd2.sentinelone.net (EU Central 1)
- Auth: API Token in `.env` as `SENTINELONE_API_TOKEN`
- Base URL in `.env` as `SENTINELONE_BASE_URL`
- Main script: `Generate-MonthlyReports.ps1`
- Reports output: `.\Reports\` folder (gitignored — contains customer data)
- Latest confirmed agent version: 25.1.4.434

## SentinelOne API endpoints available
| Endpoint | What it returns |
|---|---|
| `/web/api/v2.1/threats` | All threat events — full MITRE, classification, indicators, user, device |
| `/web/api/v2.1/agents` | All managed endpoints — health, version, mode, scan status |
| `/web/api/v2.1/sites` | All customer sites (75 total, 43 active with agents) |
| `/web/api/v2.1/activities` | Admin audit log — 50,000+ entries |
| `/web/api/v2.1/exclusions` | Whitelist/exclusion entries (84 total) |
| `/web/api/v2.1/update/agent/packages` | Available agent versions |
| `/web/api/v2.1/reports` | Existing scheduled reports |

## Threat object key fields
```
threatInfo.threatName          — name of the threat
threatInfo.classification      — Malware / Ransomware / Infostealer / Backdoor / PUA / General
threatInfo.confidenceLevel     — malicious / suspicious
threatInfo.mitigationStatus    — mitigated / not_mitigated / marked_as_benign
threatInfo.detectionType       — dynamic (Behavioral AI) / static
threatInfo.createdAt           — detection timestamp
threatInfo.updatedAt           — last updated timestamp
threatInfo.processUser         — user account running the threat
threatInfo.filePath            — path of the detected file
indicators[].tactics[].name   — MITRE ATT&CK tactic names
agentRealtimeInfo.siteName     — customer/site name
agentRealtimeInfo.agentComputerName — device name
```

## Agent object key fields
```
computerName          — device hostname
siteName              — customer/site name
osName                — OS version string
osType                — windows / macos / linux
agentVersion          — installed SentinelOne agent version
isUpToDate            — true/false
mitigationMode        — protect (auto-block) / detect (monitor only — NO automatic blocking)
infected              — true if currently infected
networkStatus         — connected / disconnected
lastActiveDate        — last seen timestamp
scanStatus            — finished / aborted / none
userActionsNeeded     — array of health issue codes (ne_not_running, ne_cf_not_active, etc.)
isPendingUninstall    — true/false
```

## Report format — ALWAYS use this exact structure

Every report must have these 11 sections in this order. Do not add, remove, or reorder sections.
Use `=` for the outer border (72 chars), `-` for section dividers (72 chars), numbered sections.

```
========================================================================
  IP DIMENSION - MONTHLY SECURITY REPORT
  Customer  : [CUSTOMER NAME]
  Period    : [MONTH YEAR]
  Generated : [DD MMM YYYY HH:MM]
  Risk Level: [CRITICAL / HIGH / MEDIUM / LOW]
========================================================================

1. EXECUTIVE SUMMARY
------------------------------------------------------------------------
   [Plain English summary — threats detected, auto-mitigation rate,
    critical flags on separate lines starting with !! for urgent items]

2. THREAT STATISTICS
------------------------------------------------------------------------
   Total Detected            : [n]
   Auto-Mitigated            : [n]  ([n]%)
   Unresolved                : [n]
   Marked as Benign          : [n]
   Mean Time to Respond      : [n] minutes (auto-mitigation avg)

   By Confidence Level:
     Malicious               : [n]
     Suspicious              : [n]

   By Detection Engine:
     Behavioral AI (dynamic) : [n]
     Static Analysis         : [n]

   By Classification:
     [Classification Name]   : [n]
     ...

3. HIGH-RISK THREAT CATEGORIES
------------------------------------------------------------------------
   [!!] RANSOMWARE       : [n]  - verify backups, review affected devices
   [!!] INFOSTEALER      : [n]  - credential/data theft risk
   [!!] BACKDOOR         : [n]  - verify all remote access tools
   [!!] LATERAL MOVEMENT : [n]  - review network segmentation
   [!]  LOLBins          : [n]  detection(s) using OS-native tools
        [count]x  [tool name]
   OR: "No high-risk threat categories detected this period."

4. MITRE ATT&CK TACTICS OBSERVED
------------------------------------------------------------------------
   - [Tactic Name]
   - [Tactic Name]
   OR: "None detected."

5. TOP THREATS BY FREQUENCY
------------------------------------------------------------------------
   [count]x  [threat name]
   ...
   OR: "No threats this period."

6. MOST AFFECTED DEVICES
------------------------------------------------------------------------
   [count]x  [device name]
   ...

7. USER ACCOUNTS INVOLVED IN THREATS
------------------------------------------------------------------------
   [count]x  [username]
   ...
   (omit this section if no non-system users involved)

8. DAILY THREAT TREND - [MONTH YEAR]
------------------------------------------------------------------------
   [YYYY-MM-DD]  [count]  [### bar chart]
   ...
   (omit this section if no threats)

9. ENDPOINT HEALTH & COVERAGE
------------------------------------------------------------------------
   Total Managed Devices     : [n]
   Agent Coverage            : [%]
   Protect Mode (auto-block) : [n]
   Detect-Only Mode          : [n]  [!! warning if > 0]
   Infected Devices          : [n]
   Outdated Agent Version    : [n]  (Latest: [version])
   Agent Health Issues       : [n]
   Not Seen in 7+ Days       : [n]
   Pending Uninstall         : [n]
   Scan Aborted              : [n]

   OS Distribution:
     [OS Name]               : [n]

   Agent Version Distribution:
     [version]               : [n]  [UPDATE AVAILABLE if not latest]

   DETECT-ONLY DEVICES (if any):
     [DETECT] [device] [[OS]]

   AGENT HEALTH ISSUES (if any):
     [HEALTH] [device] - [issue codes]

   OUTDATED AGENT DEVICES (if any):
     [UPDATE] [device] - v[version] on [OS]

   INFECTED DEVICES (if any):
     [INFECTED] [device] [[OS]] last seen [date]

   DEVICES NOT REPORTING >7 DAYS (if any):
     [STALE] [device] - last seen [date]

10. UNRESOLVED THREATS - IMMEDIATE ACTION REQUIRED
------------------------------------------------------------------------
   [[date]] [[confidence]] [threat name]
            Device: [device]  |  Class: [classification]  |  User: [user]
   ...
   (omit this section if no unresolved threats)

11. RECOMMENDATIONS & ACTION ITEMS
------------------------------------------------------------------------
   [URGENT] [action]
   [HIGH]   [action]
   [MEDIUM] [action]
   [LOW]    [action]
   OR: [OK] No issues detected. Security posture is healthy.

========================================================================
  Report generated by IP Dimension Managed Security Services
  Questions: security@ipdimension.co.za
========================================================================
```

## Risk Level scoring rules
Calculate a risk score, then assign level:
- +10 per unresolved threat
- +20 per infected device
- +20 per Infostealer detection
- +25 per Backdoor detection
- +30 per Lateral Movement detection
- +15 per Ransomware detection
- +3 per detect-only agent
- +5 per agent with health issues
- +2 per outdated agent
- +3 per stale agent (not seen >7 days)

Score >= 50 → CRITICAL | >= 20 → HIGH | >= 5 → MEDIUM | < 5 → LOW

## How to run the script
```powershell
cd "c:\Users\cornel\OneDrive - IP DIMENSION (PTY) LTD\Documents\GitHub\SentinelOne"
.\Generate-MonthlyReports.ps1 -Month "2026-06"               # specific month
.\Generate-MonthlyReports.ps1 -Month "2026-05" -ReportDir ".\Reports"  # custom output dir
```
Script reads `.env` automatically. Outputs one `.txt` per customer + `_MASTER_SUMMARY_` file.

## Python layer + PostgreSQL (see README.md)
`generate_monthly_reports.py` is the Python port and produces byte-identical text
output. Alongside it sits a database layer modelled on `Cove-main/`:

| File | Role |
|---|---|
| `s1_client.py` | REST client — auth, pagination, `siteIds` filtering |
| `s1_metrics.py` | Metric computation — risk score, MTTR, LOLBins, health |
| `s1_dataset.py` | Flattens API records into database-shaped tables |
| `s1_db.py` | Insert-only loader for the `sentinelone` schema + `customers` roster |
| `sql/schema.sql` | Schema definition — idempotent |

Scoping a run to one customer (filters the API server-side):
```powershell
python generate_monthly_reports.py --customer "VGI Consulting" --db   # month to date
python generate_monthly_reports.py --month 2026-06 --active --db      # per active customer
python s1_db.py load "VGI Consulting"                                 # database only
```

**Period.** `--month` defaults to the current month and the window runs from the
1st to the end of *today*, never into the future — a run on the 27th covers 1-27
and labels itself `July 2026 (1-27, month to date)`. Safe to schedule daily.
Month-over-month comparisons cut the earlier months to the same day so a partial
month is not compared against a complete one. `snapshot` records the period on
every load: `report_month`, `period_label`, `month_start`, `month_end`,
`is_partial_month`, `days_covered`.

Agent/endpoint data is always current whatever `--month` says — `/agents` has no
history. Only threats are windowed.

`Generate-MonthlyReports.ps1` was **not** given any of this; it still needs an
explicit `-Month` and always assumes a whole month.

**When changing a metric or a risk weight, change it in all three places:**
`Generate-MonthlyReports.ps1`, `generate_monthly_reports.py`, and
`s1_metrics.py` (`RISK_WEIGHTS`). Otherwise the `.txt` and the database disagree.

Every load is a snapshot — never update rows in place. `customer_name`
(= `siteName`) is the grouping key; `site_id` is an attribute used for API
filtering. Read data back through the `v_*_latest` views, which resolve the
newest snapshot **per customer per month**, not per run.

## Pagination pattern for API calls
```powershell
$all = @(); $cursor = $null
do {
    $url = "$baseUrl/web/api/v2.1/[endpoint]?limit=100"
    if ($cursor) { $url += "&cursor=$cursor" }
    $r = Invoke-RestMethod -Uri $url -Headers $headers -Method GET
    $all += $r.data
    $cursor = $r.pagination.nextCursor
} while ($cursor)
```

## Known customers and risk context (as of June 2026)
| Customer | Devices | Known Issues |
|---|---|---|
| Good Earth Global | 13 | ITAYI's MacBook Air actively compromised — osascript C2/infostealer running as root |
| VGI Consulting | 56 | 26 agents in detect-only mode — threats NOT auto-blocked |
| IP Dimension HQ | 44 | Hermes.exe lateral movement tool detected repeatedly on Cobe-DellNew |
| De Swardt | 1 | Vinga detections on DC, very low mitigation rate |
| Precious Metals Tswane | 37 | Recurring ransomware flags, lateral movement, 1 infected device |
| Sekretari | 7 | Persistent install.exe detections, agent pending uninstall |
| Vulpes | 53 | Large fleet, generally clean |
| JHPE | 17 | Advanced IP Scanner flagged as ransomware repeatedly on DC |

## Rules Copilot must always follow
1. Never suggest committing or referencing the `.env` file.
2. Reports output goes to `.\Reports\` only — never hardcode other paths.
3. Always use the 11-section format above — do not invent new sections or rename existing ones.
4. Risk level must always be calculated from the score formula, not guessed.
5. MTTR = average minutes between `threatInfo.createdAt` and `threatInfo.updatedAt` for mitigated threats only, excluding values > 7 days.
6. Detect-only agents must always be flagged as HIGH risk in recommendations — they cannot auto-block.
7. Always check `isUpToDate` field against the latest available version from `/update/agent/packages`.
8. When adding new API calls, always use pagination (cursor-based) — never assume all data fits in one response.
9. The `.gitignore` excludes `.env` and `Reports/` — do not modify this.
10. All timestamps from the API are UTC — display as-is without converting.
