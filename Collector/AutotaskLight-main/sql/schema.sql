-- ===========================================================================
-- Autotask reporting schema
--
-- Design: every load is a *snapshot* of one company's tickets over a lookback
-- window. Nothing is ever updated in place, which makes trend queries ("was
-- this customer's SLA better last month?") possible, and lets the same ticket
-- appear in several snapshots as its status and resolution change.
--
-- autotask.customers is the driver table: autotask_db.py loops over its active
-- rows and loads one snapshot per customer.
--
-- Apply with:  psql "$AUTOTASK_PG_DSN" -f sql/schema.sql
-- Idempotent — safe to re-run.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS autotask;
SET search_path TO autotask, public;

-- ---------------------------------------------------------------------------
-- Driver table — which customers to load. Maintained with
--   python autotask_db.py sync-customers "IP Capital"
-- or by hand. Deactivate rather than delete, so old snapshots keep their context.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id                  SERIAL      PRIMARY KEY,
    customer_name       TEXT        NOT NULL UNIQUE,   -- exact Autotask company name
    autotask_company_id BIGINT,                        -- filled in by sync-customers
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS customers_active_idx ON customers (customer_name) WHERE is_active;

-- Seed the IP Capital family. ON CONFLICT DO NOTHING keeps re-runs harmless;
-- run sync-customers afterwards to fill in autotask_company_id.
INSERT INTO customers (customer_name, autotask_company_id) VALUES
    ('IP Capital',        367),
    ('IP Capital: BITCO', 809),
    ('IP Capital: CCOD',  810)
ON CONFLICT (customer_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- One row per load run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT        NOT NULL DEFAULT 'autotask_api',
    company_filter  TEXT,                       -- the company this snapshot is for
    days_back       INTEGER,                    -- lookback window used
    ticket_count    INTEGER     NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS snapshot_captured_at_idx ON snapshot (captured_at DESC);
CREATE INDEX IF NOT EXISTS snapshot_company_idx     ON snapshot (company_filter);

-- ---------------------------------------------------------------------------
-- The company the snapshot is for (one row per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company (
    snapshot_id     BIGINT  NOT NULL REFERENCES snapshot (snapshot_id) ON DELETE CASCADE,
    company_id      BIGINT  NOT NULL,           -- Autotask Companies.id
    company_name    TEXT,
    is_active       BOOLEAN,
    ticket_count    INTEGER NOT NULL DEFAULT 0,
    details         JSONB,                      -- full Companies record as returned

    PRIMARY KEY (snapshot_id, company_id)
);

CREATE INDEX IF NOT EXISTS company_company_id_idx ON company (company_id);
CREATE INDEX IF NOT EXISTS company_name_idx       ON company (company_name);

-- ---------------------------------------------------------------------------
-- Tickets (one row per ticket per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket (
    snapshot_id           BIGINT NOT NULL,
    ticket_id             BIGINT NOT NULL,      -- Autotask Tickets.id
    ticket_number         TEXT,
    company_id            BIGINT NOT NULL,
    company_name          TEXT,                 -- denormalised for direct slicing

    -- content
    title                 TEXT,
    description           TEXT,
    resolution            TEXT,

    -- classification (picklist labels, already resolved by the client)
    ticket_type           TEXT,
    priority              TEXT,
    issue_type            TEXT,
    sub_issue_type        TEXT,
    status                TEXT,

    -- SLA outcome
    sla_met               BOOLEAN,
    first_response_met    BOOLEAN,

    -- elapsed business time
    business_hours        NUMERIC(10, 2),       -- create -> complete, business hours only
    business_hours_start  TEXT,                 -- window used, e.g. '08:00'
    business_hours_end    TEXT,
    holiday_set_id        BIGINT,
    holidays_excluded     INTEGER,

    -- timeline
    first_response_at     TIMESTAMPTZ,
    first_response_due_at TIMESTAMPTZ,
    created_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,

    PRIMARY KEY (snapshot_id, ticket_id),
    FOREIGN KEY (snapshot_id, company_id) REFERENCES company (snapshot_id, company_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ticket_company_idx    ON ticket (company_name);
CREATE INDEX IF NOT EXISTS ticket_ticket_id_idx  ON ticket (ticket_id);
CREATE INDEX IF NOT EXISTS ticket_created_at_idx ON ticket (created_at DESC);
CREATE INDEX IF NOT EXISTS ticket_status_idx     ON ticket (status);
CREATE INDEX IF NOT EXISTS ticket_sla_breach_idx ON ticket (company_name) WHERE sla_met IS FALSE;

-- ---------------------------------------------------------------------------
-- Ticket notes (one row per note per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_note (
    snapshot_id     BIGINT NOT NULL,
    note_id         BIGINT NOT NULL,            -- Autotask TicketNotes.id
    ticket_id       BIGINT NOT NULL,
    company_id      BIGINT NOT NULL,

    title           TEXT,
    description     TEXT,
    note_type       TEXT,
    publish         TEXT,
    created_at      TIMESTAMPTZ,

    PRIMARY KEY (snapshot_id, note_id),
    FOREIGN KEY (snapshot_id, ticket_id) REFERENCES ticket (snapshot_id, ticket_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ticket_note_ticket_idx   ON ticket_note (ticket_id);
CREATE INDEX IF NOT EXISTS ticket_note_snapshot_idx ON ticket_note (snapshot_id);

-- ---------------------------------------------------------------------------
-- Company contacts (one row per contact per snapshot)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contact (
    snapshot_id     BIGINT NOT NULL,
    contact_id      BIGINT NOT NULL,            -- Autotask Contacts.id
    company_id      BIGINT NOT NULL,

    first_name      TEXT,
    last_name       TEXT,
    title           TEXT,
    email           TEXT,
    phone           TEXT,
    mobile_phone    TEXT,
    is_active       BOOLEAN,

    PRIMARY KEY (snapshot_id, contact_id),
    FOREIGN KEY (snapshot_id, company_id) REFERENCES company (snapshot_id, company_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS contact_company_idx ON contact (company_id);
CREATE INDEX IF NOT EXISTS contact_email_idx   ON contact (email);

-- BEGIN imported_at (DATA-01, approved 12 Nov 2026): generated by docs/tools/generate_imported_at.py, do not edit by hand.
-- imported_at is when a row was written to this database. captured_at, where a table has one, is when the source data was captured.
-- Rows stored before this existed are left empty (NULL) on purpose: nobody knows when they were imported. Every row stored from now on
-- gets the time it was inserted, with no change to the loaders (they name their columns). Safe to run again; no existing row is touched.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE customers ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE snapshot ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE company ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE company ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE ticket ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE ticket ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE ticket_note ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE ticket_note ALTER COLUMN imported_at SET DEFAULT now();
ALTER TABLE contact ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
ALTER TABLE contact ALTER COLUMN imported_at SET DEFAULT now();
-- END imported_at


-- ===========================================================================
-- Views over the most recent snapshot per company — what reports should read
-- ===========================================================================

-- One row per company: its newest snapshot. Unlike Cove, snapshots here are
-- per-company, so "latest" must be resolved per company_filter rather than
-- globally.
CREATE OR REPLACE VIEW v_latest_snapshot AS
SELECT DISTINCT ON (company_filter) *
FROM snapshot
ORDER BY company_filter, captured_at DESC;

CREATE OR REPLACE VIEW v_company_latest AS
SELECT c.*
FROM company c
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_ticket_latest AS
SELECT t.*
FROM ticket t
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_ticket_note_latest AS
SELECT n.*
FROM ticket_note n
JOIN v_latest_snapshot s USING (snapshot_id);

CREATE OR REPLACE VIEW v_contact_latest AS
SELECT ct.*
FROM contact ct
JOIN v_latest_snapshot s USING (snapshot_id);

-- Customer service scorecard, latest snapshot per company
CREATE OR REPLACE VIEW v_company_summary AS
SELECT
    company_name,
    count(*)                                              AS total_tickets,
    count(*) FILTER (WHERE completed_at IS NOT NULL)       AS completed_tickets,
    count(*) FILTER (WHERE completed_at IS NULL)           AS open_tickets,
    count(*) FILTER (WHERE sla_met)                        AS sla_met,
    count(*) FILTER (WHERE sla_met IS FALSE)               AS sla_breached,
    count(*) FILTER (WHERE first_response_met)             AS first_response_met,
    count(*) FILTER (WHERE first_response_met IS FALSE)    AS first_response_missed,
    round(100.0 * count(*) FILTER (WHERE sla_met)
          / NULLIF(count(*) FILTER (WHERE sla_met IS NOT NULL), 0), 1)
                                                          AS sla_met_pct,
    round(avg(business_hours), 2)                          AS avg_business_hours,
    round(max(business_hours), 2)                          AS worst_business_hours,
    count(*) FILTER (WHERE priority ILIKE '%critical%'
                        OR priority ILIKE '%high%')        AS high_priority_tickets
FROM v_ticket_latest
GROUP BY company_name;
