-- ===========================================================================
-- Autotask reporting — read queries
--
-- Snapshots are per-company, so "latest" means the newest snapshot *for that
-- company*. The v_*_latest views in schema.sql already resolve that; use them
-- for current-state questions and the raw tables for trends across time.
--
-- Run one at a time:  psql "$AUTOTASK_PG_DSN" -f sql/queries.sql
-- (or paste individually — this file is a reference, not a script)
-- ===========================================================================

SET search_path TO autotask, public;

-- ---------------------------------------------------------------------------
-- 1. What is loaded — the driver list and the most recent load per customer
-- ---------------------------------------------------------------------------
SELECT c.customer_name,
       c.autotask_company_id,
       c.is_active,
       s.snapshot_id,
       s.captured_at,
       s.days_back,
       s.ticket_count
FROM customers c
LEFT JOIN v_latest_snapshot s ON s.company_filter = c.customer_name
ORDER BY c.is_active DESC, c.customer_name;

-- ---------------------------------------------------------------------------
-- 2. Service scorecard — one row per customer, latest snapshot
-- ---------------------------------------------------------------------------
SELECT * FROM v_company_summary ORDER BY sla_met_pct NULLS LAST;

-- ---------------------------------------------------------------------------
-- 3. SLA breaches, latest snapshot — the "why did we miss" list
-- ---------------------------------------------------------------------------
SELECT company_name,
       ticket_number,
       priority,
       status,
       business_hours,
       created_at,
       completed_at,
       title
FROM v_ticket_latest
WHERE sla_met IS FALSE
ORDER BY company_name, business_hours DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- 4. First-response misses — separately tracked from resolution SLA
-- ---------------------------------------------------------------------------
SELECT company_name,
       ticket_number,
       priority,
       first_response_due_at,
       first_response_at,
       round((EXTRACT(EPOCH FROM (first_response_at - first_response_due_at)) / 3600.0)::numeric, 2)
           AS hours_late,
       title
FROM v_ticket_latest
WHERE first_response_met IS FALSE
ORDER BY hours_late DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- 5. Still open, oldest first — the ageing list
-- ---------------------------------------------------------------------------
SELECT company_name,
       ticket_number,
       status,
       priority,
       created_at,
       round((EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0)::numeric, 1) AS days_open,
       title
FROM v_ticket_latest
WHERE completed_at IS NULL
ORDER BY created_at;

-- ---------------------------------------------------------------------------
-- 6. Where the work comes from — issue mix per customer
-- ---------------------------------------------------------------------------
SELECT company_name,
       coalesce(issue_type, '(none)')     AS issue_type,
       coalesce(sub_issue_type, '(none)') AS sub_issue_type,
       count(*)                           AS tickets,
       round(avg(business_hours), 2)      AS avg_business_hours
FROM v_ticket_latest
GROUP BY company_name, issue_type, sub_issue_type
ORDER BY company_name, tickets DESC;

-- ---------------------------------------------------------------------------
-- 7. Resolution time by priority — is severity actually driving response?
-- ---------------------------------------------------------------------------
SELECT company_name,
       coalesce(priority, '(none)')                   AS priority,
       count(*)                                       AS tickets,
       round(avg(business_hours), 2)                  AS avg_business_hours,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY business_hours)::numeric, 2)
                                                      AS median_business_hours,
       round(max(business_hours), 2)                  AS worst_business_hours
FROM v_ticket_latest
WHERE business_hours IS NOT NULL
GROUP BY company_name, priority
ORDER BY company_name, avg_business_hours DESC;

