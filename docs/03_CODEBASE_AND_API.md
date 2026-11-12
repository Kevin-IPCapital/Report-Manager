# 3. Codebase reference

The HTTP API (every endpoint, with examples) is in **[`08_API_REFERENCE.md`](08_API_REFERENCE.md)**. This file describes the code layout and the behaviour behind it.

## Source tree

```text
ops-dashboard/
  README.md                          How to run it; configuration; access; clients; scores; collectors
  docs/                              This documentation pack and STAGED_PLAN.md
    tools/generate_er.py             Regenerates 09_ER_DIAGRAMS.md and diagrams/er_* from the DDL and models
    tools/generate_fixtures.py       Regenerates the API response fixtures the report tests read (needs Django)
    tools/viewer_smoke.js            Runs the viewer page's script against a stub page and a live viewer
    serve.py, Open-Docs.cmd          The documentation viewer (python docs/serve.py)
  backend/
    manage.py                        Django entry point (sets DJANGO_SETTINGS_MODULE=config.settings)
    config/settings.py               Environment, databases, security, token policy, throttling, alerts
    config/env.py                    .env loader and DATABASE_URL parser (no third-party packages)
    config/urls.py                   The route map (every route is documented in 08)
    requirements.txt                 Django and psycopg
    collector_jobs.example.json     Recommended job configuration. Copy it to collector_jobs.json (yours, never shipped, git-ignored)
    deploy/                          Register-CollectorTasks.ps1 and README.md (running unattended)
    api/
      views.py                       Login, accounts, sections, clients, resolution, weights
      auth.py                        Access tokens, rotating refresh tokens, require_auth, staff_required, can_manage_collectors
      throttle.py                    Failed-attempt throttling (cache)
      models.py                      RefreshToken, Client, ClientSourceMap, ClientAlias, ScoreWeight, PatchSnapshot, Report
      migrations/                    0001 tokens, 0002 client, 0003 clients links/aliases, weights, snapshots, 0004 reports
      clients.py                     Name tokenising, grouping, discrepancies, resolver, source customer lists
      reports.py                     Saved report definitions: the block rules, and their validation
      gaps.py                        The lasting record of gaps between the source systems and the client list
      access.py                      Which clients a signed-in person may see: the owner everything, a company's administrators their company (and unclaimed clients), members the clients they were given (once enforced); the pure rule is scope_for
      verify.py                      The read-only real-data check behind `manage.py verify_data`
      scoring.py                     Patch score calculation and weight validation
      snapshots.py                   Saving score snapshots, backfill, trend
      data/                          Report data, one module per source
        __init__.py                    get_section(): name -> payload
        common.py                      query(), timestamps, column definitions
        tickets.py                     Autotask tickets (+ technician detection)
        patches.py                     N-central patch compliance and drill-down
        servers.py                     SentinelOne agent health
        backups.py                     Cove backup devices, health and red flags
      collection_actions.py          Queue a run; execution status
      collection_health.py           Health and latest-log endpoints
      collection_health_core.py      Log parsing, JOB_NAMES, JOB_LABELS, freshness from logs
      collection_log_core.py         Bounded read of a job's latest run
      collection_freshness.py        Stored source timestamps (data age)
      collection_alerts.py           Alert rules, announce-once planning, email text
      collector_queue.py             SQLite queue, schedules, heartbeat, alert state
      management/commands/
        run_collection.py            Allowlisted collector subprocess, timeout, lock, logs
        collection_outcomes.py       SUCCESS / PARTIAL / FAILED classification
        collection_worker.py         One worker tick (+ patch score snapshots)
        run_scheduler.py             Worker every minute, alert check every 5, one process
        collection_alerts.py         Check and email problems
        snapshot_scores.py           Save score snapshots by hand
        client_master_view.py        Create or drop the client_master view (PostgreSQL)
        client_access.py             List, grant or revoke a person's access to a client
        company.py                   List, create and move companies, people and clients; make the platform owner
        verify_data.py               Check every dashboard section against the real databases (read-only)
        makestaff.py                 Grant or remove staff access
        dbcheck.py, exportsql.py     Database diagnostics; schema as SQL for pgAdmin
      test_*.py, tests*.py           See "Tests" below
  frontend/
    package.json, vite.config.js     Preact + Vite; `npm test` runs the Node tests
    src/App.jsx, routes.js           Router; page list (staffOnly pages hidden from others)
    src/api/adapter.js               The only file that knows how data is fetched and tokens handled
    src/auth/AuthContext.jsx         Browser authentication state
    src/state/                       Settings (theme, colours), date range, loading overlay
    src/pagination.js, text.js       Paging and text/duration formatting (with Node tests)
    src/filter.js, glossary.js       Column search and device-name matching; hover definitions (page-scoped where a label differs)
    src/serverHealth.js, freshness.js  Server Health filters and counts; the Data Collection freshness wording (pure, tested)
    src/overview.js                  The Overview's client filter: choices, request parameters, what a click on a measure does (pure, tested)
    src/report/lists.js              Personal reports and templates: the two lists and what each person may do (pure, tested)
    src/ranges.js                    Date ranges you name and save (pure, tested); state/SavedRangesContext.jsx holds the list
    src/trend.js, gaps.js            Trend ranges and the ticket volume trend; wording for the record of gaps (pure, tested)
    src/worst.js                     "Worst first" ordering per page, and the Server Health sort choices (pure, tested)
    src/storage.js, persisted.js, rangeMemory.js, dateMath.js   What is remembered between visits, its checks, and date ranges (pure, tested)
    src/state/persist.js             usePersistentState: useState that remembers its value, per signed-in person
    src/components/                  Tables, charts, modals, layout, filters, ticket explorer
    src/pages/                       See "Frontend pages"
    src/report/                      Report rules and PDF: config.js (blocks, validation), dates.js, fetch.js, extract.js, pdf.js, template-cases.json
  Collector/
    AutotaskLight-main/              Autotask collector (+ sql/proposed/technician.sql, not applied)
    ncentralAPI-main/                N-central inventory, patches, monitoring
    SentinelOne-main/                SentinelOne monthly collector
    Cove-main/                       Cove backup collector (new; not yet run live)
```

