-- ===========================================================================
-- SentinelOne EDR reporting — query library
-- Run against the `sentinelone` schema:
--   psql "$SENTINELONE_PG_DSN" -f sql/queries.sql
--
-- Most queries read the v_*_latest views, which resolve the newest snapshot per
-- customer per month — necessary because per-customer runs mean different
-- customers are loaded at different times.
-- ===========================================================================

SET search_path TO sentinelone, public;

-- ---------------------------------------------------------------------------
-- 1. Portfolio overview, latest month on record
-- ---------------------------------------------------------------------------
SELECT * FROM v_portfolio_month
ORDER BY report_month DESC
LIMIT 1;

-- ---------------------------------------------------------------------------
-- 2. Customer scorecard, worst first — the master summary table
-- ---------------------------------------------------------------------------
SELECT customer_name, risk_level, risk_score, threats, mitigation_rate,
       not_mitigated, ransomware, infostealer, lateral_movement,
       devices, infected, detect_only, health_issues, outdated_agents,
       stale_agents, avg_mttr_minutes
FROM v_customer_month_latest
WHERE report_month = (SELECT max(report_month) FROM v_customer_month_latest)
ORDER BY CASE risk_level
             WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
             WHEN 'MEDIUM'   THEN 2 ELSE 3 END,
         threats DESC;

-- ---------------------------------------------------------------------------
-- 3. One customer's history — is it getting better or worse?
-- ---------------------------------------------------------------------------
SELECT report_month, threats, threat_delta, risk_score, risk_delta, risk_level
FROM v_customer_trend
WHERE customer_name = 'REPLACE ME'
ORDER BY report_month;

-- ---------------------------------------------------------------------------
-- 4. Unresolved threats needing attention now
-- ---------------------------------------------------------------------------
SELECT customer_name, device_name, threat_name, classification,
       confidence_level, process_user, created_at
FROM v_threat_latest
WHERE mitigation_status = 'not_mitigated'
ORDER BY (classification IN ('Ransomware', 'Infostealer', 'Backdoor')) DESC,
         created_at DESC;

-- ---------------------------------------------------------------------------
-- 5. Endpoints not auto-blocking — detect-only is monitoring, not protection
-- ---------------------------------------------------------------------------
SELECT customer_name, computer_name, os_name, agent_version, last_active
FROM v_agent_latest
WHERE mitigation_mode = 'detect'
ORDER BY customer_name, computer_name;

-- ---------------------------------------------------------------------------
-- 6. Agent health issues and stale endpoints
-- ---------------------------------------------------------------------------
SELECT customer_name, computer_name, os_name, agent_version,
       user_actions_needed, health_issue_count,
       last_active, days_since_active, is_stale
FROM v_agent_latest
WHERE health_issue_count > 0 OR is_stale OR infected
ORDER BY infected DESC, health_issue_count DESC, days_since_active DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- 7. Outdated agents against the newest package seen at capture time
-- ---------------------------------------------------------------------------
SELECT a.customer_name, a.computer_name, a.agent_version,
       s.latest_agent_version, a.os_name
FROM v_agent_latest a
JOIN snapshot s USING (snapshot_id)
WHERE a.is_up_to_date IS false
ORDER BY a.customer_name, a.agent_version;

-- ---------------------------------------------------------------------------
-- 8. Ransomware and infostealer activity across the estate
-- ---------------------------------------------------------------------------
SELECT report_month, customer_name, device_name, threat_name, classification,
       mitigation_status, process_user, file_path, created_at
FROM v_threat_latest
WHERE classification IN ('Ransomware', 'Infostealer', 'Backdoor')
ORDER BY created_at DESC;

-- ---------------------------------------------------------------------------
-- 9. MITRE ATT&CK coverage — which tactics we are actually seeing
-- ---------------------------------------------------------------------------
SELECT m.tactic, count(DISTINCT m.customer_name) AS customers,
       sum(m.threat_count) AS threats
FROM mitre_tactic m
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name)
GROUP BY m.tactic
ORDER BY threats DESC;

-- ---------------------------------------------------------------------------
-- 10. Living-off-the-land activity — legitimate binaries used maliciously
-- ---------------------------------------------------------------------------
SELECT l.customer_name, l.threat_name, l.detections
FROM lolbin l
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name)
ORDER BY l.detections DESC, l.customer_name;

