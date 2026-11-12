# 5. Operations, deployment and collector runbook

## Environment, paths and dependencies

Environment-specific files are deliberately omitted from this documentation. Never commit `.env`, access tokens, database passwords or collector logs with customer data. Each folder with credentials has a `.env.example` listing the keys with placeholder values.

| Item | Location | Notes |
|---|---|---|
| Django configuration | `backend/.env` (from `backend/.env.example`) | Variables below. Real environment variables override the file |
| Source collector configuration | `Collector/<name>/.env` (from each `.env.example`) | API base URL/token and PostgreSQL DSN. Kept apart from the web app |
| Frontend dev proxy | `frontend/.env` | `API_PROXY_TARGET` (default `http://localhost:8000`); development server only |
| Job configuration | `backend/collector_jobs.json` (**yours: never shipped**; create it once by copying `collector_jobs.example.json`) | `source_directories`, per job `enabled`, `schedule_enabled`, `interval_minutes`, `timeout_seconds`, `args` |
| Persistent worker state | `backend/collector_runtime.sqlite3` | batches, batch jobs, schedules, **heartbeat**, **alert state** (never source data) |
| Collector logs | `backend/logs/collectors/<job>.log` | Append-only; run duration, status, errors. Protect access |
| Worker and scheduler locks | `backend/logs/collectors/worker.lock`, `scheduler.lock` | Prevent two copies running |

**Source path semantics:** `run_collection.py` resolves relative `source_directories` against the `collector_jobs.json` file's folder, not the shell's current directory. As shipped they are `../Collector/<name>`.

### Backend environment variables (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///db.sqlite3` | App database (users, tokens, clients, weights, snapshots). `postgres://user:pass@host:5432/db`; percent-encode special characters in the password. A `?options=-c%20search_path%3D<schema>` suffix selects a schema |
| `TEST_DATABASE_URL` | (blank) | Used only by `manage.py test`. Blank: tests run on a temporary database on `DATABASE_URL`'s server with its `search_path` option dropped (the role needs CREATEDB). A SQLite URL such as `sqlite:///test.sqlite3` runs the tests in memory with no PostgreSQL |
| `REPORTS_DATABASE_URL` | (blank) | Where the `autotask`, `ncentral`, `sentinelone` (and `cove`) schemas live. Blank uses the default database |
| `DJANGO_DEBUG` | `0` | `1` = development. Unset means production mode, which then requires a real secret key |
| `DJANGO_SECRET_KEY` | dev placeholder | Required (a long random value) when `DJANGO_DEBUG=0`. Signs access tokens and derives the refresh-token leeway successor; identical on every instance |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `DJANGO_DEV_LOGIN_BYPASS` | `0` | `1` makes login accept **any** username and password. Honoured only while `DJANGO_DEBUG=1` |
| `DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL` | `0` | `1` lets any signed-in user use the collector endpoints. Honoured only while `DJANGO_DEBUG=1`; prefer `manage.py makestaff` |
| `DJANGO_CLIENT_ACCESS_ENFORCED` | `0` | `1` turns on the company wall and the client limits: the platform owner sees everything, a company's administrators see their own company's clients, everyone else only the clients they were given. Off, everyone signed in sees every client. Do the steps in "Turning on client access" first: with it on, someone in no company sees nothing |
| `DJANGO_DEFAULT_COMPANY_NAME` | `IP Capital` | The name of the first company, made when the companies migration runs. Every existing person and client is put in it |
| `DJANGO_REGISTRATION_OPEN` | open in debug, closed otherwise | Whether `/api/auth/register` accepts new accounts |
| `DJANGO_SSL_REDIRECT` | `1` (production) | Redirect HTTP to HTTPS |
| `DJANGO_HSTS_SECONDS` | one year (production) | HSTS lifetime |
| `DJANGO_BEHIND_PROXY` | `0` | `1` trusts `X-Forwarded-Proto` from a reverse proxy that terminates HTTPS |
| `FRONTEND_URL` | `http://localhost:5173` | Used in password-reset links |
| `EMAIL_BACKEND` | console backend | Console prints emails; use `django.core.mail.backends.smtp.EmailBackend` for real mail |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | localhost / 25 / blank / blank / `0` | SMTP settings |
| `DEFAULT_FROM_EMAIL` | `Report Manager <no-reply@localhost>` | Sender of reset and alert emails |
| `THROTTLE_MAX_ATTEMPTS` | `5` | Failed sign-ins (or reset requests) allowed per address and username within the window |
| `THROTTLE_WINDOW_SECONDS` | `900` | The window |
| `COLLECTOR_ALERT_EMAILS` | (blank) | Comma-separated recipients of collector alerts. Blank: alerts show on the page but nothing is emailed |
| `COLLECTOR_HEARTBEAT_MAX_MINUTES` | `5` | A worker silent this long, while something is scheduled, raises an alert |
| `COLLECTOR_ALERT_REPEAT_HOURS` | `24` | Reminder interval while a problem lasts |
| `COLLECTOR_ALERT_ON_PARTIAL` | `0` | `1` also emails a warning when an N-central patch run is **partial** (some devices could not be found in N-central; they count as disconnected) |

