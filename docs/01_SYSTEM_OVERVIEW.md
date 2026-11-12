# 1. System overview, boundaries and ownership

## Purpose

Replace manually assembled monthly operational reports with a Report Manager dashboard and a customisable PDF export. Collect data from operational systems into PostgreSQL on agreed schedules, serve source-backed dashboards through Django, and make collection health observable. The reference reports are the June 2026 **Executive Summary Report** (15 pages) and **Ticket Overview Report** (one page).

## Systems and components

| System / component | Purpose | Implementation | Owner of the data |
|---|---|---|---|
| **Autotask PSA** | Companies, tickets, notes, contacts | REST API; `Collector/AutotaskLight-main` | Autotask owns source records; the reporting team owns extracted copies |
| **N-central** | Customers, devices, patches, monitoring | REST API; `Collector/ncentralAPI-main` | N-central owns device and patch state |
| **SentinelOne** | Endpoint agents, threats, monthly snapshots | REST API; `Collector/SentinelOne-main` | SentinelOne owns security state |
| **Cove** | Backup status | REST API; `Collector/Cove-main` (**new, disabled, not yet run live**) | Source owner unconfirmed |
| **PostgreSQL** | Reporting history and application data | Schemas `autotask`, `ncentral`, `sentinelone`, `cove`, `report_manager` | Reporting/DB team operates; vendors own upstream truth |
| **Django API** | Authentication, clients, reports, scores, collector control | `backend/api`, `backend/config` | Application team |
| **Preact/Vite UI** | Patches, servers, tickets, technicians, reports, collection, clients, settings | `frontend/src` | Application team |
| **Collector queue** | Prevent overlapping manual and scheduled batches; heartbeat; alert state | `backend/collector_runtime.sqlite3` | Application/platform team |
| **Worker, scheduler, alerts** | Run queued and due collectors; email problems | `collection_worker`, `run_scheduler` or Windows Task Scheduler, `collection_alerts` | Platform/operations team (proposed) |
| **Collector log store** | Run evidence, errors, durations | `backend/logs/collectors/*.log` | Platform/operations team (proposed) |
| **Client master list** | Which customers in which systems are the same client | `report_manager.api_client*` tables, Clients and Client Resolution pages | Reporting/product owner |
| **Score snapshots** | Saved patch scores for trends | `report_manager.api_patchsnapshot`, written by the worker | Application team |
| **Reports** | Saved report definitions (blocks with filters); chart-first A4 PDF with cover, contents and annex | Definitions in `report_manager.api_report`; PDF made in the browser by `Reports.jsx` and `src/report/` | Application/reporting team |

## Ownership and responsibility (a proposal, not confirmed)

The material supplied does not establish real team ownership or on-call contacts. Treat the matrix as an approval checklist.

| Service | Proposed accountable group | Operating responsibility | Escalation |
|---|---|---|---|
| Autotask tenant/API | Autotask-owning business unit | Credentials, permissions, 401/500 resolution, approved request budget | Autotask admin/vendor |
| N-central API/tenant | N-central operations group | Device lifecycle, 404 assets, request limits | N-central admin/vendor |
| SentinelOne console | Security/endpoint operations | API token, tenant/site access, threat interpretation | Security operations/vendor |
| Cove | Backup service owner, **TBD** | Access, credentials, integration review | Backup operations/vendor |
| Collector source adapters | Reporting engineering | Mappings, retries, snapshot policies, tests | Reporting engineering lead |
| Django API and authentication | Application engineering | Routes, authorization, data contracts | Engineering lead / security review |
| PostgreSQL | DB/platform engineering | Schemas, backups, indexing, grants, restore | DBA/platform lead |
| Scheduler, worker and alert mail | Platform operations | Service account, cadence, logs, alert recipients, recovery | Platform/on-call owner |
| React UI and PDF templates | Application/reporting product owner | Visualisation, filters, report definitions | Product owner |
| Client master list | Reporting/product owner | Confirm groupings, resolve discrepancies | Product owner |
| Identity and access policy | Security/product owner | Per-client permissions, production login policy | Security lead |

**Required handover fields** before production: named owner, technical contact, escalation contact, runbook link, credentials custodian, access approval, recovery target, data retention, operational hours.

## Technology and deployment boundaries

- **Frontend:** Preact, Vite, JavaScript/JSX, CSS. Short-lived access token in memory; refresh by HTTP-only cookie. The PDF is generated in the browser. Light, dark and accent themes; priority colours are user settings.
- **Backend:** Django with `psycopg`. Custom management commands. Settings come from `backend/.env`.
- **Sources:** four standalone Python projects with their own `.env` files. Do not redistribute those credentials; rotate any that were in shared archives.
- **Orchestration:** `collector_jobs.json` allowlists jobs and source locations. The queue is a local SQLite file, not source data. Something must tick the worker every minute (`run_scheduler`, or Windows Task Scheduler) or UI requests stay queued.
- **Reporting SQL:** schema-qualified (`autotask.ticket`, `ncentral.patch_run`, `sentinelone.agent`).

## Trust boundaries

1. **External API boundary:** upstream load belongs to the system administrators; never poll sources from dashboard requests.
2. **Collection-to-PostgreSQL boundary:** least-privilege collector role where feasible; never run destructive `init` commands on populated N-central history.
3. **App-to-database boundary:** the browser only sees what the Django API serializes.
4. **Authorization boundary:** three levels, **off by default** (`DJANGO_CLIENT_ACCESS_ENFORCED`): the platform owner (a Django superuser) above the companies; a company's administrators (staff), who see only their own company; and members, who see only the clients they were given. A company is a wall, administrators included. While enforcement is off, the `client` and `org` filters are conveniences, not authorization; while it is on, every person is held to their level in the data layer. Collector status and runs are for administrators; the raw collector log is the owner's.
5. **Operations boundary:** logs can reveal host names, customer identities and errors; limit the log endpoints and the filesystem.

## Outstanding decisions

- Whether the default database and the reports database are the same physical database; credentials and schema privileges in production.
- Whether the local SQLite queue remains acceptable if the worker and web app ever run on different machines.
- Nothing to decide for N-central patch devices that return 404 (decided 12 Nov 2026, NC-01): the device counts as **disconnected**, and Server Health says so on that device's card; the run itself stays **Partial** on the Data Collection page. Alerts on partial runs are off by default; nothing is deleted.
- The shape of the **master client list** for SQL joins and for tracking missing clients (DATA-03, DATA-04; see [`04_DATA_MODEL.md`](04_DATA_MODEL.md)).
- **Companies:** built (ACC-10 to ACC-12 and ACC-14). Still to decide or build: how a director sees several branches (ACC-13), making the shared score weights and report templates separate per company (ACC-15, needed before an unrelated organisation uses the system), and separate data sources per company (FUT-01).
- Whether to switch client access on, and when: it is built and off by default; it needs each person to be given their clients first.
- Ownership and retention of reports and templates, audit history and future scheduled report generation.
- Whether to apply the proposed Autotask technician columns and change the collector (see [`05_OPERATIONS.md`](05_OPERATIONS.md), Technician data).
