-- ===========================================================================
-- Cove Data Protection reporting schema
--
-- Design: every load is a *snapshot*. The Cove API only exposes current state,
-- so history is built by appending snapshots. Nothing is ever updated in place,
-- which makes trend queries ("was this device failing last week too?") possible.
--
-- Apply with:  psql "$COVE_PG_DSN" -f sql/schema.sql
-- Idempotent — safe to re-run.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS cove;
SET search_path TO cove, public;

-- ---------------------------------------------------------------------------
-- Customers to load. cove_db.py `load-active` reads the active rows; the loader never writes here.
-- Add one row per customer, matching the customer name Cove reports (partial, case-insensitive match).
-- ---------------------------------------------------------------------------
-- Mirrors the table as it exists in the live database (created there by hand). Note it has NO unique constraint on
-- customer_name, so a name entered twice would be loaded twice. Once you have checked there are no duplicates you can add
-- one:  CREATE UNIQUE INDEX customers_name_uniq ON customers (lower(customer_name));
CREATE TABLE IF NOT EXISTS customers (
    id              SERIAL      PRIMARY KEY,
    customer_name   TEXT        NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    is_active       BOOLEAN     DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- One row per load run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT        NOT NULL DEFAULT 'cove_api',
    customer_filter TEXT,                       -- NULL = whole partner estate
    device_count    INTEGER     NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS snapshot_captured_at_idx ON snapshot (captured_at DESC);

-- ---------------------------------------------------------------------------
-- Device-level facts (one row per device per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device (
    snapshot_id         BIGINT      NOT NULL REFERENCES snapshot (snapshot_id) ON DELETE CASCADE,
    device_id           BIGINT      NOT NULL,

    -- identity
    device_name         TEXT,
    device_alias        TEXT,
    customer            TEXT,
    contact_email       TEXT,
    hostname            TEXT,

    -- machine
    os                  TEXT,
    os_type             TEXT,           -- Workstation / Server / Unknown
    account_type        TEXT,           -- Endpoint/Server / Microsoft 365
    client_version      TEXT,
    client_old          BOOLEAN,
    os_eol              TEXT,           -- EOL notice, NULL when supported
    is_server           BOOLEAN,
    is_m365             BOOLEAN,

    -- storage
    used_storage_bytes  BIGINT,
    dedup_efficiency    INTEGER,        -- percent
    storage_location    TEXT,
    strict_location_policy BOOLEAN,

    -- config & security posture
    reporting_enabled   BOOLEAN,
    reporting_frequency INTEGER,
    dr_testing          TEXT,
    immutable_enabled   BOOLEAN,
    fips_enabled        BOOLEAN,
    passphrase_set      BOOLEAN,
    lsv_enabled         BOOLEAN,
    lsv_status          TEXT,
    cloud_status        TEXT,
    retention_days      INTEGER,

    -- backup currency
    last_success        TIMESTAMPTZ,
    days_since_success  INTEGER,

    -- rollups
    health              TEXT NOT NULL,  -- OK / WARNING / CRITICAL
    red_flag_count      INTEGER NOT NULL DEFAULT 0,
    active_datasources  TEXT,           -- comma-separated friendly names

    PRIMARY KEY (snapshot_id, device_id)
);

CREATE INDEX IF NOT EXISTS device_customer_idx  ON device (customer);
CREATE INDEX IF NOT EXISTS device_health_idx    ON device (health) WHERE health <> 'OK';
CREATE INDEX IF NOT EXISTS device_device_id_idx ON device (device_id);

-- ---------------------------------------------------------------------------
-- Per-datasource backup session (Files & Folders, SQL, Hyper-V, M365 ...)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasource_session (
    snapshot_id         BIGINT NOT NULL,
    device_id           BIGINT NOT NULL,
    ds_code             TEXT   NOT NULL,        -- D01, D19, ...
    ds_name             TEXT,
    customer            TEXT,                   -- denormalised for direct slicing
    device_name         TEXT,

    status              TEXT,
    status_code         TEXT,
    error_count         INTEGER,
    duration_seconds    INTEGER,
    sent_bytes          BIGINT,
    protected_bytes     BIGINT,
    item_count          BIGINT,
    retention_days      INTEGER,
    last_success        TIMESTAMPTZ,
    last_session        TIMESTAMPTZ,

    -- 28-day colour bar analysis
    colorbar_raw        TEXT,
    ok_days             INTEGER,
    failed_days         INTEGER,
    warning_days        INTEGER,
    missed_days         INTEGER,
    max_fail_streak     INTEGER,
    recurring_failures  BOOLEAN,
    chronic_warnings    BOOLEAN,
    chronic_missed      BOOLEAN,

    -- immutable copy
    imm_active          BOOLEAN,
    imm_last_status     TEXT,
    imm_last_status_code TEXT,
    imm_last_at         TIMESTAMPTZ,

    PRIMARY KEY (snapshot_id, device_id, ds_code),
    FOREIGN KEY (snapshot_id, device_id) REFERENCES device (snapshot_id, device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ds_session_customer_idx ON datasource_session (customer);
CREATE INDEX IF NOT EXISTS ds_session_code_idx     ON datasource_session (ds_code);

-- ---------------------------------------------------------------------------
-- M365 source coverage matrix (Exchange / OneDrive / SharePoint / Teams)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS m365_source (
    snapshot_id     BIGINT NOT NULL,
    device_id       BIGINT NOT NULL,
    source_label    TEXT   NOT NULL,            -- Exchange / OneDrive / SharePoint / Teams
    ds_code         TEXT,
    customer        TEXT,
    device_name     TEXT,

    enabled         BOOLEAN,
    coverage        TEXT,                       -- OK / NOT ENABLED / ENABLED — <reason>
    status_code     TEXT,
    last_success    TIMESTAMPTZ,
    item_count      BIGINT,
    user_count      INTEGER,                    -- licensed seats (billable)

    ok_days         INTEGER,
    failed_days     INTEGER,
    missed_days     INTEGER,

    PRIMARY KEY (snapshot_id, device_id, source_label),
    FOREIGN KEY (snapshot_id, device_id) REFERENCES device (snapshot_id, device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS m365_source_customer_idx ON m365_source (customer);
CREATE INDEX IF NOT EXISTS m365_source_coverage_idx ON m365_source (coverage) WHERE coverage <> 'OK';

-- ---------------------------------------------------------------------------
-- Red flags (one row per flag per device per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS red_flag (
    red_flag_id     BIGSERIAL PRIMARY KEY,
    snapshot_id     BIGINT NOT NULL,
    device_id       BIGINT NOT NULL,
    customer        TEXT,
    device_name     TEXT,
    category        TEXT,                       -- bucketed by the loader
    flag            TEXT   NOT NULL,

    FOREIGN KEY (snapshot_id, device_id) REFERENCES device (snapshot_id, device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS red_flag_snapshot_idx ON red_flag (snapshot_id);
CREATE INDEX IF NOT EXISTS red_flag_customer_idx ON red_flag (customer);
CREATE INDEX IF NOT EXISTS red_flag_category_idx ON red_flag (category);

-- BEGIN imported_at (DATA-01, approved 12 Nov 2026): generated by docs/tools/generate_imported_at.py, do not edit by hand.
-- imported_at is when a row was written to this database. captured_at, where a table has one, is when the source data was captured.
-- Rows stored before this existed are left empty (NULL) on purpose: nobody knows when they were imported. Every row stored from now on
-- gets the time it was inserted, with no change to the loaders (they name their columns). Safe to run again; no existing row is touched.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE customers ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE snapshot ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE device ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE device ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE datasource_session ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE datasource_session ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE m365_source ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE m365_source ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE red_flag ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE red_flag ALTER COLUMN imported_at SET DEFAULT now();
-- END imported_at


-- ===========================================================================
-- Views over the most recent snapshot — what dashboards/reports should read
-- ===========================================================================

CREATE OR REPLACE VIEW v_latest_snapshot AS
SELECT * FROM snapshot ORDER BY captured_at DESC LIMIT 1;

CREATE OR REPLACE VIEW v_device_latest AS
SELECT d.*
FROM device d
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_datasource_latest AS
SELECT ds.*
FROM datasource_session ds
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_m365_latest AS
SELECT m.*
FROM m365_source m
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_red_flag_latest AS
SELECT f.*
FROM red_flag f
JOIN v_latest_snapshot s USING (snapshot_id);

-- Customer health scorecard, latest snapshot
CREATE OR REPLACE VIEW v_customer_summary AS
SELECT
    customer,
    count(*)                                             AS total_devices,
    count(*) FILTER (WHERE is_m365)                      AS m365_devices,
    count(*) FILTER (WHERE is_server)                    AS servers,
    count(*) FILTER (WHERE health = 'OK')                AS healthy,
    count(*) FILTER (WHERE health = 'WARNING')           AS warning,
    count(*) FILTER (WHERE health = 'CRITICAL')          AS critical,
    count(*) FILTER (WHERE NOT reporting_enabled)        AS no_reporting,
    count(*) FILTER (WHERE is_server AND dr_testing = 'Not Configured') AS servers_without_dr,
    sum(red_flag_count)                                  AS red_flags,
    sum(used_storage_bytes)                              AS used_storage_bytes,
    round(sum(used_storage_bytes) / 1024.0^3, 2)         AS used_storage_gb,
    max(days_since_success)                              AS worst_days_since_success
FROM v_device_latest
GROUP BY customer;
