-- ===========================================================================
-- SentinelOne EDR reporting schema
--
-- Design: every load is a *snapshot* of one report month. A run may cover the
-- whole estate or a single customer; either way nothing is updated in place, so
-- re-running a month appends a new snapshot and the old one stays queryable.
-- That is what makes "was this customer worse last month?" answerable.
--
-- Grouping key is customer_name (the SentinelOne siteName), matching how
-- generate_monthly_reports.py buckets its reports. site_id travels alongside as
-- an attribute — it is the stable id used to filter the API, but siteName is
-- always present whereas siteId occasionally is not.
--
-- Apply with:  psql "$SENTINELONE_PG_DSN" -f sql/schema.sql
-- Idempotent — safe to re-run.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS sentinelone;
SET search_path TO sentinelone, public;

-- ---------------------------------------------------------------------------
-- Customer roster — the table that drives per-customer runs
--
-- Unlike cove.customers this one IS created here, and is kept in step with the
-- console by `python s1_db.py sync-customers` (reads /web/api/v2.1/sites).
-- Flip is_active to false to drop a site out of the automated run without
-- deleting its history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id              BIGSERIAL   PRIMARY KEY,
    site_id         TEXT        NOT NULL UNIQUE,
    customer_name   TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN     NOT NULL DEFAULT true,

    -- mirrored from the console on sync, for context only
    s1_state        TEXT,                   -- active / expired / deleted
    site_type       TEXT,
    total_licenses  INTEGER,
    active_licenses INTEGER,
    expiration      TIMESTAMPTZ,
    last_synced_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS customers_name_idx   ON customers (customer_name);
CREATE INDEX IF NOT EXISTS customers_active_idx ON customers (is_active) WHERE is_active;

-- ---------------------------------------------------------------------------
-- One row per load run
-- ---------------------------------------------------------------------------
-- The period columns record exactly what was queried. A run on the 27th of a
-- month covers the 1st through the end of that day, so month_end is the real
-- end of coverage — not the end of the calendar month — and is_partial_month
-- says so outright rather than leaving a reader to compare dates.
CREATE TABLE IF NOT EXISTS snapshot (
    snapshot_id         BIGSERIAL   PRIMARY KEY,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    source              TEXT        NOT NULL DEFAULT 'sentinelone_api',

    report_month        TEXT        NOT NULL,      -- 'YYYY-MM'
    period_label        TEXT,                      -- 'July 2026 (1-27, month to date)'
    month_start         TIMESTAMPTZ NOT NULL,      -- first second covered
    month_end           TIMESTAMPTZ NOT NULL,      -- last second covered
    is_partial_month    BOOLEAN     NOT NULL DEFAULT false,
    days_covered        INTEGER,

    customer_filter     TEXT,                      -- NULL = whole estate
    site_id_filter      TEXT,                      -- NULL = whole estate

    customer_count      INTEGER     NOT NULL DEFAULT 0,
    threat_count        INTEGER     NOT NULL DEFAULT 0,
    agent_count         INTEGER     NOT NULL DEFAULT 0,
    latest_agent_version TEXT,
    notes               TEXT
);

-- Upgrade path for a schema applied before the period columns existed.
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS period_label     TEXT;
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS is_partial_month BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS days_covered     INTEGER;

CREATE INDEX IF NOT EXISTS snapshot_captured_at_idx ON snapshot (captured_at DESC);
CREATE INDEX IF NOT EXISTS snapshot_month_idx       ON snapshot (report_month);
CREATE INDEX IF NOT EXISTS snapshot_complete_idx    ON snapshot (report_month)
    WHERE NOT is_partial_month;

