# 4. Database schemas, relations and source semantics

## Database versus schema

The PostgreSQL connection URL names a **database**. Inside it, schema-qualified queries read `autotask`, `ncentral`, `sentinelone` and (once loaded) `cove`; `report_manager` is the application schema that Django owns. `REPORTS_DATABASE_URL` may point at the same physical database as `DATABASE_URL`; check with `python manage.py dbcheck`, which prints the user, database, schema and whether the role may create tables.

The earlier copies of source tables inside `report_manager` are stale and must not be used as a mirror. The source schemas are more complete and carry source-specific keys. Do not delete the copies until dependencies are checked.

## Source schemas (from the ERD exports and collector DDL)

Diagrams of every schema, with all columns and relationships, are in [`09_ER_DIAGRAMS.md`](09_ER_DIAGRAMS.md) (generated from the DDL below, so they stay correct).

### `autotask` (6 tables)
| Table | Primary key | Purpose |
|---|---|---|
| `snapshot` | `snapshot_id` | Capture metadata: time, source, company filter, lookback, ticket count |
| `company` | `(snapshot_id, company_id)` | Company state within a snapshot |
| `ticket` | `(snapshot_id, ticket_id)` | Historical ticket versions with SLA and response timestamps. **No assignee column today** |
| `ticket_note` | `(snapshot_id, note_id)` | Notes |
| `contact` | `(snapshot_id, contact_id)` | Contacts |
| `customers` | `id` | Customers to load; `autotask_company_id` is the stable ID |

Composite foreign keys (`ticket` to `company`, `ticket_note` to `ticket`) are essential for correct historical joins. A proposed `autotask.resource` table and `ticket.assigned_resource_id` column are in `Collector/AutotaskLight-main/sql/proposed/technician.sql` (**not applied**).

### `ncentral` (13 tables)
| Table | Primary key | Purpose |
|---|---|---|
| `customers` | `org_unit_id` | Customer organisation units |
| `sites` | `site_id` | Sites |
| `device` | `device_id` | Devices: name, class, customer name, `org_unit_id`, last seen |
| `patch_run` | `run_id` | Patch run: start, finish, counts, `ok`, error |
| `device_patch` | `(run_id, device_id)` | Per-device inventory, coverage, error |
| `patch_entry` | `(run_id, device_id, title)` | Individual patches, installed/missing flags |
| `patch_backlog` | `(device_id, title)` | First/last missing, resolved |
| `poll_run`, `device_poll` | `poll_id`; `(poll_id, device_id)` | Monitoring polls |
| `perf_check`, `perf_event` | `(device_id, task_id)`; `event_id` | Monitor definitions and transitions |
| `metric_run`, `perf_sample` | `run_id`; `(device_id, sampled_at)` | CPU/RAM/disk samples (no data yet) |

Not all related IDs are declared foreign keys; do not infer constraints without profiling. No collector code deletes or prunes `patch_entry` or `device_patch`, so history from earlier runs is retained (which is what makes backfilled score snapshots possible).

### `sentinelone` (14 tables)
`snapshot`, `customer_month`, `customers` (unique `site_id`), `agent` (`(snapshot_id, agent_id)`), `threat`, and the monthly distribution tables (`agent_version`, `daily_trend`, `lolbin`, `mitre_tactic`, `os_spread`, `threat_class`, `top_device`, `top_threat`, `top_user`). Zero threats can be a genuine zero-threat period.

### `cove` (6 tables, new; applied by `Collector/Cove-main/sql/schema.sql`)
The dashboard reads the **current** devices as described in [`03_CODEBASE_AND_API.md`](03_CODEBASE_AND_API.md): the newest snapshot of each load scope, newest wins. `health` is set by the collector (CRITICAL, WARNING or OK) and each red flag has a category bucket. `customers` (the list to load: `id`, `customer_name`, `created_at`, `is_active`; no unique constraint on the name), `snapshot` (`snapshot_id`, `captured_at`, ...), `device` (`(snapshot_id, device_id)`), `datasource_session`, `m365_source`, `red_flag`. Every load appends a snapshot; nothing is updated in place. **Not yet loaded against a live system.**

## `report_manager` (the application schema)

Created by `python manage.py migrate` (or `exportsql` when the role cannot create tables). Django's own tables (`auth_*`, `django_*`) plus:

| Table | Purpose | Key columns |
|---|---|---|
| `api_refreshtoken` | Refresh tokens, hashed, grouped in families | `token_hash` (unique), `family`, `expires_at`, `family_expires_at`, `rotated_at`, `revoked` |
| `api_client` | **The master client** | `name` (unique), `contact_email`, `notes`, `is_active`, `company_id` (empty = unclaimed) |
| `api_company` | Whoever a person works for; a wall between organisations once access is enforced | `name` (unique) |
| `api_companymember` | Which company a person works for (one, for now) | `user_id` (unique), `company_id` |
| `api_clientsourcemap` | Links a client to one customer in one source, by that system's own ID | `client_id`, `source`, `external_id`; unique `(source, external_id)` |
| `api_clientalias` | Other spellings of a client's name | `client_id`, `alias`, `alias_norm` (unique) |
| `api_scoreweight` | Patch score weights; `user_id` null is the shared default set | `user_id`, `key`, `weight`; unique per user and key, and unique shared key |
| `api_clientaccess` | Which client a person may see (used only while access is enforced) | `user_id`, `client_id`; unique together |
| `api_clientgap` | The lasting record of gaps between the source systems and the master client list (see below) | `key`, `kind`, `source`, `client_id`, `external_id`, `label`, `first_seen_at`, `last_seen_at`, `resolved_at`; unique `key` among open gaps |
| `api_report` | A saved report definition: its blocks and options as JSON (see the API reference), private to its owner unless shared | `owner_id`, `name`, `shared`, `config` (JSON), `created_at`, `updated_at`; unique `(owner_id, name)` |
| `api_patchsnapshot` | Patch scores as of one N-central run, for all devices (`client_id` null) or one client | `run_id`, `client_id`, `captured_at`, `source` (`live`/`backfill`), `weights`, `components`, `scores` (JSON) |

`source` values in `api_clientsourcemap`: `autotask` (Autotask `company_id`), `ncentral` (N-central `org_unit_id`), `sentinelone` (SentinelOne `site_id`), `cove` (`cove.customers.id`). `external_id` is text.

`api_patchsnapshot.scores` looks like `{"overall": 90.58, "windows-security": 96.0, "windows-patching": null, "third-party": 88.0}`; `components` lists `{type, category_id, category, installed, missing, compliance}` per category; `weights` records the shared weights used, so history can be audited or re-weighted.

The collectors' own `customers` tables stay in their schemas: the collectors read them to decide whom to load. `report_manager.api_client` is where **we** manage clients.

## Views the collectors already create

Each source schema ships `v_*` views (Autotask 6, N-central 21, SentinelOne 10, Cove 6), mostly "latest snapshot" and summary views. Two cautions about the N-central patch views, from reading their definitions (not from running them):
- `v_device_patch_current` takes each device's newest `device_patch` row by `captured_at`, **without** skipping rows with an `error` or `coverage = 'unavailable'`, whereas the dashboard deliberately uses each device's newest *usable* inventory. A failed fetch can therefore become a device's "current" state in that view.
- `v_patch_summary` measures **device-level** security compliance (devices with inventory and no missing security patch, as a percentage), and `v_patch_trend` gives daily missing-patch totals per customer **name**. The dashboard score is a different measure: the share of patch **entries** installed, averaged per category. Which definition the reports should use is an open decision (SCORE-02).

## Current reporting SQL

### Tickets
`DISTINCT ON (ticket_id) ... ORDER BY ticket_id, snapshot_id DESC` takes the latest version of each ticket, then `created_at` is filtered to the requested UTC range. This is a ticket-**created**-in-period view. **Risk:** choosing by greatest `snapshot_id` assumes snapshots increase monotonically with consistent scope. Technician columns are probed in `information_schema` on each request and used only when present.

### Patches
`ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY run_id DESC)` over `device_patch` rows from finished runs with no error and coverage not `unavailable`; inventory rank 1 is the latest, 2 the previous. Joined to `patch_entry` on `(run_id, device_id)`. Optional limits: an `as_of` run (earlier position) and `ncentral.device.org_unit_id IN (...)` (client or customer scope). The type is derived: third-party if `is_third_party`, else security if `is_security`, else Windows patching. Compliance is per **patch entry** counts, not necessarily N-able's device-level coverage metric. `ncentral.device.org_unit_id` is the **customer** org unit: every N-central collector fetches devices per customer (`get_devices_for_org_unit`) and writes that ID; `site_id` is the site and is null for devices attached directly to the customer. This is from the collector code; confirm it against live rows.

### SentinelOne health
Newest agent per `(customer_name, agent_id)` with its snapshot `captured_at`; decommissioned agents excluded. `data_age_hours = now - captured_at`; an agent's `last_active` is never used as collection age.