-- ---------------------------------------------------------------------------
-- 11. Response time league table — who waits longest for mitigation
-- ---------------------------------------------------------------------------
SELECT customer_name, threats, mitigated, mitigation_rate, avg_mttr_minutes,
       round(avg_mttr_minutes / 60.0, 1) AS avg_mttr_hours
FROM v_customer_month_latest
WHERE report_month = (SELECT max(report_month) FROM v_customer_month_latest)
  AND avg_mttr_minutes IS NOT NULL
ORDER BY avg_mttr_minutes DESC;

-- ---------------------------------------------------------------------------
-- 12. Repeat-offender devices — same machine hit across multiple months
-- ---------------------------------------------------------------------------
SELECT customer_name, device_name,
       count(DISTINCT report_month) AS months_affected,
       count(*)                     AS total_detections
FROM v_threat_latest
WHERE device_name IS NOT NULL
GROUP BY customer_name, device_name
HAVING count(DISTINCT report_month) > 1
ORDER BY months_affected DESC, total_detections DESC;

-- ---------------------------------------------------------------------------
-- 13. Users whose sessions keep triggering detections
-- ---------------------------------------------------------------------------
SELECT t.customer_name, t.process_user, count(*) AS detections,
       count(DISTINCT t.device_name) AS devices
FROM v_threat_latest t
WHERE t.process_user IS NOT NULL
  AND upper(t.process_user) NOT LIKE 'NT AUTHORITY%'
GROUP BY t.customer_name, t.process_user
ORDER BY detections DESC
LIMIT 30;

-- ---------------------------------------------------------------------------
-- 14. Daily detection trend for one customer
-- ---------------------------------------------------------------------------
SELECT d.trend_date, d.threat_count
FROM daily_trend d
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name)
WHERE d.customer_name = 'REPLACE ME'
ORDER BY d.trend_date;

-- ---------------------------------------------------------------------------
-- 15. OS and agent-version spread across the estate
-- ---------------------------------------------------------------------------
SELECT o.os_name, sum(o.device_count) AS devices
FROM os_spread o
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name)
GROUP BY o.os_name
ORDER BY devices DESC;

SELECT v.agent_version, sum(v.device_count) AS devices
FROM agent_version v
JOIN v_current_snapshot_ids c USING (snapshot_id, customer_name)
GROUP BY v.agent_version
ORDER BY v.agent_version DESC;

-- ---------------------------------------------------------------------------
-- 16. Roster health — active customers with no data for the latest month
-- ---------------------------------------------------------------------------
SELECT site_id, customer_name, last_loaded_month
FROM v_coverage_gaps
WHERE last_loaded_month IS DISTINCT FROM
      (SELECT max(report_month) FROM v_customer_month_latest)
ORDER BY last_loaded_month NULLS FIRST, customer_name;

-- ---------------------------------------------------------------------------
-- 17. Load audit — what ran, when, which period, and how much it brought back
-- ---------------------------------------------------------------------------
SELECT snapshot_id, captured_at, report_month, period_label,
       month_start AS period_start, month_end AS period_end,
       is_partial_month, days_covered,
       coalesce(customer_filter, '<whole estate>') AS scope,
       customer_count, threat_count, agent_count, latest_agent_version, notes
FROM snapshot
ORDER BY captured_at DESC
LIMIT 25;

-- ---------------------------------------------------------------------------
-- 17b. Completed months only — for reporting that must not mix a partial month
--      in with finished ones
-- ---------------------------------------------------------------------------
SELECT report_month, period_label, customers, devices, threats, unresolved
FROM v_portfolio_month
WHERE NOT is_partial_month
ORDER BY report_month DESC;

-- ---------------------------------------------------------------------------
-- 17c. Per-day rate, so a month to date compares fairly against complete months
-- ---------------------------------------------------------------------------
SELECT customer_name, report_month, period_label, days_covered,
       threats, threats_per_day
FROM v_customer_trend
WHERE customer_name = 'REPLACE ME'
ORDER BY report_month;

-- ---------------------------------------------------------------------------
-- 18. Everything flagged for one customer's latest month, as one list
-- ---------------------------------------------------------------------------
SELECT category, device_name, detail
FROM v_red_flag_latest
WHERE customer_name = 'REPLACE ME'
ORDER BY category, device_name;