## Collector jobs

| Job | Script (folder) | State as shipped | Notes |
|---|---|---|---|
| `ncentral_inventory` | `ncentral_inventory.py` (ncentral) | enabled, schedule off, 1440 min | Live success reported: 27 devices written for one customer |
| `ncentral_patches` | `ncentral_patch.py collect` (ncentral) | enabled, schedule off, 1440 min | A live run on 9 Nov found 58 devices, 1,074 entries and 6 device assets that answered 404: a PARTIAL run, whose 6 devices count as disconnected on Server Health. On SUCCESS or PARTIAL the worker saves the patch score snapshots |
| `ncentral_monitoring` | `ncentral_observability.py monitoring` (ncentral) | enabled, schedule off, 60 min | Live success reported (27 devices, 128 checks, 28 events) |
| `autotask_tickets` | `autotask_db.py load-active --days 120 --limit 500` (autotask) | **disabled** | Failing upstream: two HTTP 401, one HTTP 500 (an Autotask-side SMTP setting). No dry run |
| `sentinelone_monthly` | `s1_db.py load-active` (sentinelone) | enabled, schedule off, 10080 min | Command succeeded; stored content not verified. 10080 is the longest interval the scheduler accepts. No dry run |
| `cove_backup` | `cove_db.py load-active` (cove) | **disabled** | New. Not yet run against a live system. Needs `sql/schema.sql` applied and rows in `cove.customers` first. No dry run. The Backups page and the report's Backup Health section stay empty (and say why) until it has run |

`collector_jobs.example.json` holds the recommended values (schedules on for the three N-central jobs). The allowlist of runnable scripts is in `run_collection.py` (`JOB_COMMANDS`); adding a job needs a code change, a config entry and a name in the job lists (`api/tests_collection.py` fails if the lists disagree). Adding a JSON key alone does not expose a new executable.

**`--dry-run`** is supported only by the N-central jobs, and it still calls the external API. `NO_DRY_RUN` (Autotask, SentinelOne, Cove) are refused because those scripts would ignore the flag and do a **real** load.

## Running collection unattended

Two things must run: the **worker** (every minute: runs queued "Update all" requests and due jobs one at a time, records a heartbeat) and the **alert check** (every 5 minutes). Nothing runs on a schedule until a job has `"schedule_enabled": true`. Full steps are in `backend/deploy/README.md`.

| Option | Command | Use when |
|---|---|---|
| A. One process | `python manage.py run_scheduler` | Scripts are blocked, or you want one console. Does not restart itself |
| B. Windows Task Scheduler | `backend/deploy/Register-CollectorTasks.ps1`, or the two tasks created by hand | A server that must restart by itself |