## Cross-source customer identity

| Source | Stable ID | Where it lives in the dashboard |
|---|---|---|
| Autotask | `company_id` | `api_clientsourcemap` (`source='autotask'`); tickets carry `client_id` |
| N-central | `org_unit_id` (on `ncentral.device`) | `source='ncentral'`; patches filter on it |
| SentinelOne | `site_id` | `source='sentinelone'`; servers carry `client_id` |
| Cove | `cove.customers.id` | `source='cove'` |

**Rule:** match by ID; names only through a client's name or alias; never join on a name in SQL. The resolution page groups unlinked customers whose names have identical words in identical order and flags that as an inference. Names that differ by an extra, missing or reordered word (or abbreviations such as "Ltd") never group and need an alias or a manual link.

### Joining across schemas today
The long-form link table supports a join for any one source:
```sql
SELECT c.name, t.ticket_number
FROM autotask.ticket t
JOIN report_manager.api_clientsourcemap m ON m.source = 'autotask' AND m.external_id = t.company_id::text
JOIN report_manager.api_client c ON c.id = m.client_id;
```

### The joinable master list and the record of gaps (DATA-03, DATA-04)
- **`client_master`** is a **view** with one row per client and an array of references in each schema: `autotask_company_ids`, `ncentral_org_unit_ids`, `sentinelone_site_ids`, `cove_customer_ids`, so a query joins to any source in one step (arrays, because one client can have several N-central org units, for example). It is built from `api_clientsourcemap`, so there is still one place to maintain links, and it is always current. It is **created on request**, not by `migrate`: `python manage.py client_master_view` (PostgreSQL only; `--sql` prints the SQL and changes nothing; `--drop` removes it). A link whose ID is not a number is left out of the numeric arrays rather than making the view fail. Example: `SELECT m.client_name, count(*) FROM ncentral.device d JOIN report_manager.client_master m ON d.org_unit_id = ANY (m.ncentral_org_unit_ids) GROUP BY 1;`
- **Companies.** `api_company` holds the companies, `api_companymember` puts each person in one, and `api_client.company_id` says which company has **claimed** a client (empty = unclaimed, visible to administrators only). The companies migration makes the first company (named by `DJANGO_DEFAULT_COMPANY_NAME`, default `IP Capital`) and puts every existing person and client in it, so nothing changes the day it arrives. The platform owner is a Django superuser, not a table.
- **`api_clientgap`** keeps a lasting record of the gaps between the source systems and the master client list: `kind` (`unlinked`, `missing` or `stale`), `source`, `client_id` (for missing and stale ones), `external_id`, `label`, `first_seen_at`, `last_seen_at` and `resolved_at` (null while open). `key` says exactly which gap it is, and is unique among open gaps, so the same gap is recognised from visit to visit. It is brought up to date whenever the Client Resolution data is read, and resolved gaps are kept.

## Dates and timestamps

For imported records lacking a source date, do not manufacture a historical event time. Every table in the four collector schemas now has **`imported_at TIMESTAMPTZ`**, which records when the row was **written to this database**, alongside any real `captured_at` or source timestamp (**DATA-01**, approved 12 Nov 2026; the change is described under "imported_at" in `docs/05_OPERATIONS.md`). Rows stored before it was added have **NULL** there, meaning "imported before this was recorded": nothing is stamped after the event, and a missing value is never filled in. Many N-central columns are timestamp **without** time zone; the API treats those as UTC. Verify how the collector writes them before relying on data-age or trend calculations.

## Safe read-only verification queries

```sql
SELECT schemaname, relname, n_live_tup AS estimated_rows
FROM pg_stat_user_tables
WHERE schemaname IN ('autotask','ncentral','sentinelone','cove','report_manager')
ORDER BY 1,2;

SELECT 'autotask' AS source, max(captured_at) AS last_collected FROM autotask.snapshot
UNION ALL SELECT 'sentinelone', max(captured_at) FROM sentinelone.snapshot
UNION ALL SELECT 'cove', max(captured_at) FROM cove.snapshot;

SELECT run_id, started_at, finished_at, ok, device_count, entry_count, error
FROM ncentral.patch_run ORDER BY run_id DESC LIMIT 10;

SELECT source, count(*) FROM report_manager.api_clientsourcemap GROUP BY source;
SELECT run_id, client_id, captured_at, source, scores->>'overall' AS overall
FROM report_manager.api_patchsnapshot ORDER BY captured_at DESC LIMIT 10;
```
`n_live_tup` is an estimate. Query exact counts only when needed and approved for load.
