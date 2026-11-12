# Report Manager

`backend/` Django API (token auth, PostgreSQL) · `frontend/` Preact + Vite · `Collector/` the four source collectors
(Autotask, N-central, SentinelOne, Cove). **Documentation is in `docs/`**: read it all in one page with `python docs/serve.py` (or double-click `docs/Open-Docs.cmd`); the API is in `docs/08_API_REFERENCE.md`. `docs/STAGED_PLAN.md` says what is done and what is next.

## How the pieces fit
- The **collectors** write snapshots into one PostgreSQL schema per source: `autotask`, `ncentral`, `sentinelone`, `cove`.
- The **Django API** reads those schemas directly (`backend/api/data/`, one module per source) and owns the
  `report_manager` schema: users, refresh tokens and the client list.
- The **dashboard** only talks to the API. A **worker** (`manage.py collection_worker`) runs queued or scheduled collectors.

## Backend
    cd backend
    python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env                                    # then edit DATABASE_URL and REPORTS_DATABASE_URL
    python manage.py dbcheck                                # tests every configured connection
    python manage.py migrate
    python manage.py runserver                              # http://localhost:8000
    python manage.py test

If the database user cannot create tables, `python manage.py exportsql [--schema name]` writes `schema.sql` to run in pgAdmin.

## Frontend
    cd frontend
    npm install
    npm run dev                                             # proxies /api to API_PROXY_TARGET in frontend/.env

## Configuration (`backend/.env`; commit only `.env.example`)
- `DATABASE_URL`: app database (users, tokens, clients). `REPORTS_DATABASE_URL`: where the source schemas live.
- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `FRONTEND_URL`, and the `EMAIL_*` settings.
- Development switches, honoured only while `DJANGO_DEBUG=1`: `DJANGO_DEV_LOGIN_BYPASS` (default on in debug: any
  username and password signs in) and `DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL` (default off).
- Each collector has its own `.env` (see its `.env.example`). Real environment variables override `.env`.

## Access
- Signing in gives access to the reports. **Staff** users also get the Data Collection and Clients pages and can change
  clients, the shared score weights and run collectors: `python manage.py makestaff <username>` (`--remove` undoes it).
- Real accounts are the default: register on the sign-in page (open while `DJANGO_DEBUG=1`; set `DJANGO_REGISTRATION_OPEN=1`
  to allow it elsewhere) or `createsuperuser`. For local development you can set `DJANGO_DEV_LOGIN_BYPASS=1` so any username
  and password signs in; it is off by default and ignored unless `DJANGO_DEBUG=1`.
- Passwords must pass Django's validators (length 8, not common, not all digits, not similar to the username). Five failed
  sign-ins, or five reset requests, per address and username lock that combination for 15 minutes.
- `DJANGO_DEBUG` defaults to **off**. In production mode a real `DJANGO_SECRET_KEY` is required, HTTPS is enforced and
  HSTS is on. Run `python manage.py check --deploy` before going live.
- Companies and client access are built and **off by default**: with `DJANGO_CLIENT_ACCESS_ENFORCED=1` the platform owner sees everything, a company's administrators see their own company's clients (a wall between companies), and everyone else only the clients they were given. Follow `docs/05_OPERATIONS.md`, "Companies, the platform owner and turning on client access", before turning it on.
- `python manage.py verify_data` checks every dashboard section against the real databases (read-only).

## Clients
- A **client** is the master record. Link it to customers in the source systems by each system's own ID: Autotask company ID,
  N-central org unit ID, SentinelOne site ID, Cove customer ID. Add other spellings of its name as aliases.
- Matching order: ID link, then name or alias, otherwise unmapped. Names are never used in SQL joins.
- The **Client Resolution** page (staff) lists customers found in the sources that no client is linked to. They are grouped when their
  names have exactly the same words in the same order (capitals and punctuation ignored): "IP Capital: BITCO" and "IP Capital (BitCo)"
  group, "IP Capital: BITCO Holdings" does not. Every group is flagged as an inference; confirming links by ID and keeps other
  spellings as aliases. "Accept All" does this for every group at once. The page also reports clients missing from a source, and links
  that point at a customer that no longer exists.
- The master list is `report_manager`'s client tables (`Client`, `ClientSourceMap`, `ClientAlias`). The collectors' own `customers`
  tables stay where they are, because the collectors read them to decide whom to load.
- Tickets and servers carry `client_id`; patches and the patch trend can be filtered by client (needs an N-central link).

## Patch scores and trends
- Scores are calculated by the backend. A type's score is the weighted average of its categories' compliance; the overall score
  is the weighted average of the type scores. Weights default to 1; 0 leaves an item out.
- There is one **shared** weight set (staff edit it); each user can override it with their own. Both are saved on the server.
- After every successful N-central patch collection the worker saves a **snapshot** (all devices, plus each client linked to
  N-central) with the counts and the shared weights used. The trend reads these. Nothing is shown until snapshots exist.
- `python manage.py snapshot_scores` saves the latest run by hand; `--backfill [--limit N]` builds older runs from the history
  already in the database (marked `backfill`: they use today's definitions).

## Collectors
- Jobs and schedules live in `backend/collector_jobs.json`, **your own file, never shipped or overwritten by an archive**. Create it once by copying `backend/collector_jobs.example.json`. Every job has an `interval_minutes` (used for freshness).
  A job is only run automatically if `schedule_enabled` is true; all are `false` as shipped. Switch them on one at a time
  once the scheduled tasks are registered (recommended values are in `collector_jobs.example.json`).
- Shipped disabled: `autotask_tickets` (failing upstream) and `cove_backup` (new; enable after a manual run succeeds).
- Run the worker every minute and the alert check every five: either `python manage.py run_scheduler` (one process, no scripts) or two
  Windows scheduled tasks (`backend/deploy/` has a registration script and a guide, including what to do when scripts are blocked).
- Every worker tick records a heartbeat, shown on the Data Collection page. Failures, stuck runs, overdue data and a silent
  worker are emailed to `COLLECTOR_ALERT_EMAILS` once, as a daily reminder, and once more when they clear.
- Run one job by hand: `python manage.py run_collection <job>`. Autotask, SentinelOne and Cove have no dry-run mode and refuse `--dry-run`.
- Adding a collector: add it to `JOB_NAMES`, `JOB_COMMANDS`, `SOURCE_QUERIES`, `collector_jobs*.json` and the names in
  `CollectionHealth.jsx`. `api/tests_collection.py` fails if the first three disagree.
- Cove: see `Collector/Cove-main/README.md`. It needs `psql -f sql/schema.sql` and rows in `cove.customers` before its first load.

## Tests
`python manage.py test` (a new temporary database is created, so the `search_path` in `DATABASE_URL` is ignored for the run; set `TEST_DATABASE_URL=sqlite:///test.sqlite3` to test without PostgreSQL). Report data tests use a fake reporting database (`api/test_support.py`), so they need no source
schemas. `api/test_data.py`, `test_logic.py`, `tests_collection.py` and `test_collection_health.py` need no database at all. The test database needs a role that can create databases.

## Auth design
- Login returns a short-lived access token (kept in browser memory) and sets an httpOnly, SameSite=Strict refresh cookie.
- Refresh tokens rotate on every use; only hashes are stored. A 10 s leeway window accepts a just-rotated token and
  returns the same successor; reusing a rotated token after that revokes the whole session.