- `run_scheduler` is its own process, **not** a timer inside the web server (a web server can run several processes, restarts on code changes and recycles workers). It starts a worker each minute unless one is still running, runs the alert check every 5 minutes even during a long batch, and refuses to start twice.
- If PowerShell refuses the script ("not digitally signed"), that is the execution policy: `Unblock-File`, or `powershell -ExecutionPolicy Bypass -File ...`; under an `AllSigned` Group Policy neither works and option A or the Task Scheduler GUI is the way.
- No scheduling library is used: the rules (an interval per job) already live in `collector_jobs.json`. APScheduler would be worth adding only for fixed times ("daily at 02:00"). Windows has no cron; Task Scheduler is its equivalent.
- **Worker behaviour:** exclusive worker lock, then mark interrupted RUNNING batches failed, then drain the queued batch (or find due jobs), run each as a subprocess, append logs, update the queue. A tick that finds another worker busy exits normally and records a `BUSY` heartbeat. While a collector runs, the worker records a heartbeat every 30 seconds, so a long collection never looks like a dead worker.
- **Known behaviour:** `due_jobs()` advances a job's next-run time when it selects it, so a batch that cannot be enqueued is skipped until the next interval (no automatic retry).

## Alerts

`python manage.py collection_alerts` emails problems to `COLLECTOR_ALERT_EMAILS`. `--dry-run` prints without sending or recording; `--test-email` checks the mail settings. The same list shows on the Data Collection page and in `GET /api/collectors/health`.

| Alert key | Raised when |
|---|---|
| `worker:never`, `worker:stale` | Something is scheduled and the heartbeat is missing, or older than `COLLECTOR_HEARTBEAT_MAX_MINUTES` |
| `<job>:failed` | An enabled job's last run FAILED |
| `<job>:stuck` | A run started and never finished within 1.5 times its timeout |
| `<job>:overdue` | A scheduled job's last good run (succeeded or partial, not a dry run) is older than 1.5 times its interval plus 10 minutes. A failed run does not reset this. Without a good run in the log, the newest stored timestamp is used instead |
| `<job>:nodata` | A scheduled job has no successful run and no stored data yet (warning) |
| `<job>:partial` | Only with `COLLECTOR_ALERT_ON_PARTIAL=1` (warning): some devices could not be found in N-central |

Each problem is emailed once, again as a reminder after `COLLECTOR_ALERT_REPEAT_HOURS`, and once when it clears. State is recorded only after the email was actually sent, so a failed send is retried on the next run. Disabled jobs are never reported.

## Operator commands

From `backend`, with the virtual environment active:

```powershell
python manage.py dbcheck                 # test every database connection; shows user, schema and CREATE privilege
python manage.py migrate                 # create or update the app tables
python manage.py exportsql [--schema x]  # write schema.sql to run in pgAdmin if the role cannot create tables
python manage.py makestaff <username>    # give (or --remove) staff access
python manage.py run_collection <job>    # run one collector now (real upstream calls, real writes)
python manage.py collection_worker       # one worker tick
python manage.py run_scheduler           # worker every minute + alert check every 5 minutes, until Ctrl+C
python manage.py collection_alerts       # check and email problems (--dry-run, --test-email)
python manage.py snapshot_scores         # save the latest patch score snapshot (--backfill [--limit N])
python manage.py verify_data            # check every dashboard section against the real databases, read-only (--verbose for full errors)
python manage.py company list           # companies, who is in them, and the platform owner (also: create, add-user, claim, owner; see the section on companies)
python manage.py client_access list      # who may see which clients (also: grant <user> <client>, revoke <user> <client>)
python manage.py client_master_view      # create the client_master view for SQL joins (--sql prints it, --drop removes it; PostgreSQL only)
python manage.py test                    # the test suite
python manage.py check --deploy          # production configuration review
python docs/serve.py                     # (from the project folder) read the documentation in one page
```

Frontend (`frontend/`): `npm install`, `npm run dev`, `npm run build`, `npm test`.

