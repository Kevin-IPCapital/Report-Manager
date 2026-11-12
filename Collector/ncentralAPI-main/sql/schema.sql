-- ===========================================================================
-- N-central reporting schema — performance and patching
--
-- One file, one `customers` table, applied by either collector:
--     python ncentral_performance.py init
--     python ncentral_patch.py init
-- Both run the same file, so it does not matter which goes first.
--
-- !! NOT IDEMPOTENT ANY MORE — `init` DESTROYS PERFORMANCE HISTORY. !!
-- The DROP block below the customers/sites tables rebuilds the performance
-- tables from scratch on every run, so device_poll, perf_check and perf_event are
-- emptied each time this file is applied. That history cannot be re-fetched: the
-- API exposes only current monitor state, so past polls and state transitions
-- exist nowhere else. Read the block's own comment before running init again.
-- (The scheduled collectors call `collect`, not `init`, so Task Scheduler does
-- not trip this — only a manual init does.)
--
-- WHAT THIS CANNOT DO — read before building a report on it. N-central exposes
-- monitor *state*, never a measured value. Confirmed against the REST API
-- (?includeData / ?details / ?expand are silently ignored; lastDataId is null on
-- every monitor in the estate) and against the legacy SOAP deviceGetStatus,
-- which returns the same 19 fields. So this answers "Disk C: was in Warning for
-- 14 hours" and never "C: is 8% free". Time in breach, not utilisation.
--
-- ---------------------------------------------------------------------------
-- Two collectors, two cadences, two grains:
--
--   PERFORMANCE   hourly.  State-transition driven. Every monitor reports the
--                 exact moment it entered its current state, so an hourly poll
--                 still yields second-accurate breach boundaries and writes a
--                 row only when something changed.
--                 Availability comes free from the same poll: if a device's
--                 monitors scanned recently the agent reported in, so it was up.
--                 `device_poll` records that per device per poll — without it a
--                 dead server would leave no rows and downtime would look like
--                 missing data rather than an outage.
--
--   PATCHING      daily.  An inventory, not a state machine. Sourced from the
--                 patch section of /api/devices/{id}/assets, because there is no
--                 patch endpoint (/api/patches, /api/patch-status,
--                 /api/devices/{id}/patch-status all 404). Windows only; Linux
--                 returns an empty section and some devices 404 outright, and
--                 both are recorded as such rather than as "0 missing".
--
-- The patch tables carry no foreign key into `device` and denormalise device
-- identity instead, so either collector can run without the other having ever
-- run.
--
-- All timestamps are NAIVE LOCAL time. The API returns transitionTime without a
-- zone and the collectors normalise everything to naive local to match, so no
-- value here is ever silently reinterpreted as UTC. Durations end at the last
-- successful poll rather than now(), so a stopped collector cannot inflate
-- anything.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS ncentral;
SET search_path TO ncentral, public;

-- ---------------------------------------------------------------------------
-- Driver table — which customers to collect. Shared by both collectors.
-- Populate with any of:
--     python ncentral_clients.py sync
--     python ncentral_performance.py sync-customers
--     python ncentral_patch.py sync-customers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    org_unit_id     BIGINT      PRIMARY KEY,      -- N-central orgUnitId
    customer_name   TEXT        NOT NULL,
    created_at      TIMESTAMP   NOT NULL DEFAULT localtimestamp,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- Sites, and which customer each belongs to. Not a driver table — collection is
-- still per customer, because /api/org-units/{customer}/devices already returns
-- every descendant site's devices tagged with its own siteId/siteName. This is
-- the lookup that turns those tags into "which client is this site under", and
-- the answer to "what sites does customer X have".
-- Populate with:
--     python ncentral_sites.py sync
--
-- No foreign key to customers, deliberately: a site whose parent customer is
-- missing from `customers` (permissions, paging, or a site parented straight to
-- a service org) must still be recorded rather than abort the whole sync. Same
-- reasoning as the patch tables denormalising device identity.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    site_id         BIGINT      PRIMARY KEY,      -- orgUnitId of the SITE
    site_name       TEXT        NOT NULL,
    parent_id       BIGINT,                       -- immediate parent org unit
    customer_id     BIGINT,                       -- nearest CUSTOMER ancestor
    customer_name   TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT localtimestamp,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS sites_customer_idx ON sites (customer_id);

-- ===========================================================================
-- PERFORMANCE
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- REBUILD BLOCK — deliberate, and destructive.
--
-- CREATE TABLE IF NOT EXISTS cannot add a column to a table that already exists,
-- so these are dropped and rebuilt to pick up device.site_id.
--
-- WHAT THIS DELETES, every time init runs:
--   device        the device dimension
--   device_poll   the availability record — every past poll
--   poll_run      collector run history
--   perf_check    the monitor inventory
--   perf_event    every recorded state transition
-- None of it is recoverable. N-central returns current state only, so 30-day
-- uptime and time-in-breach are rebuilt from zero and stay thin until enough
-- hourly polls accumulate again.
--
-- CASCADE is required because the tables reference each other and the
-- performance views depend on them; the views are recreated further down.
--
-- Deliberately NOT dropped: `customers` (the driver table — dropping it stops
-- collection until re-synced), `sites`, and every patch table. The patch side
-- gains nothing from this rebuild, and patch_backlog.first_missing_at is
-- unrecoverable ageing data, so it is left intact.
--
-- Once your database has site_id, delete this block. The ALTER further down
-- keeps future column additions non-destructive.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS perf_event, perf_check, device_poll, poll_run, device CASCADE;

