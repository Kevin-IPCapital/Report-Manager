# On-Demand Report Prompt Templates
# Paste these into GitHub Copilot Chat, Claude, or any AI assistant

---

## GENERATE A REPORT FOR ONE CUSTOMER

```
Using the SentinelOne API at the base URL in .env, generate a monthly security
report for the customer site "[CUSTOMER NAME]" for [MONTH YEAR].

Follow the exact 11-section format defined in .github/copilot-instructions.md.
Pull all threat data with pagination using createdAt__gte and createdAt__lte
filters for the month. Pull all agent data for that site.

Calculate the risk score and assign the risk level using the scoring formula.
Do not skip any section. If a section has no data, say "None detected" or
"No [items] this period" rather than omitting the section entirely.

Output the report in plain text exactly matching the format in copilot-instructions.md.
```

---

## REGENERATE ALL CUSTOMER REPORTS FOR A MONTH

```
Run Generate-MonthlyReports.ps1 for [MONTH e.g. 2026-06] and generate individual
reports for every active customer site. Save them to .\Reports\ as
[CustomerName]_[YYYY-MM].txt plus a _MASTER_SUMMARY_ file.

Follow all rules in .github/copilot-instructions.md. Do not modify the report
format or section order.
```

---

## ADD A NEW SECTION TO THE REPORT

```
Add a new section "[SECTION NAME]" to Generate-MonthlyReports.ps1.
Insert it between section [X] and section [X+1].
Renumber all subsequent sections.

Data to include:
- [describe the data]

Format rules:
- Use the same header style as existing sections (number + title + dashes)
- Left-align labels padded to 26 characters with a colon separator
- Omit the section if there is no data to show

Update .github/copilot-instructions.md to include the new section in the
format reference so future reports are consistent.
```

---

## GENERATE MASTER SUMMARY ONLY

```
Using data already pulled from SentinelOne for [MONTH YEAR], generate only the
master summary table. Include all customers sorted by risk level (CRITICAL first)
then by threat count descending.

Columns: Customer | Risk | Threats | MitigRate | Unresolved | Ransomware |
         Infostealer | LateralMov | Devices | Infected | DetectOnly | Outdated

Use the risk scoring formula from .github/copilot-instructions.md.
```

---

## INVESTIGATE A SPECIFIC CUSTOMER

```
Pull detailed threat and agent data from SentinelOne for the customer site
"[CUSTOMER NAME]" for the last [30 / 7 / 90] days.

Summarise:
1. Total threats and mitigation rate
2. Any unresolved threats with full detail (date, name, device, user, classification)
3. MITRE ATT&CK tactics observed
4. All agent health issues (detect-only mode, outdated versions, health flags, stale devices)
5. Infected devices
6. Top threat names and affected devices
7. Recommended immediate actions

Use the same risk scoring and format rules from .github/copilot-instructions.md.
```

---

## ADD A NEW CUSTOMER / SITE

```
A new customer site "[SITE NAME]" has been added to SentinelOne.
Verify the site appears in /web/api/v2.1/sites and that agents are reporting.

Check:
- Site ID and state (should be "active")
- Number of agents registered
- Agent versions (compare to latest: 25.1.4.434)
- Protection mode (all should be "protect", not "detect")
- Any immediate threats or health issues

Output a brief onboarding health check using the same format as section 9
(Endpoint Health & Coverage) from the standard report.
```

---

## MONTH-OVER-MONTH COMPARISON

```
Compare threat data for [CUSTOMER NAME] across the last 3 months.

For each month pull:
- Total threats detected
- Mitigation rate
- Unresolved count
- Ransomware count
- Infostealer count
- Lateral movement count

Display as a table with months as columns. Add a trend indicator
(↑ increase / ↓ decrease / → no change) for each metric month-over-month.
Note any significant spikes or improvements.
```