This tree is the **archive as supplied and extended**, not a statement about a deployed server.

## Backend behaviour worth knowing

**Wiring.** Django is configured by names in `settings.py`, not by import statements: `ROOT_URLCONF` points at `config/urls.py`, `INSTALLED_APPS` loads `api` (its models and migrations), `DATABASES` is built from `DATABASE_URL`. `manage.py` only sets the settings module and hands over to Django.

**Report data.** `api/data/` reads the source schemas directly with schema-qualified SQL through one function (`common.query`). The browser does the filtering, grouping and averaging; one request serves each page. The tests replace `common.query` with a fake reporting database, so they need neither PostgreSQL nor the source schemas.

**Tickets.** Latest version of each ticket (`DISTINCT ON (ticket_id)` by `snapshot_id DESC`), then `created_at` within the period. `response_hours` and `resolution_hours` are elapsed time. A late first response marks the row red (`_row_reasons` is the single place for red-row rules). `technician` appears only when the source has assignments (see [`05_OPERATIONS.md`](05_OPERATIONS.md)).

**Patches.** Each device's newest and second-newest **usable** inventory (finished run, no error, coverage not `unavailable`), joined to `patch_entry`. A failed device is never read as compliant. `inventory_query(rank, as_of, org_units)` also rebuilds the position as of an earlier run (for snapshots) and limits devices to chosen N-central org units. The client filter's choices come from `ncentral.device`.

**Scores** (`scoring.py`). A type's score is the weighted average of its categories' compliance; the overall score is the weighted average of the type scores. A weight of 0 leaves an item out; an item with no value is skipped; a result with nothing to average is `null`, never 0. Weights: the caller's own over the shared default over 1.