-- ---------------------------------------------------------------------------
-- Device dimension — current identity, overwritten each poll.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device (
    device_id       BIGINT      PRIMARY KEY,
    device_name     TEXT,
    device_class    TEXT,
    os              TEXT,
    -- NULL is a real answer, not a gap: N-central attaches many devices straight
    -- to the customer and reports siteId null for them (9 of 22 on this estate).
    site_id         BIGINT,                      -- orgUnitId of the device's site
    site_name       TEXT,
    customer_name   TEXT,
    org_unit_id     BIGINT,
    device_uri      TEXT,
    last_checkin    TIMESTAMP,
    last_scan       TIMESTAMP,                   -- newest scan across its monitors
    monitor_count   INTEGER     NOT NULL DEFAULT 0,
    perf_check_count INTEGER    NOT NULL DEFAULT 0,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP
);

CREATE INDEX IF NOT EXISTS device_customer_idx ON device (customer_name);
CREATE INDEX IF NOT EXISTS device_site_idx ON device (site_id);

-- Column upgrades. A no-op while the REBUILD BLOCK above is in place, since the
-- CREATE TABLE already carries site_id — kept because it is what makes the next
-- column addition non-destructive, and it is what still applies once that block
-- is deleted. Needs Postgres 9.6+.
ALTER TABLE device ADD COLUMN IF NOT EXISTS site_id BIGINT;