-- ---------------------------------------------------------------------------
-- Per-customer monthly scorecard — the master-summary row, in table form
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer_month (
    snapshot_id         BIGINT NOT NULL REFERENCES snapshot (snapshot_id) ON DELETE CASCADE,
    customer_name       TEXT   NOT NULL,
    site_id             TEXT,
    report_month        TEXT   NOT NULL,           -- denormalised for direct slicing

    -- threat volume
    threats             INTEGER NOT NULL DEFAULT 0,
    mitigated           INTEGER NOT NULL DEFAULT 0,
    not_mitigated       INTEGER NOT NULL DEFAULT 0,
    marked_benign       INTEGER NOT NULL DEFAULT 0,
    malicious           INTEGER NOT NULL DEFAULT 0,
    suspicious          INTEGER NOT NULL DEFAULT 0,
    mitigation_rate     NUMERIC(5,1),

    -- classification of concern
    ransomware          INTEGER NOT NULL DEFAULT 0,
    infostealer         INTEGER NOT NULL DEFAULT 0,
    backdoor            INTEGER NOT NULL DEFAULT 0,
    pua                 INTEGER NOT NULL DEFAULT 0,
    lateral_movement    INTEGER NOT NULL DEFAULT 0,
    lolbin_detections   INTEGER NOT NULL DEFAULT 0,

    -- detection engines
    behavioral_ai       INTEGER NOT NULL DEFAULT 0,
    static_ai           INTEGER NOT NULL DEFAULT 0,

    -- response
    avg_mttr_minutes    NUMERIC(10,1),             -- NULL when nothing was mitigated

    -- endpoint estate
    devices             INTEGER NOT NULL DEFAULT 0,
    infected            INTEGER NOT NULL DEFAULT 0,
    outdated_agents     INTEGER NOT NULL DEFAULT 0,
    stale_agents        INTEGER NOT NULL DEFAULT 0,  -- not seen in 7 days
    pending_uninstall   INTEGER NOT NULL DEFAULT 0,
    detect_only         INTEGER NOT NULL DEFAULT 0,  -- NOT auto-blocking
    protect_mode        INTEGER NOT NULL DEFAULT 0,
    health_issues       INTEGER NOT NULL DEFAULT 0,
    scan_aborted        INTEGER NOT NULL DEFAULT 0,

    -- rollup
    risk_score          INTEGER NOT NULL DEFAULT 0,
    risk_level          TEXT    NOT NULL,            -- CRITICAL / HIGH / MEDIUM / LOW

    report_path         TEXT,                        -- .txt written for this customer

    PRIMARY KEY (snapshot_id, customer_name)
);

CREATE INDEX IF NOT EXISTS cm_customer_idx   ON customer_month (customer_name);
CREATE INDEX IF NOT EXISTS cm_month_idx      ON customer_month (report_month);
CREATE INDEX IF NOT EXISTS cm_site_idx       ON customer_month (site_id);
CREATE INDEX IF NOT EXISTS cm_risk_idx       ON customer_month (risk_level) WHERE risk_level <> 'LOW';