**Snapshots** (`snapshots.py`). After a successful or partial N-central patch collection the worker saves one snapshot per patch run for all devices and for each client linked to N-central: counts, the shared weights used, and the resulting scores. The trend reads these; with none it returns empty lists. `snapshot_scores --backfill` rebuilds older runs from history already in the database (marked `backfill`: today's definitions).

**Clients** (`clients.py`). Customers are linked to a client by each system's own ID. A name matches only through the client's name or an alias (case, punctuation and accents ignored). Unlinked customers are grouped when their names have the same words in the same order; every group is flagged as an inference and needs confirming. The collectors' own `customers` tables are left alone, because the collectors read them to decide whom to load.

**Authentication** (`auth.py`, `views.py`, `throttle.py`). Short-lived signed access token (10 minutes, held in browser memory); opaque refresh token in an HTTP-only cookie, stored hashed, rotated on every use, with a 10-second leeway successor and family revocation on late reuse. Sign-in and reset requests are throttled (5 per 15 minutes per address and username). Passwords pass Django's validators. The dev login bypass is off unless explicitly enabled and `DJANGO_DEBUG=1`. In production mode a real secret key is required, HTTPS is enforced and HSTS is on.

**Collector control.** No HTTP request starts a collector. A request is queued in `collector_runtime.sqlite3`; the worker runs it. The worker takes a global lock, records a heartbeat (including every 30 seconds during a long job), runs each job as a subprocess with its own timeout, and classifies the outcome. `run_scheduler` or two scheduled tasks keep it ticking; `collection_alerts` emails problems.

## What the app remembers, and the order pages start in

**Order.** Pages start worst first, using the rules in `src/worst.js`: **Tickets** (Client Tickets and Technician Tickets) late or flagged first, then open before closed, the longest open first, closed ones slowest first; **Server Health** infected, then disconnected, then unknown, then connected, the oldest data first within each; **Patches** the category with the lowest compliance first (a category with no figure last); **Backups** critical, then warning, then OK, the longest since a successful backup first (never succeeded is the longest of all). A table's column sort overrides it, and the **Reset Sort** button returns to it. Server Health also has a **Sort** box (Worst first, Name, Customer then name, Oldest data first). The Overview is exempt, and the Data Collection, Clients, Client Resolution and Reports lists keep their own order (worst was not defined for them).

**Remembered between visits** (in the browser, for the signed-in person only; see Settings, Remembered Inputs, to forget them): the date range (a range ending today as its length), the last range used to generate a report, the ticket, backup and server filters, the device-name filters, the Server Health sort and card size, the Patches client, and for the tables of tickets, backups, patch categories and patch entries their sort and page size. Not remembered: column searches, hidden columns, minimised charts, and the report editor. Entries are stored under `rm:v1:<username>:<name>`; every read is checked, so a damaged or outdated entry is ignored, a browser that blocks storage remembers nothing, and an entry for something that no longer exists (a column, a client, a filter the page no longer offers) is dropped.

## Frontend pages

The front end has its own guide, [`10_FRONTEND_GUIDE.md`](10_FRONTEND_GUIDE.md): how it starts, every component and shared module, what is remembered, styling, conventions and recipes. The table below is the quick list of pages.

| Route | Page | Who | Purpose | Limitation |
|---|---|---|---|---|
| `/` | `Patches.jsx` | signed in | **Overview**: the Overall Score card, which is **for one client**: with exactly one client chosen it shows that client's score, and with none or several it says "Please select a single client" instead of a combined figure; a Clients card beside it (every client you may see with its device count; clicking names adds them to the filter, shown as bubbles that remove them one at a time); the trend directly below (also for one client, with the same prompt otherwise; range: all history or the last 3, 6 or 12 months); then Scores By Client (one group per client, every client or only the chosen ones; a client's name shows only that client, a measure drills down to categories and patches, with a device filter on the patch list); the score weights. Several clients can be chosen and are remembered | Trend is empty until snapshots exist; no per-device scores yet |
| `/server-health` | `ServerHealth.jsx` | signed in | SentinelOne endpoints as cards, each with a SentinelOne Status and an N-central Status and three sections (Performance, Patches, Data Collected): eight tiles (Endpoints, Servers, SentinelOne Connected, SentinelOne Disconnected, N-central Connected, N-central Disconnected, Infected, Out Of Date) that count and, except the first, filter; a device-name filter; Expand All and Collapse All; paging | CPU, RAM and disk are placeholders ("No data"): nothing collects them yet |
| `/backups` | `Backups.jsx` | signed in | Cove backup devices: health tiles, a device-name filter, charts by health, customer, device type and red-flag category, table, device card | Empty until the Cove collector has been run and enabled |
| `/tickets` | `Tickets.jsx` | signed in | Tickets by client: tiles, charts, a volume trend (tickets created per day, or per week for periods over 62 days; a collapsible chart like the others), table (via `TicketExplorer`) | Autotask is failing upstream |
| `/technicians` | `TechnicianTickets.jsx` | signed in | The same tickets led by technician | Needs assignment data (not collected yet); says so |
| `/reports` | `Reports.jsx` | signed in | Saved reports: list, create, edit, copy, export, import, generate a PDF for chosen dates; a section's filters can hold several values (bubbles remove them); a report's default period can be a duration | Each section's filters choices come from live data |
| `/collection-health` | `CollectionHealth.jsx` | staff | Job status, freshness, logs, rerun, schedule, worker heartbeat, alerts | |
| `/clients` | `Clients.jsx` | staff | Master clients, links and aliases, with each client's company; an Unclaimed Clients panel where an administrator claims a client for their company (the owner assigns it) | Moving a claimed client to another company is the owner's, by `manage.py company claim` or the API, not yet on the screen |
| `/access` | `Access.jsx` | staff | An administrator: their company's people and the clients each may see. The owner also gets a Companies panel (make one) and a company box for each person | Does nothing until `DJANGO_CLIENT_ACCESS_ENFORCED=1` |
| `/clients/resolution` | `ClientResolution.jsx` | staff | Group and confirm unlinked customers; discrepancies, each with how long it has lasted; what was resolved recently | Inference only; name-based grouping |
| `/settings` | `Settings.jsx` | signed in | Theme, accent, priority colours, layout | |
| `/register`, `/forgot`, `/reset` | account pages | signed out | Account flows | |