-- ---------------------------------------------------------------------------
-- One row per poll. A missing hour must read as "not measured", never "fine".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS poll_run (
    poll_id         BIGSERIAL   PRIMARY KEY,
    started_at      TIMESTAMP   NOT NULL,
    finished_at     TIMESTAMP,
    org_unit_id     BIGINT,
    device_count    INTEGER     NOT NULL DEFAULT 0,
    check_count     INTEGER     NOT NULL DEFAULT 0,
    event_count     INTEGER     NOT NULL DEFAULT 0,
    ok              BOOLEAN     NOT NULL DEFAULT TRUE,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS poll_run_started_idx ON poll_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- Per-device sample, one row per poll. This is the availability record.
--
-- `reporting` is the liveness verdict: the device's newest monitor scan is
-- fresher than the collector's threshold, i.e. it was alive and reachable when
-- polled. `scan_age_mins` is kept so a borderline call can be re-judged later
-- without re-polling.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_poll (
    poll_id         BIGINT      NOT NULL REFERENCES poll_run (poll_id) ON DELETE CASCADE,
    device_id       BIGINT      NOT NULL REFERENCES device (device_id) ON DELETE CASCADE,
    polled_at       TIMESTAMP   NOT NULL,
    -- Three-valued on purpose. NULL = could not tell (API error, or the device
    -- has no monitors at all), which stays out of the uptime denominator instead
    -- of being counted as downtime.
    reporting       BOOLEAN,
    last_scan       TIMESTAMP,
    scan_age_mins   NUMERIC(10, 1),
    monitor_count   INTEGER     NOT NULL DEFAULT 0,
    breach_count    INTEGER     NOT NULL DEFAULT 0,  -- performance checks not OK
    error           TEXT,

    PRIMARY KEY (poll_id, device_id)
);

CREATE INDEX IF NOT EXISTS device_poll_device_idx ON device_poll (device_id, polled_at DESC);
CREATE INDEX IF NOT EXISTS device_poll_down_idx   ON device_poll (device_id)
    WHERE reporting IS NOT TRUE;

-- ---------------------------------------------------------------------------
-- The performance monitors being tracked, one row per (device, task).
-- task_ident separates same-named monitors: Disk C: vs Disk D:, DIMM.Socket.A1.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perf_check (
    device_id        BIGINT     NOT NULL REFERENCES device (device_id) ON DELETE CASCADE,
    task_id          BIGINT     NOT NULL,
    module           TEXT       NOT NULL,        -- CPU / Memory / Disk / Disk I/O / ...
    task_ident       TEXT,
    service_id       BIGINT,
    stale_after_mins INTEGER,
    first_seen       TIMESTAMP,
    last_seen        TIMESTAMP,

    PRIMARY KEY (device_id, task_id)
);

CREATE INDEX IF NOT EXISTS perf_check_module_idx ON perf_check (module);

-- ---------------------------------------------------------------------------
-- Performance state changes. One row per observed transition.
--
-- state_class:
--   ok        Normal, no active issue
--   degraded  Warning
--   breached  Failed, or the monitor appears in active-issues
--   unknown   No Data / Misconfigured / Stale — not reporting, which is not a
--             breach and is excluded from breach percentages
--
-- The active-issues cross-reference is required, not cosmetic: monitors sit at
-- stateStatus 'No Data' while genuinely in alarm, so trusting stateStatus alone
-- reports real breaches as "unknown".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perf_event (
    event_id        BIGSERIAL   PRIMARY KEY,
    device_id       BIGINT      NOT NULL,
    task_id         BIGINT      NOT NULL,
    transition_time TIMESTAMP   NOT NULL,
    state           TEXT,
    state_class     TEXT        NOT NULL,
    in_active_issue BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Same class as the previous event = a full round trip happened between two
    -- polls. Detected, but its duration is bounded by the poll interval rather
    -- than measured.
    flap_suspected  BOOLEAN     NOT NULL DEFAULT FALSE,
    first_seen_poll BIGINT      REFERENCES poll_run (poll_id),
    recorded_at     TIMESTAMP   NOT NULL,

    FOREIGN KEY (device_id, task_id) REFERENCES perf_check (device_id, task_id) ON DELETE CASCADE,
    -- Dedup key: re-polling an unchanged monitor inserts nothing.
    UNIQUE (device_id, task_id, transition_time)
);

CREATE INDEX IF NOT EXISTS perf_event_check_idx ON perf_event (device_id, task_id, transition_time);
CREATE INDEX IF NOT EXISTS perf_event_class_idx ON perf_event (state_class) WHERE state_class <> 'ok';

-- ===========================================================================
-- METRICS — actual measured values (CPU %, RAM %), via Automation Manager
--
-- The REST API returns monitor STATE and nothing else. Confirmed on 2026.3.1.10:
-- every service-monitor row has lastDataId and lastUpdate null, /assets carries
-- inventory only (CPU model, core count, OS build — no utilisation), and the
-- device resource advertises only `assets` and `scheduled-tasks`. Every
-- metric-shaped endpoint tried returns 404.
--
-- So real numbers have to come the other way round: an Automation Manager policy
-- samples the agent and writes a reading into a device custom property, which
-- the API DOES expose. ncentral_metrics.py reads that property and lands it here.
--
-- The property holds one JSON reading — see amp_perf_sample.ps1:
--     {"ts":"2026-08-17T09:00:00","cpu_pct":91.2,"mem_pct":78.4, ...}
--
-- ts is the AMP's own sample time and is why this is trustworthy. A custom
-- property keeps its last value forever, so a dead AMP leaves a stale number
-- sitting there looking current. Keying on the AMP's timestamp instead of
-- collection time means re-reading an unchanged property inserts nothing, and a
-- stopped AMP shows as a gap rather than as a flat line of repeated readings.
--
-- Cadence is the AMP schedule, not the collector's: reading twice as often as
-- the AMP samples yields the same rows, not twice the resolution.
-- ===========================================================================

-- One row per ncentral_metrics.py collect, so a gap is visible as a gap.
CREATE TABLE IF NOT EXISTS metric_run (
    run_id          BIGSERIAL   PRIMARY KEY,
    started_at      TIMESTAMP   NOT NULL,
    finished_at     TIMESTAMP,
    org_unit_id     BIGINT,
    device_count    INTEGER     NOT NULL DEFAULT 0,  -- devices examined
    sample_count    INTEGER     NOT NULL DEFAULT 0,  -- readings found
    new_count       INTEGER     NOT NULL DEFAULT 0,  -- readings not already stored
    stale_count     INTEGER     NOT NULL DEFAULT 0,  -- readings older than the limit
    missing_count   INTEGER     NOT NULL DEFAULT 0,  -- devices with no property set
    ok              BOOLEAN     NOT NULL DEFAULT TRUE,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS metric_run_started_idx ON metric_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- The readings themselves.
--
-- No foreign key into `device`, and device identity is denormalised — same
-- reasoning as the patch tables: this collector must run whether or not the
-- performance collector ever has, and the rebuild block above would otherwise
-- silently drop the constraint.
--
-- PRIMARY KEY (device_id, sampled_at) is the dedup key: collecting the same
-- unchanged property twice inserts once.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perf_sample (
    device_id       BIGINT      NOT NULL,
    sampled_at      TIMESTAMP   NOT NULL,   -- the AMP's clock, not the collector's
    collected_at    TIMESTAMP   NOT NULL,   -- when we read the property
    run_id          BIGINT      REFERENCES metric_run (run_id) ON DELETE SET NULL,

    device_name     TEXT,
    customer_name   TEXT,
    site_id         BIGINT,
    site_name       TEXT,

    cpu_pct         NUMERIC(5, 2),
    memory_pct      NUMERIC(5, 2),
    memory_total_mb BIGINT,
    memory_used_mb  BIGINT,
    -- Per-volume free space, keyed by drive letter: {"C:": 8.4, "D:": 61.0}.
    -- JSONB rather than columns because the volume set differs per device and
    -- changes without warning.
    disks           JSONB,
    -- The reading exactly as the AMP wrote it. Kept so a field added to the
    -- script later is already captured before anyone changes this table.
    payload         JSONB,

    PRIMARY KEY (device_id, sampled_at)
);

CREATE INDEX IF NOT EXISTS perf_sample_time_idx     ON perf_sample (sampled_at DESC);
CREATE INDEX IF NOT EXISTS perf_sample_device_idx   ON perf_sample (device_id, sampled_at DESC);
CREATE INDEX IF NOT EXISTS perf_sample_customer_idx ON perf_sample (customer_name);

-- ===========================================================================
-- PATCHING
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- One row per patch collection run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patch_run (
    run_id              BIGSERIAL   PRIMARY KEY,
    started_at          TIMESTAMP   NOT NULL,
    finished_at         TIMESTAMP,
    org_unit_id         BIGINT,
    device_count        INTEGER     NOT NULL DEFAULT 0,  -- devices examined
    patched_device_count INTEGER    NOT NULL DEFAULT 0,  -- devices with an inventory
    entry_count         INTEGER     NOT NULL DEFAULT 0,
    missing_count       INTEGER     NOT NULL DEFAULT 0,
    ok                  BOOLEAN     NOT NULL DEFAULT TRUE,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS patch_run_started_idx ON patch_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- Per-device patch position, one row per run.
--
-- coverage says why a device has no inventory, so an unpatched-by-design box is
-- never read as compliant:
--   windows      inventory present
--   no-inventory device returned an empty patch section (Linux, appliances)
--   unavailable  /assets 404'd or errored for this device
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_patch (
    run_id                  BIGINT   NOT NULL REFERENCES patch_run (run_id) ON DELETE CASCADE,
    device_id               BIGINT   NOT NULL,
    captured_at             TIMESTAMP NOT NULL,
    device_name             TEXT,
    device_class            TEXT,
    os                      TEXT,
    customer_name           TEXT,
    site_name               TEXT,
    coverage                TEXT     NOT NULL,

    entry_count             INTEGER  NOT NULL DEFAULT 0,
    installed_count         INTEGER  NOT NULL DEFAULT 0,
    missing_count           INTEGER  NOT NULL DEFAULT 0,
    -- Split out because they are different conversations: a missing security
    -- patch is a risk item, a missing third-party app version is housekeeping,
    -- and Defender definition churn is neither.
    missing_security_count  INTEGER  NOT NULL DEFAULT 0,
    missing_windows_count   INTEGER  NOT NULL DEFAULT 0,
    missing_thirdparty_count INTEGER NOT NULL DEFAULT 0,
    missing_definition_count INTEGER NOT NULL DEFAULT 0,

    last_installed_at       TIMESTAMP,
    days_since_last_install NUMERIC(10, 1),
    undated_installed_count INTEGER  NOT NULL DEFAULT 0,
    patch_monitor_states    JSONB,                       -- Patch Status v2, PME, ...
    reboot_monitor_state    TEXT,
    error                   TEXT,

    PRIMARY KEY (run_id, device_id)
);

CREATE INDEX IF NOT EXISTS device_patch_device_idx   ON device_patch (device_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS device_patch_customer_idx ON device_patch (customer_name);

-- ---------------------------------------------------------------------------
-- Every patch entry, per run. The detail behind the counts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patch_entry (
    run_id          BIGINT   NOT NULL REFERENCES patch_run (run_id) ON DELETE CASCADE,
    device_id       BIGINT   NOT NULL,
    title           TEXT     NOT NULL,
    category        TEXT,                -- Security Updates / Definition Updates / ...
    category_id     TEXT,                -- raw WSUS classification GUID
    result          TEXT,                -- Installed / Not Installed
    is_missing      BOOLEAN  NOT NULL DEFAULT FALSE,
    is_security     BOOLEAN  NOT NULL DEFAULT FALSE,
    is_third_party  BOOLEAN  NOT NULL DEFAULT FALSE,
    installed_at    TIMESTAMP,
    kb              TEXT,                -- KB number pulled out of the title, when present

    PRIMARY KEY (run_id, device_id, title)
);

CREATE INDEX IF NOT EXISTS patch_entry_missing_idx  ON patch_entry (device_id) WHERE is_missing;
CREATE INDEX IF NOT EXISTS patch_entry_category_idx ON patch_entry (category);
CREATE INDEX IF NOT EXISTS patch_entry_kb_idx       ON patch_entry (kb);

-- ---------------------------------------------------------------------------
-- Backlog ageing, carried across runs.
--
-- first_missing_at is when this collector first saw the patch missing — not when
-- the vendor released it. The API gives no date on a "Not Installed" entry, so
-- ages start accruing from the first run and are floors, not exact ages.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patch_backlog (
    device_id           BIGINT    NOT NULL,
    title               TEXT      NOT NULL,
    device_name         TEXT,
    customer_name       TEXT,
    category            TEXT,
    is_security         BOOLEAN   NOT NULL DEFAULT FALSE,
    is_third_party      BOOLEAN   NOT NULL DEFAULT FALSE,
    kb                  TEXT,
    first_missing_at    TIMESTAMP NOT NULL,
    last_missing_at     TIMESTAMP NOT NULL,
    times_seen_missing  INTEGER   NOT NULL DEFAULT 1,
    resolved_at         TIMESTAMP,          -- set when it stops being missing

    PRIMARY KEY (device_id, title)
);

CREATE INDEX IF NOT EXISTS patch_backlog_open_idx ON patch_backlog (device_id)
    WHERE resolved_at IS NULL;

-- BEGIN imported_at (DATA-01, approved 12 Nov 2026): generated by docs/tools/generate_imported_at.py, do not edit by hand.
-- imported_at is when a row was written to this database. captured_at, where a table has one, is when the source data was captured.
-- Rows stored before this existed are left empty (NULL) on purpose: nobody knows when they were imported. Every row stored from now on
-- gets the time it was inserted, with no change to the loaders (they name their columns). Safe to run again; no existing row is touched.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE customers ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE sites ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE sites ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE device ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE device ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE poll_run ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE poll_run ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE device_poll ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE device_poll ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE perf_check ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE perf_check ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE perf_event ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE perf_event ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE metric_run ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE metric_run ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE perf_sample ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE perf_sample ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE patch_run ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE patch_run ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE device_patch ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE device_patch ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE patch_entry ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE patch_entry ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE patch_backlog ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE patch_backlog ALTER COLUMN imported_at SET DEFAULT now();
-- END imported_at


-- ===========================================================================
-- VIEWS — performance
-- ===========================================================================

CREATE OR REPLACE VIEW v_measured_until AS
SELECT coalesce(max(started_at), localtimestamp) AS measured_until
FROM poll_run
WHERE ok;

-- Current state of every performance monitor.
CREATE OR REPLACE VIEW v_perf_current AS
SELECT DISTINCT ON (e.device_id, e.task_id)
       e.device_id,
       d.device_name,
       d.customer_name,
       c.module,
       coalesce(c.task_ident, '-')      AS instance,
       e.state,
       e.state_class,
       e.in_active_issue,
       e.transition_time,
       round(EXTRACT(EPOCH FROM (m.measured_until - e.transition_time)) / 86400.0, 2)
           AS days_in_state
FROM perf_event e
JOIN perf_check c USING (device_id, task_id)
JOIN device d USING (device_id)
CROSS JOIN v_measured_until m
ORDER BY e.device_id, e.task_id, e.transition_time DESC;

-- Right now: is it reporting, and is anything breached.
CREATE OR REPLACE VIEW v_device_current AS
SELECT DISTINCT ON (dp.device_id)
       dp.device_id,
       d.device_name,
       d.device_class,
       d.customer_name,
       d.site_name,
       d.device_uri,
       dp.polled_at,
       dp.reporting,
       CASE
           WHEN dp.reporting            THEN 'UP'
           WHEN dp.reporting IS FALSE    THEN 'NOT REPORTING'
           WHEN dp.monitor_count = 0     THEN 'not monitored'
           ELSE 'unknown'
       END                                                       AS status,
       dp.scan_age_mins,
       dp.monitor_count,
       dp.breach_count,
       dp.error
FROM device_poll dp
JOIN device d USING (device_id)
ORDER BY dp.device_id, dp.polled_at DESC;

-- Uptime from the poll samples. Resolution is the poll interval — hourly polling
-- means uptime accurate to the hour, which is what a "was it up" report needs.
CREATE OR REPLACE VIEW v_device_uptime_30d AS
SELECT dp.device_id,
       d.device_name,
       d.customer_name,
       count(*)                                                  AS polls,
       count(*) FILTER (WHERE dp.reporting)                       AS polls_up,
       count(*) FILTER (WHERE dp.reporting IS FALSE)              AS polls_down,
       count(*) FILTER (WHERE dp.reporting IS NULL)               AS polls_unknown,
       round(100.0 * count(*) FILTER (WHERE dp.reporting)
             / nullif(count(*) FILTER (WHERE dp.reporting IS NOT NULL), 0), 3)
                                                                 AS uptime_pct,
       min(dp.polled_at)                                          AS first_poll,
       max(dp.polled_at)                                          AS last_poll
FROM device_poll dp
JOIN device d USING (device_id)
CROSS JOIN v_measured_until m
WHERE dp.polled_at > m.measured_until - INTERVAL '30 days'
GROUP BY dp.device_id, d.device_name, d.customer_name;

-- Each performance event expanded into the interval it covers.
CREATE OR REPLACE VIEW v_perf_interval AS
SELECT e.device_id,
       e.task_id,
       c.module,
       c.task_ident,
       d.device_name,
       d.customer_name,
       e.state,
       e.state_class,
       e.flap_suspected,
       e.transition_time AS from_time,
       least(
           coalesce(
               lead(e.transition_time) OVER (PARTITION BY e.device_id, e.task_id
                                             ORDER BY e.transition_time),
               m.measured_until),
           m.measured_until
       ) AS to_time
FROM perf_event e
JOIN perf_check c USING (device_id, task_id)
JOIN device d USING (device_id)
CROSS JOIN v_measured_until m;

-- Time in breach per monitor over the last 30 days. The performance report.
CREATE OR REPLACE VIEW v_perf_30d AS
WITH bounds AS (
    SELECT measured_until AS period_end,
           measured_until - INTERVAL '30 days' AS period_start
    FROM v_measured_until
),
clipped AS (
    SELECT i.*,
           EXTRACT(EPOCH FROM (least(i.to_time, b.period_end)
                               - greatest(i.from_time, b.period_start))) AS secs
    FROM v_perf_interval i
    CROSS JOIN bounds b
    WHERE i.to_time > b.period_start AND i.from_time < b.period_end
)
SELECT device_id,
       device_name,
       customer_name,
       module,
       coalesce(task_ident, '-')                                    AS instance,
       round(sum(secs) / 3600.0, 1)                                  AS measured_hours,
       round(sum(secs) FILTER (WHERE state_class = 'degraded') / 3600.0, 1) AS warning_hours,
       round(sum(secs) FILTER (WHERE state_class = 'breached') / 3600.0, 1) AS breach_hours,
       round(sum(secs) FILTER (WHERE state_class = 'unknown')  / 3600.0, 1) AS unmeasured_hours,
       count(*) FILTER (WHERE state_class IN ('degraded', 'breached')) AS breach_events,
       count(*) FILTER (WHERE flap_suspected)                         AS suspected_flaps,
       round(max(secs) FILTER (WHERE state_class IN ('degraded', 'breached')) / 3600.0, 1)
                                                                     AS longest_breach_hours,
       -- Percentage of *measured* time in trouble: unknown time is excluded from
       -- both sides rather than counted as healthy.
       round(100.0 * sum(secs) FILTER (WHERE state_class IN ('degraded', 'breached'))
             / nullif(sum(secs) FILTER (WHERE state_class <> 'unknown'), 0), 2)
                                                                     AS breach_pct
FROM clipped
GROUP BY device_id, device_name, customer_name, module, task_ident;

-- Per-device rollup: uptime beside the worst resource.
CREATE OR REPLACE VIEW v_device_perf_30d AS
SELECT u.device_id,
       u.device_name,
       u.customer_name,
       u.uptime_pct,
       u.polls,
       u.polls_down,
       coalesce(sum(p.warning_hours + p.breach_hours), 0) AS trouble_hours,
       max(p.breach_pct)                                  AS worst_breach_pct,
       sum(p.breach_events)                               AS breach_events
FROM v_device_uptime_30d u
LEFT JOIN v_perf_30d p USING (device_id)
GROUP BY u.device_id, u.device_name, u.customer_name, u.uptime_pct, u.polls, u.polls_down;

-- Per-site rollup — the same 30-day picture aggregated to the site a device sits
-- in, for "which branch is having a bad month".
--
-- Keyed on device.site_id, not site_name: two customers can both have a site
-- called "Head Office", and grouping by name would silently merge them into one
-- row. Sites are named from the `sites` table when it has been synced
-- (ncentral_sites.py sync) and fall back to the name the device carried, so this
-- view still works standalone.
--
-- uptime_pct is device-weighted, not a mean of means: it re-derives from the
-- poll counts, so a site with one flapping device among thirty is not dragged to
-- 50% the way avg(uptime_pct) would.
--
-- Expect one row with a NULL site_id, and read it as "attached to the customer,
-- not to a site" rather than as unclassified: the API reports siteId null for
-- those devices. On this estate that row covers 9 of 22 devices, so it is a
-- material part of the picture and is not filtered out.
CREATE OR REPLACE VIEW v_site_perf_30d AS
SELECT d.site_id,
       coalesce(s.site_name, max(d.site_name))            AS site_name,
       coalesce(s.customer_name, max(d.customer_name))    AS customer_name,
       count(*)                                           AS devices,
       count(*) FILTER (WHERE u.polls_down > 0)           AS devices_with_downtime,
       round(100.0 * sum(u.polls_up) / nullif(sum(u.polls_up + u.polls_down), 0), 3)
                                                          AS uptime_pct,
       sum(u.polls_down)                                  AS polls_down,
       round(coalesce(sum(r.trouble_hours), 0), 1)        AS trouble_hours,
       max(r.worst_breach_pct)                            AS worst_breach_pct,
       coalesce(sum(r.breach_events), 0)                  AS breach_events
FROM device d
JOIN v_device_uptime_30d u USING (device_id)
LEFT JOIN v_device_perf_30d r USING (device_id)
LEFT JOIN sites s ON s.site_id = d.site_id
GROUP BY d.site_id, s.site_name, s.customer_name;

-- ===========================================================================
-- VIEWS — metrics. Actual values, unlike everything above.
-- ===========================================================================

-- Latest reading per device, with its age. Age is the point: a custom property
-- keeps its last value indefinitely, so `sample_age_mins` is the only thing
-- separating a live reading from one a dead AMP left behind months ago. Anything
-- consuming this view should filter on it rather than trust the number.
CREATE OR REPLACE VIEW v_perf_sample_current AS
SELECT DISTINCT ON (device_id)
       device_id,
       device_name,
       customer_name,
       site_name,
       sampled_at,
       round(EXTRACT(EPOCH FROM (localtimestamp - sampled_at)) / 60.0, 1) AS sample_age_mins,
       cpu_pct,
       memory_pct,
       memory_total_mb,
       memory_used_mb,
       disks
FROM perf_sample
ORDER BY device_id, sampled_at DESC;

-- Per device per day: the shape you actually want for "was it busy on Tuesday".
-- avg and max are both kept because they answer different questions — a box that
-- averages 40% but peaks at 100% every afternoon is not a quiet box.
CREATE OR REPLACE VIEW v_perf_sample_daily AS
SELECT device_id,
       max(device_name)                      AS device_name,
       max(customer_name)                    AS customer_name,
       max(site_name)                        AS site_name,
       sampled_at::date                      AS day,
       count(*)                              AS samples,
       round(avg(cpu_pct), 1)                AS cpu_avg,
       max(cpu_pct)                          AS cpu_max,
       round(avg(memory_pct), 1)             AS memory_avg,
       max(memory_pct)                       AS memory_max,
       count(*) FILTER (WHERE cpu_pct    >= 90) AS samples_cpu_over_90,
       count(*) FILTER (WHERE memory_pct >= 90) AS samples_memory_over_90
FROM perf_sample
GROUP BY device_id, sampled_at::date;

-- Pressure over the last 7 days, worst first. The capacity conversation.
--
-- pct_time_* is a share of SAMPLES, not of wall-clock: with hourly AMP runs a
-- device sampled 100 times in 7 days has missed most of the week, so read it
-- beside `samples` rather than as "% of the week".
CREATE OR REPLACE VIEW v_perf_pressure_7d AS
SELECT device_id,
       max(device_name)                                          AS device_name,
       max(customer_name)                                        AS customer_name,
       max(site_name)                                            AS site_name,
       count(*)                                                  AS samples,
       min(sampled_at)                                           AS first_sample,
       max(sampled_at)                                           AS last_sample,
       round(avg(cpu_pct), 1)                                    AS cpu_avg,
       max(cpu_pct)                                              AS cpu_max,
       round(100.0 * count(*) FILTER (WHERE cpu_pct >= 90)
             / nullif(count(*) FILTER (WHERE cpu_pct IS NOT NULL), 0), 1)
                                                                 AS pct_time_cpu_over_90,
       round(avg(memory_pct), 1)                                 AS memory_avg,
       max(memory_pct)                                           AS memory_max,
       round(100.0 * count(*) FILTER (WHERE memory_pct >= 90)
             / nullif(count(*) FILTER (WHERE memory_pct IS NOT NULL), 0), 1)
                                                                 AS pct_time_memory_over_90
FROM perf_sample
WHERE sampled_at > localtimestamp - INTERVAL '7 days'
GROUP BY device_id
ORDER BY pct_time_cpu_over_90 DESC NULLS LAST, cpu_avg DESC NULLS LAST;

-- Free space per volume, latest reading, emptiest first. The `disks` JSONB is
-- one object per device ({"C:": 8.4, "D:": 61.0}); this expands it to one row
-- per volume so it can be filtered and sorted like any other column.
--
-- free_pct, not used_pct: it is what the AMP measures and what a low-disk alert
-- is phrased in. The numeric guard skips any value that is not a plain number,
-- so a malformed reading drops that volume instead of erroring the whole view.
CREATE OR REPLACE VIEW v_disk_free_current AS
SELECT c.device_id,
       c.device_name,
       c.customer_name,
       c.site_name,
       c.sampled_at,
       c.sample_age_mins,
       v.key                        AS volume,
       round(v.value::numeric, 1)   AS free_pct
FROM v_perf_sample_current c
CROSS JOIN LATERAL jsonb_each_text(coalesce(c.disks, '{}'::jsonb)) AS v(key, value)
WHERE v.value ~ '^[0-9]+(\.[0-9]+)?$'
ORDER BY free_pct;

-- Lowest free space each device has hit per day. Disk fills slowly and then all
-- at once, so the daily minimum is the series worth trending — an average would
-- hide the afternoon a backup filled the volume.
CREATE OR REPLACE VIEW v_disk_free_daily AS
SELECT s.device_id,
       max(s.device_name)          AS device_name,
       max(s.customer_name)        AS customer_name,
       s.sampled_at::date          AS day,
       v.key                       AS volume,
       count(*)                    AS samples,
       min(round(v.value::numeric, 1)) AS free_pct_min,
       round(avg(v.value::numeric), 1) AS free_pct_avg
FROM perf_sample s
CROSS JOIN LATERAL jsonb_each_text(coalesce(s.disks, '{}'::jsonb)) AS v(key, value)
WHERE v.value ~ '^[0-9]+(\.[0-9]+)?$'
GROUP BY s.device_id, s.sampled_at::date, v.key;

-- Is the AMP actually running everywhere? Devices that stop reporting readings
-- are the failure mode this whole route has, so it gets its own view.
CREATE OR REPLACE VIEW v_metric_collector_health AS
SELECT (SELECT count(*)                       FROM metric_run)                AS runs,
       (SELECT count(*) FILTER (WHERE NOT ok) FROM metric_run)                AS failed_runs,
       (SELECT max(started_at)                FROM metric_run)                AS last_run,
       (SELECT round(EXTRACT(EPOCH FROM (localtimestamp - max(started_at))) / 3600.0, 1)
        FROM metric_run)                                                      AS hours_since_last_run,
       (SELECT count(DISTINCT device_id)      FROM perf_sample)               AS devices_ever_sampled,
       (SELECT count(*) FROM v_perf_sample_current WHERE sample_age_mins <= 120)
                                                                              AS devices_fresh_2h,
       (SELECT count(*) FROM v_perf_sample_current WHERE sample_age_mins  > 120)
                                                                              AS devices_stale_2h;

-- ===========================================================================
-- VIEWS — patching
-- ===========================================================================

-- The most recent run per device.
CREATE OR REPLACE VIEW v_device_patch_current AS
SELECT DISTINCT ON (device_id) *
FROM device_patch
ORDER BY device_id, captured_at DESC;

-- Estate compliance, latest data. Devices without an inventory are counted
-- separately rather than folded in as compliant.
CREATE OR REPLACE VIEW v_patch_summary AS
SELECT customer_name,
       count(*)                                              AS devices,
       count(*) FILTER (WHERE coverage = 'windows')           AS devices_with_inventory,
       count(*) FILTER (WHERE coverage = 'no-inventory')      AS devices_no_inventory,
       count(*) FILTER (WHERE coverage = 'unavailable')       AS devices_unavailable,
       count(*) FILTER (WHERE coverage = 'windows'
                          AND missing_security_count = 0)     AS devices_security_clean,
       count(*) FILTER (WHERE missing_security_count > 0)      AS devices_security_behind,
       sum(missing_count)                                     AS missing_total,
       sum(missing_security_count)                            AS missing_security,
       sum(missing_windows_count)                             AS missing_windows,
       sum(missing_thirdparty_count)                          AS missing_thirdparty,
       sum(missing_definition_count)                          AS missing_definitions,
       round(100.0 * count(*) FILTER (WHERE coverage = 'windows'
                                        AND missing_security_count = 0)
             / nullif(count(*) FILTER (WHERE coverage = 'windows'), 0), 1)
                                                              AS security_compliance_pct,
       max(days_since_last_install)                           AS worst_days_since_install
FROM v_device_patch_current
GROUP BY customer_name;

-- What is outstanding right now, oldest first. The remediation list.
CREATE OR REPLACE VIEW v_patch_backlog_open AS
SELECT device_name,
       customer_name,
       category,
       is_security,
       is_third_party,
       coalesce(kb, '-')  AS kb,
       title,
       first_missing_at,
       last_missing_at,
       times_seen_missing,
       round(EXTRACT(EPOCH FROM (last_missing_at - first_missing_at)) / 86400.0, 1)
           AS days_outstanding
FROM patch_backlog
WHERE resolved_at IS NULL
ORDER BY is_security DESC, first_missing_at;

-- Windows vs third-party, side by side per device.
CREATE OR REPLACE VIEW v_patch_by_type AS
SELECT device_name,
       customer_name,
       coverage,
       entry_count,
       installed_count,
       missing_windows_count    AS windows_missing,
       missing_security_count   AS security_missing,
       missing_definition_count AS definition_missing,
       missing_thirdparty_count AS thirdparty_missing,
       missing_count            AS total_missing,
       reboot_monitor_state,
       days_since_last_install
FROM v_device_patch_current
ORDER BY security_missing DESC, windows_missing DESC;

-- How the backlog moves over time.
CREATE OR REPLACE VIEW v_patch_trend AS
SELECT date_trunc('day', captured_at)::date AS day,
       customer_name,
       count(DISTINCT device_id)            AS devices,
       sum(missing_count)                   AS missing_total,
       sum(missing_security_count)          AS missing_security,
       sum(missing_thirdparty_count)        AS missing_thirdparty
FROM device_patch
GROUP BY day, customer_name
ORDER BY day DESC, customer_name;

-- ===========================================================================
-- VIEWS — collector health. Gaps here mean the numbers above are thinner than
-- they look.
-- ===========================================================================

CREATE OR REPLACE VIEW v_collector_health AS
SELECT count(*)                                       AS polls,
       count(*) FILTER (WHERE NOT ok)                 AS failed_polls,
       min(started_at)                                AS first_poll,
       max(started_at)                                AS last_poll,
       round(EXTRACT(EPOCH FROM (localtimestamp - max(started_at))) / 3600.0, 1)
                                                      AS hours_since_last_poll,
       sum(event_count)                               AS state_changes_recorded
FROM poll_run;

CREATE OR REPLACE VIEW v_patch_collector_health AS
SELECT count(*)                        AS runs,
       count(*) FILTER (WHERE NOT ok)  AS failed_runs,
       min(started_at)                 AS first_run,
       max(started_at)                 AS last_run,
       round(EXTRACT(EPOCH FROM (localtimestamp - max(started_at))) / 3600.0, 1)
                                       AS hours_since_last_run
FROM patch_run;