-- ---------------------------------------------------------------------------
-- Threat-level facts (one row per threat per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS threat (
    snapshot_id         BIGINT NOT NULL,
    threat_id           TEXT   NOT NULL,
    customer_name       TEXT   NOT NULL,
    site_id             TEXT,

    threat_name         TEXT,
    classification      TEXT,
    confidence_level    TEXT,                       -- malicious / suspicious
    mitigation_status   TEXT,                       -- mitigated / not_mitigated / marked_as_benign
    detection_type      TEXT,                       -- dynamic (Behavioral AI) / static
    analyst_verdict     TEXT,
    incident_status     TEXT,

    created_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ,
    mttr_minutes        NUMERIC(10,1),              -- only set for mitigated threats
    detected_on         DATE,                       -- local calendar day, matches the .txt trend

    process_user        TEXT,
    file_path           TEXT,
    sha1                TEXT,
    storyline           TEXT,

    device_name         TEXT,
    agent_id            TEXT,
    agent_os            TEXT,

    is_lolbin           BOOLEAN NOT NULL DEFAULT false,
    is_lateral_movement BOOLEAN NOT NULL DEFAULT false,
    mitre_tactics       TEXT,                       -- comma-separated, for eyeballing

    PRIMARY KEY (snapshot_id, threat_id),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS threat_customer_idx   ON threat (customer_name);
CREATE INDEX IF NOT EXISTS threat_created_idx    ON threat (created_at DESC);
CREATE INDEX IF NOT EXISTS threat_class_idx      ON threat (classification);
CREATE INDEX IF NOT EXISTS threat_unresolved_idx ON threat (mitigation_status)
    WHERE mitigation_status = 'not_mitigated';
CREATE INDEX IF NOT EXISTS threat_device_idx     ON threat (device_name);

-- ---------------------------------------------------------------------------
-- Endpoint facts (one row per agent per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent (
    snapshot_id         BIGINT NOT NULL,
    agent_id            TEXT   NOT NULL,
    customer_name       TEXT   NOT NULL,
    site_id             TEXT,

    computer_name       TEXT,
    domain              TEXT,
    machine_type        TEXT,
    group_name          TEXT,

    os_name             TEXT,
    os_type             TEXT,
    agent_version       TEXT,
    is_up_to_date       BOOLEAN,

    mitigation_mode     TEXT,                       -- protect / detect
    infected            BOOLEAN,
    network_status      TEXT,
    scan_status         TEXT,
    is_pending_uninstall BOOLEAN,
    is_active           BOOLEAN,
    is_decommissioned   BOOLEAN,

    last_active         TIMESTAMPTZ,
    days_since_active   INTEGER,
    is_stale            BOOLEAN NOT NULL DEFAULT false,   -- > 7 days quiet

    user_actions_needed TEXT,                       -- comma-separated health codes
    health_issue_count  INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (snapshot_id, agent_id),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS agent_customer_idx ON agent (customer_name);
CREATE INDEX IF NOT EXISTS agent_detect_idx   ON agent (mitigation_mode) WHERE mitigation_mode = 'detect';
CREATE INDEX IF NOT EXISTS agent_unhealthy_idx ON agent (health_issue_count) WHERE health_issue_count > 0;
CREATE INDEX IF NOT EXISTS agent_computer_idx ON agent (computer_name);

-- ---------------------------------------------------------------------------
-- Breakdown tables — the ranked lists that appear in the .txt sections
-- ---------------------------------------------------------------------------

-- Section 2: classification breakdown
CREATE TABLE IF NOT EXISTS threat_class (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    classification  TEXT    NOT NULL,
    threat_count    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, classification),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Section 3: MITRE ATT&CK tactics observed
CREATE TABLE IF NOT EXISTS mitre_tactic (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    tactic          TEXT    NOT NULL,
    threat_count    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, tactic),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Top threat names
CREATE TABLE IF NOT EXISTS top_threat (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    threat_name     TEXT    NOT NULL,
    detections      INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, threat_name),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Most-affected devices
CREATE TABLE IF NOT EXISTS top_device (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    device_name     TEXT    NOT NULL,
    detections      INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, device_name),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Users whose sessions triggered detections (NT AUTHORITY\* excluded upstream)
CREATE TABLE IF NOT EXISTS top_user (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    process_user    TEXT    NOT NULL,
    detections      INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, process_user),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Living-off-the-land binaries seen
CREATE TABLE IF NOT EXISTS lolbin (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    threat_name     TEXT    NOT NULL,
    detections      INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, threat_name),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Daily detection trend
CREATE TABLE IF NOT EXISTS daily_trend (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    trend_date      DATE    NOT NULL,
    threat_count    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, trend_date),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- OS spread across the estate
CREATE TABLE IF NOT EXISTS os_spread (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    os_name         TEXT    NOT NULL,
    device_count    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, os_name),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- Agent version spread
CREATE TABLE IF NOT EXISTS agent_version (
    snapshot_id     BIGINT  NOT NULL,
    customer_name   TEXT    NOT NULL,
    agent_version   TEXT    NOT NULL,
    device_count    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, customer_name, agent_version),
    FOREIGN KEY (snapshot_id, customer_name)
        REFERENCES customer_month (snapshot_id, customer_name) ON DELETE CASCADE
);

-- BEGIN imported_at (DATA-01, approved 12 Nov 2026): generated by docs/tools/generate_imported_at.py, do not edit by hand.
-- imported_at is when a row was written to this database. captured_at, where a table has one, is when the source data was captured.
-- Rows stored before this existed are left empty (NULL) on purpose: nobody knows when they were imported. Every row stored from now on
-- gets the time it was inserted, with no change to the loaders (they name their columns). Safe to run again; no existing row is touched.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE customers ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE snapshot ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE customer_month ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE customer_month ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE threat ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE threat ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE agent ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE agent ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE threat_class ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE threat_class ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE mitre_tactic ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE mitre_tactic ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE top_threat ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE top_threat ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE top_device ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE top_device ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE top_user ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE top_user ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE lolbin ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE lolbin ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE daily_trend ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE daily_trend ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE os_spread ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE os_spread ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE agent_version ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE agent_version ALTER COLUMN imported_at SET DEFAULT now();
-- END imported_at


-- ===========================================================================
-- Views
--
-- Per-customer runs mean "the latest snapshot" is not one row for the whole
-- estate — customer A may have been loaded after customer B. The _latest views
-- therefore resolve the newest snapshot *per customer per month*, which is what
-- dashboards actually want.
-- ===========================================================================

-- Dropped and recreated rather than CREATE OR REPLACE'd. Replacing a view can
-- only *append* columns, so adding a column mid-list — as the period columns did
-- — fails on an existing install with "cannot change name of view column".
-- Dropping first makes this file re-runnable as the schema evolves.
--
-- CASCADE is safe for these ten because they are all recreated immediately
-- below. If you have built your own views on top of them, they will be dropped
-- too and will need recreating.
DROP VIEW IF EXISTS v_red_flag_latest      CASCADE;
DROP VIEW IF EXISTS v_threat_latest        CASCADE;
DROP VIEW IF EXISTS v_agent_latest         CASCADE;
DROP VIEW IF EXISTS v_current_snapshot_ids CASCADE;
DROP VIEW IF EXISTS v_coverage_gaps        CASCADE;
DROP VIEW IF EXISTS v_customer_trend       CASCADE;
DROP VIEW IF EXISTS v_portfolio_month      CASCADE;
DROP VIEW IF EXISTS v_customer_current     CASCADE;
DROP VIEW IF EXISTS v_customer_month_latest CASCADE;
DROP VIEW IF EXISTS v_latest_snapshot      CASCADE;

CREATE OR REPLACE VIEW v_latest_snapshot AS
SELECT * FROM snapshot ORDER BY captured_at DESC LIMIT 1;

-- Newest scorecard row for every (customer, month) pair, whichever run produced it
-- The period columns ride along so no consumer has to join snapshot just to
-- find out whether a row covers a whole month or a month to date.
CREATE OR REPLACE VIEW v_customer_month_latest AS
SELECT DISTINCT ON (cm.customer_name, cm.report_month)
       cm.*,
       s.captured_at,
       s.period_label,
       s.month_start   AS period_start,
       s.month_end     AS period_end,
       s.is_partial_month,
       s.days_covered
FROM customer_month cm
JOIN snapshot s USING (snapshot_id)
ORDER BY cm.customer_name, cm.report_month, s.captured_at DESC;

-- The most recent month on record per customer — the "where do we stand" view
CREATE OR REPLACE VIEW v_customer_current AS
SELECT DISTINCT ON (customer_name) *
FROM v_customer_month_latest
ORDER BY customer_name, report_month DESC, captured_at DESC;

-- Winning snapshot_ids, so detail tables can be filtered to non-superseded rows
CREATE OR REPLACE VIEW v_current_snapshot_ids AS
SELECT DISTINCT snapshot_id, customer_name, report_month FROM v_customer_month_latest;

CREATE OR REPLACE VIEW v_threat_latest AS
SELECT t.*, c.report_month
FROM threat t
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name);

CREATE OR REPLACE VIEW v_agent_latest AS
SELECT a.*, c.report_month
FROM agent a
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name);

CREATE OR REPLACE VIEW v_red_flag_latest AS
SELECT customer_name, report_month, 'unresolved_threat' AS category,
       threat_name AS detail, device_name
FROM v_threat_latest
WHERE mitigation_status = 'not_mitigated'
UNION ALL
SELECT customer_name, report_month, 'detect_only_agent',
       computer_name, computer_name
FROM v_agent_latest
WHERE mitigation_mode = 'detect'
UNION ALL
SELECT customer_name, report_month, 'agent_health',
       user_actions_needed, computer_name
FROM v_agent_latest
WHERE health_issue_count > 0
UNION ALL
SELECT customer_name, report_month, 'stale_agent',
       computer_name, computer_name
FROM v_agent_latest
WHERE is_stale;

-- Portfolio roll-up per month — the master summary header line
CREATE OR REPLACE VIEW v_portfolio_month AS
SELECT
    report_month,
    max(period_label)                               AS period_label,
    bool_or(is_partial_month)                       AS is_partial_month,
    max(days_covered)                               AS days_covered,
    count(*)                                        AS customers,
    sum(devices)                                    AS devices,
    sum(threats)                                    AS threats,
    sum(not_mitigated)                              AS unresolved,
    sum(ransomware)                                 AS ransomware,
    sum(infostealer)                                AS infostealer,
    sum(lateral_movement)                           AS lateral_movement,
    sum(detect_only)                                AS detect_only,
    sum(health_issues)                              AS health_issues,
    sum(infected)                                   AS infected,
    count(*) FILTER (WHERE risk_level = 'CRITICAL') AS critical_customers,
    count(*) FILTER (WHERE risk_level = 'HIGH')     AS high_customers,
    round(avg(mitigation_rate), 1)                  AS avg_mitigation_rate,
    round(avg(avg_mttr_minutes), 1)                 AS avg_mttr_minutes
FROM v_customer_month_latest
GROUP BY report_month;

-- Month-over-month movement per customer.
--
-- is_partial_month matters here: a month to date will almost always look like an
-- improvement on a complete month. threats_per_day is the honest comparison
-- while a month is still running.
CREATE OR REPLACE VIEW v_customer_trend AS
SELECT
    customer_name,
    report_month,
    period_label,
    is_partial_month,
    days_covered,
    threats,
    round(threats::numeric / nullif(days_covered, 0), 2) AS threats_per_day,
    risk_score,
    risk_level,
    lag(threats)    OVER w AS prev_threats,
    threats - lag(threats) OVER w AS threat_delta,
    lag(risk_score) OVER w AS prev_risk_score,
    risk_score - lag(risk_score) OVER w AS risk_delta
FROM v_customer_month_latest
WINDOW w AS (PARTITION BY customer_name ORDER BY report_month);

-- Roster health: which active customers are missing from the latest month
CREATE OR REPLACE VIEW v_coverage_gaps AS
SELECT c.site_id, c.customer_name, max(cm.report_month) AS last_loaded_month
FROM customers c
LEFT JOIN v_customer_month_latest cm ON cm.customer_name = c.customer_name
WHERE c.is_active
GROUP BY c.site_id, c.customer_name;