Shared behaviour: the date range at the top has quick buttons (Last 30 days, This month, Last month, This quarter, Last quarter) as well as the slider and the date boxes. Saved date ranges (Settings) appear as extra quick buttons on the date range, in a report's date choices and in the trend's range box. Every table has per-column search (click the heading), sort, collapse, and **pagination** (20 rows by default, 10/20/50/100). In a number, percentage, duration or date column the search also understands comparisons: `>5`, `<=10`, `=5`, `!=5`, `5..10`, and `>2, <10` (comma means and); durations take units (`>2h`, `<=30m`, `1d..2d`, a bare number is hours); dates are `>2026-10-01` or `2026-10-01..2026-10-31`. A bare number in a number column means equals, so "12" no longer also finds 120. Anything that is not a valid comparison is searched as text. Charts can be minimised one at a time or all together (Collapse All / Expand All Charts on the ticket pages). Hovering a tile, heading, score or chart title shows its definition (`frontend/src/glossary.js`). Durations show as "N days, M hours, O min" with zero units omitted. Staff-only pages are hidden from other users (the API enforces it too).

## Reports

A report is a list of **blocks** (sections), each with its own filters; the definition is saved on the server (`api_report`, `/api/reports`) and the PDF is made in the browser from the live API responses, with nothing sent to third parties. The rules for a definition live in `frontend/src/report/config.js` and, repeated for the server, `backend/api/reports.py`; both are tested against `frontend/src/report/template-cases.json`, so they accept and reject exactly the same templates. Old (version 1) templates are migrated when read.

| Module | What it does |
|---|---|
| `report/config.js` | Block types, their filters, `validateTemplate` (normalise, migrate v1) |
| `report/dates.js` | The date presets, and which dates a run uses (entered, else the report's own, else the last 30 days) |
| `report/fetch.js` | Fetches what the blocks need, once per distinct request |
| `report/extract.js` | Turns each block's data into KPIs, charts and detail tables; the tables only reach the report as an annex |
| `report/pdf.js` | Dependency-free PDF: cover, contents with page numbers, sections, annex, "Page n of N" |
| `report/fixtures/api-responses.json` | The API's real response shapes, produced by the backend's own code, that the report tests read |

The patch section's overall score is the server's calculation. A report that carries its own weights asks the server for scores with them (shared defaults plus the report's weights, never the viewer's personal ones), so everyone who runs it gets the same numbers. The June 2026 reference reports include sections with no source yet (network reliability, true performance); do not call the builder parity-complete.

## External source integrations (observed in the scripts)

The collectors have their own guide, [`11_COLLECTORS.md`](11_COLLECTORS.md): what each does, the jobs, how a run is judged, the tables, and how to add one. The table below is the quick list.

| Integration | Auth and prefix | Calls observed | Caution |
|---|---|---|---|
| Autotask | API headers, zone discovery; REST `v1.0` | Companies, tickets, notes, contacts; fixed ticket field list (no assignee) | Last reported run: two 401 and one 500 (an Autotask-side SMTP setting); nothing loaded |
| N-central | Bearer token via `POST /api/auth/authenticate` | `/api/org-units`, `/api/org-units/{id}/devices`, `/api/devices`, `/api/devices/{id}/assets`, monitoring and custom properties | Device assets can 404 (PARTIAL): those devices count as disconnected on Server Health |
| SentinelOne | Token; `/web/api/v2.1` | Agents, sites, threats with pagination | Zero threats can be a genuine result |
| Cove | `https://api.backup.management/jsonapi` login, then calls | Customer and device statistics into the `cove` schema; read by the dashboard's Backups page and report section | New; not yet run live; the client also contains a write method (`edit_account`) that no job calls |

## Tests

- **No database needed:** `api/test_data.py` (report data through the fake reporting database), `test_logic.py` (scoring, weights, client grouping), `test_alerts.py` (alerts, heartbeat, worker tick, scheduler rules), `tests_collection.py` (job registry and configuration), `test_collection_health.py`, `test_docs.py` (this pack against the code, including the ER diagrams, and the documentation viewer), the frontend `npm test` (paging, text, column filters, glossary), and the frontend `npm test`.
- **Need the Django test database:** `api/tests.py` (authentication, throttling, clients, resolution, weights, snapshots, endpoints). The test role needs permission to create a database.
- Run: `python manage.py test` and `npm test`.
