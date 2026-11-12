# SentinelOne EDR Reporting — IP Dimension

Monthly per-customer EDR security reports from the SentinelOne console, plus a
PostgreSQL history in the `sentinelone` schema.

Structure mirrors `Cove-main/`: a thin API client, an analysis layer, a
table-shaping layer, and an insert-only loader.

| File | Role |
|---|---|
| `s1_client.py` | REST client for `/web/api/v2.1` — auth, pagination, site filtering |
| `s1_metrics.py` | Per-customer metric computation — risk score, MTTR, LOLBins, health |
| `s1_dataset.py` | Flattens raw records into database-shaped tables (`DataTable`/`Dataset`) |
| `s1_db.py` | PostgreSQL loader + the `customers` roster |
| `sql/schema.sql` | `CREATE SCHEMA sentinelone` — idempotent, safe to re-run |
| `sql/queries.sql` | Query library over the `v_*_latest` views |
| `generate_monthly_reports.py` | The `.txt` report generator (unchanged output, new flags) |
| `Generate-MonthlyReports.ps1` | Original PowerShell implementation — text reports only |

## Setup

```powershell
pip install -r requirements.txt
```

Add the database DSN to `.env` (same `ipcreporting` database Cove writes to,
different schema):

```
SENTINELONE_PG_DSN=postgresql://USER:PASSWORD@192.168.8.6:5432/ipcreporting
```

`s1_db.py` also accepts `S1_PG_DSN`, `IPC_PG_DSN` or `COVE_PG_DSN` if you would
rather not duplicate the value.

Create the schema once:

```powershell
psql "postgresql://USER:PASSWORD@192.168.8.6:5432/ipcreporting" -f sql/schema.sql
```

Then populate the customer roster from the console:

```powershell
python s1_db.py sync-customers
python s1_db.py customers          # confirm what will be reported on
```

`sentinelone.customers` is the control surface. Set `is_active = false` on a row
to drop that site out of automated runs without losing its history — a later
`sync-customers` will not silently re-enable it.

## Reporting period

The period defaults to **the current month, from the 1st through the end of
today** — so a run needs no `--month` at all and is safe to schedule daily:

```powershell
python generate_monthly_reports.py --db          # 1st -> today, whole estate
python s1_db.py load                             # same, database only
```

A run on 27 July covers 1–27 July and labels itself
`July 2026 (1-27, month to date)`. It does not claim 31 days. Pass `--month` for
a specific month, which is what you want after month end:

```powershell
python generate_monthly_reports.py --month 2026-06 --db
```

Two things follow from this:

- **Endpoint data is always current.** `/agents` returns present state only — the
  API keeps no agent history — so device counts, health issues, detect-only
  agents and staleness reflect the console at capture time whatever `--month`
  says. Only the threat side is windowed.
- **Month-over-month stays fair.** While a month is in progress the two
  comparison months are cut to the same day, so 27 days of July is measured
  against 27 days of June, not all 30. The trend line shows the spans:
  `June 2026 (1-27) : 41  |  July 2026 (1-27) : 33`.

Every load records the period it covered on the `snapshot` row —
`report_month` (`'YYYY-MM'`), `period_label`, `month_start`, `month_end`,
`is_partial_month`, `days_covered` — and those columns are carried through
`v_customer_month_latest`, `v_portfolio_month` and `v_customer_trend` so no
consumer has to guess. `v_customer_trend.threats_per_day` is the honest
comparison while a month is still running; filter on `NOT is_partial_month` when
you only want completed months.

Re-running the same month simply appends a snapshot, and the views resolve the
newest one per customer per month — so a daily run needs no cleanup.

## Selecting one customer

Everything is scoped by `--customer` (or `--site-id`), which filters the API
server-side, so a single-customer run pulls only that customer's data:

```powershell
# one customer, current month to date, .txt only
python generate_monthly_reports.py --customer "Acme Holdings"

# one customer, a specific month
python generate_monthly_reports.py --month 2026-06 --customer "Acme Holdings"

# one customer, .txt + database
python generate_monthly_reports.py --month 2026-06 --customer "Acme Holdings" --db

# one customer, database only
python generate_monthly_reports.py --month 2026-06 --customer "Acme" --db --no-reports

# whole estate, as before — plus the database
python generate_monthly_reports.py --month 2026-06 --db

# one run per active customer in sentinelone.customers
python generate_monthly_reports.py --month 2026-06 --active --db
```

A partial name is accepted when it matches exactly one site; an ambiguous name is
an error rather than a guess. With no scope flag the behaviour is unchanged from
before: every customer, in one go.

Scoped runs write `_SUMMARY_<customer>_<month>.txt` instead of overwriting the
portfolio-wide `_MASTER_SUMMARY_<month>.txt`.

## Database-only loads

`s1_db.py` skips report rendering entirely:

```powershell
python s1_db.py load                          # current month to date, whole estate
python s1_db.py load "Acme Holdings"          # current month to date, one customer
python s1_db.py load 2026-06                  # a specific month, whole estate
python s1_db.py load 2026-06 "Acme Holdings"  # a specific month, one customer
python s1_db.py load-active                   # one snapshot per active customer
python s1_db.py load-active 2026-06           # same, for a specific month
```

A leading `YYYY-MM` is optional — anything else is read as a customer name, so
`load` and `load "Acme"` both mean the current month.

## How the schema works

Every load is a **snapshot**. The API exposes threats for a window but only
*current* agent state, so history is built by appending snapshots rather than
updating in place — that is what makes month-over-month comparison possible.

- `snapshot` — one row per run: the period covered (`report_month`,
  `period_label`, `month_start`, `month_end`, `is_partial_month`,
  `days_covered`), the scope, and row counts
- `customers` — the roster driving per-customer runs
- `customer_month` — the scorecard: counts, MTTR, risk score, risk level
- `threat` / `agent` — one row per threat, one per endpoint
- `threat_class`, `mitre_tactic`, `top_threat`, `top_device`, `top_user`,
  `lolbin`, `daily_trend`, `os_spread`, `agent_version` — the ranked breakdowns
  that appear in the `.txt` sections

Grouping key is `customer_name` (SentinelOne's `siteName`), matching how the text
reports bucket data. `site_id` rides along as an attribute and is what the API is
filtered on.

Because customers can be loaded at different times, "latest" is resolved **per
customer per month**, not per run. Read through the views:

| View | What it gives you |
|---|---|
| `v_customer_month_latest` | newest scorecard row per (customer, month) |
| `v_customer_current` | most recent month on record per customer |
| `v_threat_latest` / `v_agent_latest` | detail rows from non-superseded snapshots |
| `v_portfolio_month` | portfolio roll-up per month |
| `v_customer_trend` | month-over-month threat and risk deltas |
| `v_red_flag_latest` | unresolved threats, detect-only, health, stale — one list |
| `v_coverage_gaps` | active customers missing from the latest month |

## Risk score

`s1_metrics.RISK_WEIGHTS` is the single definition used by the database layer:

| Finding | Points each |
|---|---|
| Lateral movement | 30 |
| Backdoor | 25 |
| Infected endpoint | 20 |
| Infostealer | 20 |
| Ransomware | 15 |
| Unresolved threat | 10 |
| Agent health issue | 5 |
| Detect-only endpoint | 3 |
| Stale endpoint (>7d) | 3 |
| Outdated agent | 2 |

Bands: `CRITICAL >= 50`, `HIGH >= 20`, `MEDIUM >= 5`, else `LOW`.

**The same weights are also hard-coded in `generate_monthly_reports.py` and
`Generate-MonthlyReports.ps1`.** A change has to be made in all three, or the
`.txt` and the database will disagree.
