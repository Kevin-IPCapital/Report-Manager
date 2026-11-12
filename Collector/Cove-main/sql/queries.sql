-- ===========================================================================
-- Cove reporting — query library
-- Run against the `cove` schema:  psql "$COVE_PG_DSN" -f sql/queries.sql
-- Most queries read the v_*_latest views (most recent snapshot only).
-- ===========================================================================

SET search_path TO cove, public;

-- ---------------------------------------------------------------------------
-- 1. Estate overview — one row
-- ---------------------------------------------------------------------------
SELECT
    count(DISTINCT customer)                    AS customers,
    count(*)                                    AS devices,
    count(*) FILTER (WHERE is_server)           AS servers,
    count(*) FILTER (WHERE is_m365)             AS m365_accounts,
    count(*) FILTER (WHERE health = 'CRITICAL') AS critical,
    count(*) FILTER (WHERE health = 'WARNING')  AS warning,
    round(sum(used_storage_bytes) / 1024.0^4, 2) AS used_storage_tb
FROM v_device_latest;

-- ---------------------------------------------------------------------------
-- 2. Customer scorecard, worst first
-- ---------------------------------------------------------------------------
SELECT * FROM v_customer_summary
ORDER BY critical DESC, warning DESC, red_flags DESC;

-- ---------------------------------------------------------------------------
-- 3. Devices needing attention now
-- ---------------------------------------------------------------------------
SELECT customer, device_name, hostname, os_type, health,
       days_since_success, last_success, red_flag_count
FROM v_device_latest
WHERE health <> 'OK'
ORDER BY (health = 'CRITICAL') DESC, days_since_success DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- 4. Stale backups — nothing successful in over 3 days
-- ---------------------------------------------------------------------------
SELECT customer, device_name, account_type, last_success, days_since_success
FROM v_device_latest
WHERE days_since_success > 3 OR last_success IS NULL
ORDER BY days_since_success DESC NULLS FIRST;

-- ---------------------------------------------------------------------------
-- 5. Red flags grouped by category — where to spend the week
-- ---------------------------------------------------------------------------
SELECT category,
       count(*)                        AS occurrences,
       count(DISTINCT customer)        AS customers_affected,
       count(DISTINCT device_id)       AS devices_affected
FROM v_red_flag_latest
GROUP BY category
ORDER BY occurrences DESC;

-- ---------------------------------------------------------------------------
-- 6. Full red-flag list for one customer (parameterise :cust)
-- ---------------------------------------------------------------------------
SELECT device_name, category, flag
FROM v_red_flag_latest
WHERE customer ILIKE '%' || :'cust' || '%'
ORDER BY device_name, category;

-- ---------------------------------------------------------------------------
-- 7. Chronic offenders — same datasource failing repeatedly over 28 days
-- ---------------------------------------------------------------------------
SELECT customer, device_name, ds_name, status,
       failed_days, missed_days, warning_days, max_fail_streak, colorbar_raw
FROM v_datasource_latest
WHERE recurring_failures OR chronic_missed OR chronic_warnings
ORDER BY failed_days DESC, missed_days DESC;

-- ---------------------------------------------------------------------------
-- 8. M365 coverage matrix — one row per account, one column per source
-- ---------------------------------------------------------------------------
SELECT customer, device_name,
       max(coverage) FILTER (WHERE source_label = 'Exchange')   AS exchange,
       max(coverage) FILTER (WHERE source_label = 'OneDrive')   AS onedrive,
       max(coverage) FILTER (WHERE source_label = 'SharePoint') AS sharepoint,
       max(coverage) FILTER (WHERE source_label = 'Teams')      AS teams
FROM v_m365_latest
GROUP BY customer, device_name
ORDER BY customer, device_name;

-- ---------------------------------------------------------------------------
-- 9. M365 gaps only — the actionable subset of query 8
-- ---------------------------------------------------------------------------
SELECT customer, device_name, source_label, coverage, last_success, item_count
FROM v_m365_latest
WHERE coverage <> 'OK'
ORDER BY customer, device_name, source_label;

-- ---------------------------------------------------------------------------
-- 10. Security posture gaps
-- ---------------------------------------------------------------------------
SELECT customer, device_name, os_type,
       passphrase_set, immutable_enabled, fips_enabled, dr_testing, retention_days
FROM v_device_latest
WHERE NOT is_m365
  AND (NOT passphrase_set
       OR NOT immutable_enabled
       OR (is_server AND dr_testing = 'Not Configured')
       OR retention_days < 90)
ORDER BY customer, device_name;

-- ---------------------------------------------------------------------------
-- 11. Servers without automated DR testing
-- ---------------------------------------------------------------------------
SELECT customer, device_name, os, dr_testing
FROM v_device_latest
WHERE is_server AND dr_testing = 'Not Configured'
ORDER BY customer, device_name;

-- ---------------------------------------------------------------------------
-- 12. Customers not receiving scheduled reports
-- ---------------------------------------------------------------------------
SELECT customer,
       count(*)                                  AS devices,
       count(*) FILTER (WHERE NOT reporting_enabled) AS devices_without_reporting
FROM v_device_latest
GROUP BY customer
HAVING count(*) FILTER (WHERE NOT reporting_enabled) > 0
ORDER BY devices_without_reporting DESC;