## Making people, and client groups (ACC-03 and ACC-02: make people from the Access page, and group clients)
- **Before first use:** `python manage.py migrate` adds the table for client groups (migration `0008_clientgroup`). Nothing else changes until you use the Access page.
- **No password is ever chosen for a new person.** The account starts with an unusable password and a one-time link to the choose-a-password page (`/reset`) is emailed and shown once to you. The link works once and for 60 minutes (`PASSWORD_RESET_TIMEOUT`, shared with the forgot-password link); the person can also use "Forgot password" on the sign-in page, or you can press New Link.
- **Email needs real settings.** With the default `EMAIL_BACKEND` (the console backend) nothing is emailed: the server only prints the message, and the page says so and tells you to hand over the link. For real email set `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and `EMAIL_USE_TLS` in `.env`, and `FRONTEND_URL` to the address people use, because it is the start of every link.
- **Deactivate, never delete.** A deactivated person is signed out everywhere at once and cannot sign in until reactivated; their reports and history stay.
- **What a person sees** (only once `DJANGO_CLIENT_ACCESS_ENFORCED=1`): the clients of every client group they are in, plus any given one by one, inside their own company. An "All clients" group is every client of the company, including ones made later. Administrators see every client of their company. Until the limits are switched on, everyone sees every client.

## Applying `imported_at` (DATA-01: record when each row was written to the database, approved 12 Nov 2026)

Every table in the four collector schemas gets a column `imported_at`, the time the row was **written to the database** (as opposed to `captured_at`, when the source data was captured). **It is a change to your live databases, so it is yours to apply**, and nothing in the dashboard needs it first: the dashboard works with or without it.

- **What it does.** `ALTER TABLE <table> ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;` then `ALTER TABLE <table> ALTER COLUMN imported_at SET DEFAULT now();` for each of the 39 tables. **Existing rows are not touched:** they keep NULL, meaning "imported before this was recorded", because nobody knows when they were imported and stamping them today would be a lie. Rows written from then on get the time they were inserted. The collectors need **no change**: they name their columns when they insert (a test checks this), so the new column takes its default.
- **How to apply it.** For each collector, from its folder: `psql "<connection string>" -f sql/migrations/2026-11-12_imported_at.sql`. It is one transaction (all or nothing), safe to run again, and takes a very short lock on each table, so run it when no collector is running. Re-running `sql/schema.sql` does the same thing, since the same statements are in it, just before the views.
- **How to check.** `python manage.py verify_data` has an `imported_at` line: **OK** (every table has it), **EMPTY** (not applied yet) or **FAILED** (applied to only some tables: run it again).
- **Views.** A view written as `SELECT t.*` picks the column up the next time the schema is applied. SentinelOne drops and re-creates its views each time; the others are replaced in place, which is allowed because the new column is last. A test fails if a view could be broken by a new column.
- **Adding a table later.** Run `python docs/tools/generate_imported_at.py`; a test fails until you do, so no table is missed.
- **Undoing it.** `ALTER TABLE <table> DROP COLUMN imported_at;` for each table. Nothing depends on it.

## Checking against real data

**"Data as of" on every page.** The Overview, Server Health, Backups and both ticket pages show when their data was last collected and say **Stale** once it is older than the job's `interval_minutes` in `collector_jobs.json`. If a page reads Stale when it should not (a monthly SentinelOne job with the shipped weekly interval will after eight days), change that job's `interval_minutes`, not the page.

`python manage.py verify_data` calls every dashboard section against the live databases (nothing is written) and reports each as **OK**, **EMPTY** (it worked but there is nothing there yet, such as Cove before its first load), **FAILED** (a real problem: a column that is not there, a table that cannot be read) or **SKIPPED** (nothing to open). For Server Health it also reads each server's patching, and reports a **FAILED** if the patch tables cannot be read (the page itself would still load, without patching), and how many servers were matched to an N-central device. It shows how many tickets, endpoints and devices are not yet linked to a client, and each collector's last good run and freshness. It runs without any client limit, as an administrator's check. The exit code is 1 if anything failed. If something fails, run it again with `--verbose` and send the output.

## Companies, the platform owner and turning on client access

**The model.** A company is whoever a person works for. Clients belong to a company once it has claimed them; people belong to one company. Once client access is enforced a company is a wall: nobody, administrators included, sees another company's data. The **platform owner** (a Django superuser) sits above the companies and sees everything. Company **administrators** (staff in a company) see all of their own company's clients, run the data collectors, and claim clients nobody has claimed yet. Everyone else sees only the clients their administrator gave them. Details and every rule are in `docs/08_API_REFERENCE.md`, "Companies and access to clients".

**What the migration does.** `python manage.py migrate` makes the first company (`DJANGO_DEFAULT_COMPANY_NAME`) and puts every existing person and client in it, so nothing changes the day it arrives, and nothing is limited until you turn enforcement on.

**Turning it on, in this order:**
1. `python manage.py migrate`, then check `python manage.py company list`: one company, all your people, all your clients.
2. **Make yourself the platform owner:** `python manage.py company owner <your username>`. Do this before enforcing: with it on, an administrator only sees their own company, and without an owner nobody can make companies or place a new person.
3. Check that your clients are all claimed (the Clients page shows "Unclaimed" for any that are not) and give each member their clients on the **Access** page (or `python manage.py client_access grant <username> "<client>"`).
4. Check with a test account. With enforcement still off nothing changes, so nothing is risked yet.
5. Set `DJANGO_CLIENT_ACCESS_ENFORCED=1` in `backend/.env` and restart the backend. Members now see only their clients and a note on every page tells them so; administrators see their company; you see everything.
6. To go back, set it to `0` (or remove it) and restart: nothing is lost, companies and grants stay.

**After it is on:** a new person has no company and sees nothing until the owner places them (Access page, or `python manage.py company add-user <user> "<company>"`). A client created by an administrator joins their company; clients that appear from the resolution page join the administrator's company too. A client nobody has claimed is visible to administrators only, on the Clients page under "Unclaimed Clients", where an administrator claims it or the owner assigns it.

**Data collection.** Company administrators can see collector status and run the collectors (which carry no customer names). The **raw log** of a run is the platform owner's, because it can name customers of every company.

**Do not put a second organisation on this system yet.** The shared score weights and the shared report templates are still shared by everybody (ACC-15 in `docs/06_STATUS_AND_BACKLOG.md`), and every company reads the same source databases (FUT-01). Branches of one group are fine; an unrelated company is not, until those two are done.

Someone with no clients sees no data and an empty client list. Reports are made in the browser from the same limited data, so a report run by a limited person only contains their clients. One thing the limit does not hide: a report **shared** by an administrator is visible to everyone signed in, with any client names in its filters.

## The master client view (`client_master`)

For queries that join across schemas, the `client_master` view lists every client with its references in each system as arrays. It is optional and **created on request**: run `python manage.py client_master_view --sql` to read the SQL first, then `python manage.py client_master_view` to create it (it prints the schema it landed in and some example queries); `--drop` removes it. It needs PostgreSQL. Re-running it is safe. A client's links are still maintained on the Clients and Client Resolution pages.

## Technician data

The ticket pages can show who a ticket is assigned to, but **the source does not provide it yet**: the Autotask collector requests a fixed field list with no assignee, and `autotask.ticket` has no assignee column. The backend checks on every request whether `autotask.ticket.assigned_resource_id` (and an `autotask.resource` name table) exist and starts returning technicians the moment they do; until then `technician_available` is `false` and the Technician Tickets page says so instead of inventing data.

To enable it (needs your approval, because it changes the live `autotask` schema, and a working Autotask API to verify the field names):
1. Run `Collector/AutotaskLight-main/sql/proposed/technician.sql` (adds the column and the resource table; safe to run twice).
2. Change the collector: request `assignedResourceID`, copy it into the ticket row, add `assigned_resource_id` to `TICKET_COLUMNS`, and load the Resources entity into `autotask.resource`.
3. Re-run `autotask_tickets`. Existing tickets keep a blank assignment until re-collected.

## Error runbooks

| Symptom | Immediate diagnostic | Remedy |
|---|---|---|
| Update All stays QUEUED | The Data Collection page shows "Worker last ran"; check the scheduler or task | Start `run_scheduler`, or repair the scheduled task (account, working folder, permissions) |
| "Worker has never run" alert | Heartbeat is empty | The worker has not ticked since the queue file was created; start the scheduler |
| `migrate` fails: permission denied for schema public | `python manage.py dbcheck` shows `can_create_tables=False` | Grant CREATE on the schema, use your own schema via `?options=-c%20search_path%3D<schema>`, or `exportsql` and run the file in pgAdmin |
| `manage.py test` fails with "no schema has been selected to create in" | `DATABASE_URL` has `?options=-c%20search_path%3D<schema>`, and the test run's new empty database has no such schema | Fixed in the settings: the option is dropped for test runs. If you still see it, check you are on the latest archive; or set `TEST_DATABASE_URL=sqlite:///test.sqlite3` to test without PostgreSQL |
| The `test_docs` source-index test fails and names a file | The file is on disk but not in the index. Either it is new, or it is **old**: unzipping an archive never deletes files | Delete obsolete files by hand (see the list under "Files an archive does not delete"), or regenerate with `python docs/tools/generate_source_index.py` |
| A collector fails with `ConnectTimeoutError` (the log shows the host and "connect timeout=15") | The computer running it cannot open a connection to the source at all: not a wrong password (that would be a 401) and not a refused connection | Network, not code: `Test-NetConnection <host> -Port 443` from that computer; check VPN, firewall or IP allow-list at the source, and that DNS gives the address you expect. If every job for that source fails, it is connectivity; the other sources working points the same way |
| The front end shows a red build error such as `The symbol "x" has already been declared`, or a name "is not defined" | A mistake in the source (a name used twice in one function, a missing import) that only shows when the page is built | `cd frontend`, then `npm test` (the declarations test names the file and line), or `npm install --save-dev typescript` once and `npm run check`, which also finds names that are used but never declared. Send me the file and line |
| Login returns 429 | Too many failed attempts | Wait out the window (default 15 minutes) |
| Data Collection page returns 403 | The account is not staff | `python manage.py makestaff <username>` |
| N-central patches shows Partial (hover for the number of devices) | `ncentral_patches.log`, `ncentral.patch_run.ok`, per-device 404 count | Those devices cannot be found in N-central, so they count as **disconnected**, which Server Health shows on each one's card (Patches); every other device's inventory was collected and the run counts as a good one. Nothing is deleted. Remove or reconnect the devices in N-central |
| Autotask HTTP 401 / 500 | Capture the exact request and time | Autotask administrators or vendor; the job is disabled meanwhile |
| Patch trend empty | No snapshots yet | Run a successful patch collection, or `manage.py snapshot_scores --backfill` |
| Cove job fails on first run | `cove.customers` missing or empty, or credentials | Apply `Collector/Cove-main/sql/schema.sql`, add customers, set `COVE_USERNAME` / `COVE_PASSWORD` (the old `Login` / `SECRET_KEY` names also work) |
| PDF omits values | API payload and filters | No fabricated defaults: fix the source or the source-backed calculation |