-- ---------------------------------------------------------------------------
-- 8. Monthly ticket volume and SLA trend, across all snapshots
--    DISTINCT ON de-duplicates a ticket that appears in several snapshots,
--    keeping the newest capture of it.
-- ---------------------------------------------------------------------------
WITH newest_per_ticket AS (
    SELECT DISTINCT ON (t.ticket_id)
           t.ticket_id, t.company_name, t.created_at, t.business_hours, t.sla_met
    FROM ticket t
    JOIN snapshot s USING (snapshot_id)
    ORDER BY t.ticket_id, s.captured_at DESC
)
SELECT company_name,
       date_trunc('month', created_at)::date AS month,
       count(*)                              AS tickets,
       count(*) FILTER (WHERE sla_met IS FALSE) AS sla_breached,
       round(avg(business_hours), 2)         AS avg_business_hours
FROM newest_per_ticket
GROUP BY company_name, month
ORDER BY company_name, month;

-- ---------------------------------------------------------------------------
-- 9. Snapshot-over-snapshot comparison for one customer
--    Change :customer to the company name you want.
-- ---------------------------------------------------------------------------
SELECT s.captured_at,
       s.days_back,
       count(t.ticket_id)                                  AS tickets,
       count(t.ticket_id) FILTER (WHERE t.sla_met IS FALSE) AS sla_breached,
       round(avg(t.business_hours), 2)             AS avg_business_hours
FROM snapshot s
LEFT JOIN ticket t USING (snapshot_id)
WHERE s.company_filter = 'IP Capital'
GROUP BY s.snapshot_id, s.captured_at, s.days_back
ORDER BY s.captured_at DESC;

-- ---------------------------------------------------------------------------
-- 10. Ticket detail with its notes — the narrative for a report write-up
-- ---------------------------------------------------------------------------
SELECT t.company_name,
       t.ticket_number,
       t.title,
       t.status,
       t.resolution,
       n.created_at AS note_at,
       n.title      AS note_title,
       n.description AS note_body
FROM v_ticket_latest t
LEFT JOIN ticket_note n
       ON n.snapshot_id = t.snapshot_id AND n.ticket_id = t.ticket_id
WHERE t.company_name = 'IP Capital: BITCO'
ORDER BY t.created_at DESC, n.created_at;

-- ---------------------------------------------------------------------------
-- 11. Company detail out of the JSONB blob
--     Keys follow Autotask's Companies entity — inspect first with:
--       SELECT jsonb_pretty(details) FROM v_company_latest LIMIT 1;
-- ---------------------------------------------------------------------------
SELECT company_name,
       details ->> 'phone'         AS phone,
       details ->> 'city'          AS city,
       details ->> 'countryID'     AS country_id,
       details ->> 'webAddress'    AS web_address,
       details ->> 'accountNumber' AS account_number,
       details ->> 'companyType'   AS company_type
FROM v_company_latest
ORDER BY company_name;

-- ---------------------------------------------------------------------------
-- 12. Contacts per customer, latest snapshot
-- ---------------------------------------------------------------------------
SELECT c.company_name,
       ct.first_name,
       ct.last_name,
       ct.title,
       ct.email,
       coalesce(ct.phone, ct.mobile_phone) AS phone
FROM v_contact_latest ct
JOIN v_company_latest c USING (snapshot_id, company_id)
WHERE ct.is_active
ORDER BY c.company_name, ct.last_name, ct.first_name;

-- ---------------------------------------------------------------------------
-- 13. Housekeeping — snapshot sizes, and pruning old ones
--     Deleting a snapshot cascades to company/ticket/ticket_note/contact.
-- ---------------------------------------------------------------------------
SELECT company_filter,
       count(*)          AS snapshots,
       min(captured_at)  AS first_load,
       max(captured_at)  AS last_load,
       sum(ticket_count) AS total_ticket_rows
FROM snapshot
GROUP BY company_filter
ORDER BY company_filter;

-- Keep the three most recent snapshots per company:
-- DELETE FROM snapshot s
-- WHERE s.snapshot_id NOT IN (
--     SELECT snapshot_id FROM (
--         SELECT snapshot_id,
--                row_number() OVER (PARTITION BY company_filter
--                                   ORDER BY captured_at DESC) AS rn
--         FROM snapshot
--     ) ranked WHERE rn <= 3
-- );