-- ---------------------------------------------------------------------------
-- 13. Lifecycle risk — EOL operating systems and outdated agents
-- ---------------------------------------------------------------------------
SELECT customer, device_name, os, os_eol, client_version, client_old
FROM v_device_latest
WHERE os_eol IS NOT NULL OR client_old
ORDER BY customer, device_name;

-- ---------------------------------------------------------------------------
-- 14. Billing reconciliation — chargeable devices and M365 seats per customer
-- ---------------------------------------------------------------------------
SELECT d.customer,
       count(*) FILTER (WHERE NOT d.is_m365)                    AS endpoint_devices,
       count(*) FILTER (WHERE NOT d.is_m365 AND d.is_server)    AS servers,
       count(*) FILTER (WHERE d.is_m365)                        AS m365_accounts,
       coalesce(sum(m.user_count), 0)                           AS m365_licensed_seats,
       round(sum(d.used_storage_bytes) / 1024.0^3, 2)           AS used_storage_gb
FROM v_device_latest d
LEFT JOIN v_m365_latest m
       ON m.device_id = d.device_id AND m.source_label = 'Exchange'
GROUP BY d.customer
ORDER BY d.customer;

-- ---------------------------------------------------------------------------
-- 15. Storage growth per customer across snapshots (trend)
-- ---------------------------------------------------------------------------
SELECT s.captured_at::date                         AS day,
       d.customer,
       round(sum(d.used_storage_bytes) / 1024.0^3, 2) AS used_storage_gb,
       count(*)                                    AS devices
FROM device d
JOIN snapshot s USING (snapshot_id)
GROUP BY day, d.customer
ORDER BY d.customer, day;

-- ---------------------------------------------------------------------------
-- 16. Health trend across the estate — is it getting better or worse?
-- ---------------------------------------------------------------------------
SELECT s.captured_at,
       count(*)                                    AS devices,
       count(*) FILTER (WHERE d.health = 'CRITICAL') AS critical,
       count(*) FILTER (WHERE d.health = 'WARNING')  AS warning,
       count(*) FILTER (WHERE d.health = 'OK')       AS ok
FROM device d
JOIN snapshot s USING (snapshot_id)
GROUP BY s.snapshot_id, s.captured_at
ORDER BY s.captured_at;

-- ---------------------------------------------------------------------------
-- 17. New vs resolved red flags between the two most recent snapshots
-- ---------------------------------------------------------------------------
WITH snaps AS (
    SELECT snapshot_id,
           row_number() OVER (ORDER BY captured_at DESC) AS rn
    FROM snapshot
    ORDER BY captured_at DESC
    LIMIT 2
),
curr AS (SELECT f.device_id, f.customer, f.device_name, f.flag
         FROM red_flag f JOIN snaps s USING (snapshot_id) WHERE s.rn = 1),
prev AS (SELECT f.device_id, f.flag
         FROM red_flag f JOIN snaps s USING (snapshot_id) WHERE s.rn = 2)
SELECT 'NEW' AS change, c.customer, c.device_name, c.flag
FROM curr c
LEFT JOIN prev p ON p.device_id = c.device_id AND p.flag = c.flag
WHERE p.flag IS NULL
UNION ALL
SELECT 'RESOLVED', d.customer, d.device_name, p.flag
FROM prev p
JOIN curr d ON d.device_id = p.device_id
LEFT JOIN curr c ON c.device_id = p.device_id AND c.flag = p.flag
WHERE c.flag IS NULL
GROUP BY d.customer, d.device_name, p.flag
ORDER BY change, customer, device_name;

-- ---------------------------------------------------------------------------
-- 18. Devices that disappeared since the previous snapshot (churn / offboarding)
-- ---------------------------------------------------------------------------
WITH snaps AS (
    SELECT snapshot_id, row_number() OVER (ORDER BY captured_at DESC) AS rn
    FROM snapshot ORDER BY captured_at DESC LIMIT 2
)
SELECT p.customer, p.device_name, p.last_success
FROM device p JOIN snaps s USING (snapshot_id)
WHERE s.rn = 2
  AND NOT EXISTS (
      SELECT 1 FROM device c JOIN snaps s2 USING (snapshot_id)
      WHERE s2.rn = 1 AND c.device_id = p.device_id
  )
ORDER BY p.customer, p.device_name;

-- ---------------------------------------------------------------------------
-- 19. Immutable-copy failures (ransomware protection not landing)
-- ---------------------------------------------------------------------------
SELECT customer, device_name, ds_name, imm_last_status, imm_last_at
FROM v_datasource_latest
WHERE imm_active AND imm_last_status_code NOT IN ('4', '5', '0')
ORDER BY customer, device_name;

-- ---------------------------------------------------------------------------
-- 20. LSV and cloud storage health problems
-- ---------------------------------------------------------------------------
SELECT customer, device_name, lsv_enabled, lsv_status, cloud_status
FROM v_device_latest
WHERE (lsv_enabled AND lsv_status NOT IN ('Synchronized', 'Running'))
   OR (NOT is_m365 AND cloud_status NOT IN ('Synchronized', 'Running', 'Unknown'))
ORDER BY customer, device_name;