## Files an archive does not delete

Unzipping an archive adds and replaces files but never removes any, so a file that a newer archive dropped stays in your folder. Delete these by hand if you still have them (none is used any more):
`backend/api/data.py` (now the `backend/api/data/` package), `backend/api/views_old.py`, `backend/api/urls_old.py`, `backend/test_collection_health.py`, `frontend/src/pages/ReportBuilder.jsx` (replaced by `Reports.jsx`), `docs/diagrams/er_planned_client_master.mmd` (now `er_client_master.mmd`), `frontend/src/components/ScoreTable.jsx` (the Overview's Scores By Client replaced it).
Never delete `backend/collector_jobs.json`: it is yours, and archives no longer contain it.

## Restore and safe changes

1. Back up the source schemas and the app database before migrations or collector changes; test the restore.
2. Never run the legacy N-central performance `init` against a populated database (destructive).
3. Never delete 404 devices automatically; the policy decision is pending.
4. Never run collectors in web request threads, or from shell commands built from browser input.
5. Keep the schedule off for failing collectors; healthy ones continue independently.
6. Collectors write to the original source schemas and the dashboard reads them; do not refresh stale copies in `report_manager`.
7. Test source-specific dry runs where they exist, then a controlled live run, then scheduled execution with alerting.
8. When replacing a project folder from an archive, **merge** into the existing folder: an archive never contains your `.env` files, logs or `node_modules`, so replacing the folder would delete them.

## Deployment and operations gaps

No verified production hosting topology, service manager, tested backup/restore, incident rota, CI pipeline or dedicated service-account permissions were supplied. Document them from the running environment before production signoff. The queue is a local SQLite file, so the API and the worker must run on the same machine or share the folder.
