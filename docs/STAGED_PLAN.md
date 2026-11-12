# Staged plan

Status: **Stage 0 done. Stage 1 tracks A (security), B (collection ops), C (clients), D (backend scoring), E (tickets), F (server health), G (reports), H (shared UI) and I (Cove, except its first live run) built. Done alongside F: FRESH-01 and the Server Health and Backups parts of FILTER-01. Build 2 (SORT-01 and PERSIST-01) is done too, and so is a batch of independent items (REPORT-07, most of RANGE-01 and TREND-02, the ticket trend, the Patches device filter, DATA-03 and DATA-04). Left: OVER-01, REPORT-06 (needs your answer), SCORE-02 and NC-01 (decisions), DATA-01 and TICK-02 (need approval to change live schemas), SRC-01 (your first live Cove run), ACC-01.**
Stage 1 tracks are independent unless a dependency is listed.

## Decisions taken
- Access control comes last, using Django groups plus a user-to-client link (built-in permissions are not row-level). Until then
  collector endpoints are staff-only and data endpoints are login-only. The seam: every data function will take an optional client list.
- Patch score weights: **one shared default set**; each user can keep their own; a report template can carry its own. Scores are
  computed on the backend. Snapshots store the counts and the weights used, not only the final score.
- Clients: a master client table with `client_source_map(client, source, external_id)` and `client_alias`. IDs first, aliases second,
  an "Unmapped" list for a person to resolve. Names alone are never a join key.
- Report templates: a date range can always be entered, even when a predefined template is chosen; a template can carry its own weights (REPORT-05).
- **"Worst" means (decided 10 Nov 2026):** Patches, the category with the lowest compliance first; Server Health, infected, then not connected, then the oldest data; Tickets, late or flagged first, then the longest open; Backups, critical, warning, then OK. Pages start in that order; the Overview is exempt.
- **Remembered inputs live in the browser, per person** (decided 10 Nov 2026, as recommended): simplest, no database change. They are all read and written through `frontend/src/storage.js`, so moving them to the server later means replacing that one file. A date range that ended today is remembered as its length, so it stays "the last N days".

## Stage 0 (done)
Data layer split by source; stale tests replaced with a fake reporting database; scheduler fields and freshness intervals in the job
config; Autotask disabled while failing; dev collector access off by default and `makestaff` added; `is_active` returned and
decommissioned agents hidden; dead files removed; README refreshed; Cove registered (disabled).

## Stage 1 (parallel)
| Track | Items | Main files |
|---|---|---|
| A Security **(done)** | Bypass off by default, sign-in and reset throttling, password validators, production settings, staff-only management. Not done: role model (comes last) | `settings.py`, `auth.py`, `views.py` |
| B Collection ops **(built, not yet run on your machine)** | `run_scheduler`, Task Scheduler script, heartbeat (also during long jobs), email alerts for failed, stuck, overdue and silent-worker cases | `collector_queue.py`, `collection_alerts.py`, `deploy/` |
| C Clients **(mostly done)** | Master table, source map, aliases, token-based grouping with inference flag, Client Resolution page with discrepancy messages, `client_id` on tickets and servers. **Added to do:** DATA-03 (a joinable master client list holding every schema's reference, so a query can join to any of them) and DATA-04 (a lasting record of clients missing across schemas, with first/last seen and an alert). Not done: `imported_at` (needs DDL on the live databases) | `models.py`, `clients.py`, `Clients.jsx` |
| D Patches **(mostly done)** | Backend scoring, shared and per-user weights, snapshots, trend, device names, client filter. Not done: machine filter, pagination, month/quarter periods | `data/patches.py`, `snapshots.py`, `Patches.jsx` |
| E Tickets **(built; source-limited)** | Technician page and read side done, pagination done. The source does not collect assignments: needs your approval to apply `Collector/AutotaskLight-main/sql/proposed/technician.sql` and change the collector | `data/tickets.py`, `TicketExplorer.jsx` |
| F Server health **(built)** | Count tiles, tiles that filter, a device filter, CPU/RAM/disk placeholders, paged cards | `ServerHealth.jsx`, `serverHealth.js` |
| G Reports **(built)** | Saved report list, cover and contents page, blocks with their own filters, annex, per-report weights, dates at run time | `report/*`, `Reports.jsx`, `reports.py` |
| H UI **(built)** | Comparison search in number, duration, percent and date columns, hover definitions, collapse/expand all charts. No other defects found in the shared components | `filter.js`, `glossary.js`, `components/*` |
| I Cove **(built; waits on a live run)** | Data module, Backups page, report section done. Still yours: apply the schema, add `cove.customers` rows, run `cove_backup` once, then enable it | `Collector/Cove-main`, `data/backups.py`, `Backups.jsx` |

## Documentation
Read it all in one page with `python docs/serve.py` (or `docs/Open-Docs.cmd`): it lists every file in the folder, re-reads it continuously, searches, and draws the diagrams.

The documentation pack in this folder was regenerated against the code ([`08_API_REFERENCE.md`](08_API_REFERENCE.md) and the generated ER diagrams in [`09_ER_DIAGRAMS.md`](09_ER_DIAGRAMS.md) are new). `backend/api/test_docs.py` keeps routes, settings and jobs in step with it.

## Added by you on 10 November 2026 (recorded, not yet built)
- **OVER-01:** the Patches page becomes the overview page; its score table has a heading per client with that client's own scores, and the heading is the filter.
- **PATCH-02:** a filter for the patches table per device.
- **HEALTH-01 (done):** show how many endpoints and servers there are on Server Health, as tiles that also filter the cards.
- **REPORT-06 and REPORT-07:** a dropdown of existing data for the Client Tickets report; several values for the same filter, each as a bubble that can be removed.
- **RANGE-01:** date ranges kept as a duration only (the start date is left to the report), usable by trends and by the report generator.
- **FILTER-01:** a device filter on every page that has device data.
- **SORT-01:** worst-performing first on every page except Overview, or a sort order the user chooses (needs a decision on what "worst" means per page).
- **PERSIST-01:** remember the date range, filters, sort and page size from one session to the next (needs a decision: per browser, or per user on the server).
- **FRESH-01:** Data Collection judges freshness from the latest succeeded or partial run, not from a failure.

## Priorities, re-set on 11 November 2026 (you first, other people later)

You are the only person with access, and other companies and people come once you are happy. So everything about **other people** is parked, and the work goes to what you use.

**Done on 11 Nov:** the small fixes (OVER-06 and OVER-07 for Scores By Client, UX-06 alternating rows, UX-07 the sign-up edge), UX-08 (every graph can be minimised), DOC-02 (the front end and collectors guides), OVER-05 (the "data as of" line that says how old a page's data is), and OVER-08 (the Overall Score and the trend are for one client, with "Please select a single client" otherwise). Your own run of the whole test suite (470 tests) found 4 failures: three were one leftover file that an archive cannot delete, and one was a wrong expectation in a test of mine. All are fixed.

**What is left, in the order I would do it**
1. **Done on 12 Nov, waiting to be seen in a browser:**
   - **SERVER-01 and SERVER-03:** each Server Health card has three sections that open on their own headings. **Performance** (CPU, RAM, disk: still "No data"), **Patches** (a table of the three Overview categories: Last Added, Next Due, Up To Date) and **Data Collected** (the latest date found for the device on a successful run of each collector). A device N-central cannot find shows a **Disconnected** badge (NC-01).
   - **NC-01:** the run still says **Partial** on the Data Collection page; "disconnected" is shown per device on Server Health, as **N-central Status** beside **SentinelOne Status** on each card (SERVER-04, your idea), with a pair of tiles for each.
   - **RANGE-01** (date ranges you name and save), **SCORE-01** (how the weights make the score, with the working for one client), **DATA-01** (`imported_at` on every table, ready for you to apply) and **DEV-01** (build errors are caught before the page is opened).
   - **ACC-02 and ACC-03** (client groups, and making people from the Access page) are built, after you asked to continue with them. The pure rules are tested and the page was driven in a browser, but the new endpoints and their database tests have **not been run by me**: they need `python manage.py migrate` and `python manage.py test` on your machine. **ACC-17** (access groups) waits for your answer about what an access group is; my default is recorded there.
   - **UX-09 and UX-10:** Settings has dropdowns for Theme and Accent Colour, and the "data as of" line is back in the page's column on a wide window (it was at the far left).
   - **DOC-03** (refresh the handover document) is done: it is rewritten to describe the system as it now is, in Markdown, Word and PDF.
   - **TREND-01** (a trend for server health) is closed by you: the patch and ticket trends are what you wanted, so it will not be built.
2. **Left alone by you:** PATCH-02 (patch scores per device) and SCORE-02 (what "healthy" means: all three categories, or just one). SCORE-01 is done without waiting for it.
3. **On hold by you, while you speak with some people:** TICK-02 (collect the technician on each ticket, which also needs Autotask working) and SERVER-02 (collect the N-central patch schedule, which "Next Due" needs). Nothing is invented meanwhile: both keep saying "Not collected".
4. **Needs you to apply it:** DATA-01. It changes your live databases, so run each collector's `sql/migrations/2026-11-12_imported_at.sql` when no collector is running (see "Applying `imported_at`" in `docs/05_OPERATIONS.md`), then `python manage.py verify_data` shows whether it took.
5. **Yours, alongside:** run `python manage.py snapshot_scores --backfill` so the trend has history; run the three N-central jobs once from the Data Collection page now that N-central is back after its outage, so the Overview's figures and its "data as of" line catch up; fix Autotask so tickets load; and the release gates in Stage 3 (confirm what is deployed, a production build, an unattended scheduler run, a backup and restore drill).

**Parked until you add other people:** roles, rights, users, directors across branches, per-company weights and templates, and separate data sources (ACC-02 to ACC-09, ACC-13, ACC-15, ACC-16, FUT-01).

**What is already built and does not need your attention:** companies, the owner and the wall between companies (ACC-10 to ACC-12 and ACC-14) exist and are **switched off**. Leave `DJANGO_CLIENT_ACCESS_ENFORCED` unset and nothing about them touches you. Run `migrate` when you take the next archive; it adds the tables and places you and every client in one company.

## Plan from the 11 November 2026 review

Nothing here is built yet. It records what was decided, what depends on what, the order, and what can run in parallel.

### What the real-data check showed (10 Nov, run by you)
Six sections worked on real data and none failed; tickets and the trend were empty for known reasons (VERIFY-01 in [`06_STATUS_AND_BACKLOG.md`](06_STATUS_AND_BACKLOG.md)). All three N-central jobs were failing because N-central had an outage (confirmed by you on 11 Nov, now back up), so the Overview scores were from the 9 Nov run until a new run succeeds. That is why the "data as of" line (OVER-05) exists.

### Decisions recorded
| Area | Decision |
|---|---|
| Overview layout | The Overall Score card first, the Trend directly below it. Beside the Overall Score card, a card listing every client you may see with its device count, replacing the Client dropdown. |
| Overview filter | Clicking a name in that card filters the page and adds a bubble that removes just that client. Several clients can be chosen. Clicking a client's name in Scores By Client sets the filter to that client (replacing any other). |
| Old score table | Removed. Its functions move into Scores By Client: the type rows in each client's group are clickable for the drill-down, and Score Weights stays beside the Overall Score card. |
| Roles | A role holds **clients only** (picked from a list, shown as bubbles you can remove), plus an "All clients" tick. |
| Rights | **Separate from roles**, per person, as tick boxes: make new clients; resolve client resolutions; manage data collection; edit the shared weights. An admin has all of them and the Access and Roles pages. |
| Several roles | One role is the starting point, but the model and the rules handle several (clients added together). |
| New users | Created by an admin with username, name, email, role and rights. They get an emailed set-password link (also shown once to the admin); never a chosen password. Users are deactivated, not deleted. |
| Manage rights | Need an All-clients role, checked when granted and each time used. |
| Reports | Everyone creates personal reports. Only admins create templates for everyone; people can run a template and change only the dates. Creator or admin edits. Admins do not see others' private reports. |

### My reading, to be corrected if wrong
- **"The filter narrows the table and not the card"** I read as: the filter narrows Scores By Client and the drill-down, while the **clients card itself** stays complete (every client listed, chosen ones highlighted). **Revised 11 Nov:** the Overall Score card and the trend are for **exactly one client**; with none or several chosen they say "Please select a single client" instead of a combined figure (OVER-08 in the backlog).
- **"Separate: a rights list and a clients list"** I read as: clients belong to the **role**, rights belong to the **person**.
- With no filter chosen, the top group of Scores By Client is everyone the person may see; with clients chosen it is those clients combined. That group carries the drill-down the old table had.

### Items and what they depend on
| Item | Needs | Size | Risk |
|---|---|---|---|
| UX-05 the tickets-created chart can be minimised like the others (done) | nothing | small | low |
| REPORT-08 personal reports and templates | nothing (uses today's staff flag; moves to the admin right later) | small | low |
| OVER-04 several clients in the patch queries | nothing | medium | medium: the combined trend needs a design |
| OVER-02 and OVER-03 Overview layout and Scores By Client | OVER-04 | large (one page) | medium: the page cannot be tried in a browser here |
| OVER-05 a "data as of" line saying when each page's data was last collected and whether that is stale (done) | a small endpoint any signed-in person can read | small | low |
| ACC-02, ACC-05, ACC-08 roles, several roles, converting old grants | nothing | medium | high: it decides who sees what |
| ACC-04, ACC-06 rights and the All-clients rule | ACC-02 | medium | high |
| ACC-03 users, email, deactivation | ACC-02, ACC-04 | medium | medium: needs the `EMAIL_*` settings |
| ACC-07 every route and its required permission, with a test | ACC-04 | medium | the safeguard against a forgotten route |
| ACC-09 and the Roles and Users pages | ACC-02 to ACC-04 | large | medium: not testable in a browser here |
| ACC-10 companies: a company has clients, a person belongs to one company and sees a subset of its clients, and a company is a wall admins cannot cross | **built (L0)** | large | high: it makes the system multi-company |
| ACC-11 claiming clients (unclaimed ones are visible to admins only) | ACC-10, and the claiming rule | small | high: a wrong rule lets one company claim another's client |
| ACC-12 admins belong to a company | ACC-10, and who sits above the companies | medium | high |
| ACC-13 directors seeing several branches | ACC-10 | medium | medium: not needed for the first release if the model allows it |
| ACC-14 migration: the current company, with every existing person and client in it | ACC-10 | small | medium: it is what keeps today's setup working |
| UX-06 alternating row colours in every table (done) | nothing | small | low |
| UX-07 straight edge on the sign-up page | nothing | small | low |

### Order
1. **Wave 0, small and independent, any time:** UX-05 and REPORT-08, now joined by UX-06 and UX-07.
2. **Wave 1, Track K, the Overview:** OVER-04 (backend) first, then OVER-02 and OVER-03 (the page), then OVER-05 (the "data as of" line, if you want it).
3. **Wave 2, Track L, access, now in five steps. L0 comes first: settle the open company questions above and put companies into the model (ACC-10, ACC-11, ACC-12, ACC-14).** Then **L1** roles, several roles, converting old grants, and "All clients" (ACC-02, ACC-05, ACC-08). **L2** rights and the All-clients rule (ACC-04, ACC-06) and the users API (ACC-03). **L3** apply the rights to every endpoint and add the route-and-permission test (ACC-07). **L4** the Roles page, the Users page and rights-based navigation (ACC-09).

**Companies (ACC-10) come before the first access step.** A role now belongs to a company and holds a subset of that company's clients, and an administrator belongs to one company. Settling this first avoids building roles twice. The decisions and what is still open are listed below.

**Why Overview before access.** The Overview is lower risk and you can review its layout straight away. Access is security-sensitive and slower to review. OVER-04 also changes how a requested client is checked against a person's limit, and access is built on that check, so building it first means it is changed once.

### Progress
- **Wave 0 and Wave 1 built together** (11 Nov 2026): UX-05, REPORT-08, OVER-04, OVER-02 and OVER-03. OVER-05 (a "data as of" line showing how old a page's data is) waits for your answer. **Wave 2 started and stopped: step L0 (companies) is built and switched off; the rest of it (L1 onwards) is parked until other people are added.**

### Companies: decided and built (ACC-10 to ACC-14), and what is still open
**Decided (11 Nov 2026)**
- A company is whoever the person works for. This company's branches are separate companies; other companies may use the system later. A person belongs to **one** company for now.
- A company has clients; the person sees a **subset** of them (through roles, still to come; for now, the clients their administrator gives them).
- **Company is a hard wall.** An administrator does not see across it: an admin of company A never sees company B's data.
- A client is visible only to administrators until it is **claimed** under a company.
- **A platform owner sits above the companies** (you, for now): the only one who makes companies, moves people between them, and reads the raw collector logs.
- **Company administrators can run the data collectors.**

**Built in L0**: companies, the wall, the owner, claiming, the migration, the `company` command, the Access page and Clients page changes, and the tests. Everything is switched off until `DJANGO_CLIENT_ACCESS_ENFORCED=1`.

**Decisions I took, to be overruled if wrong**
- Who may claim a client: an administrator, for their own company, while it is unclaimed; the owner can assign or take back.
- The raw collector log is the owner's; administrators see status and can run collectors.
- The platform owner sees every company's data.
- Unlinked data (a customer, device or ticket linked to no client) is visible to administrators of every company, because it belongs to nobody yet and someone has to link it. This assumes one set of source databases.

**Still open**
- **Directors seeing several branches** (ACC-13): a parent company with branches below it (proposed), or an explicit list.
- **Shared weights and report templates per company (ACC-15)**, before an unrelated organisation uses the system.
- **Separate data sources per company (FUT-01):** today every company would read the same source databases; an unrelated company would need its own.

**What is now company-scoped:** clients, the clients list, client resolution, the Access page, every data section, the trend. **Not yet:** shared weights, report templates (ACC-15).

### What can run in parallel
- **Track K and Track L are independent.** K works in the patch queries, `Patches.jsx` and the tickets chart; L works in the access model, the rights, the users and roles pages. They meet at one place, `access.allowed_client_ids` (its answer stays "no limit, or a set of clients"): K only widens how a *request* is checked against it; L only changes how it is *worked out*. With two people they can run side by side.
- **Wave 0 can be done alongside either track.**
- **Inside Track L, only L1 and REPORT-08 can start immediately.** L2 waits for L1, L3 for L2, and L4's screens can be built against the L1 and L2 endpoints as soon as those exist.
- Each track updates its own parts of the docs (08, 04, 05, 03, 06, 07 and the ER diagrams); merge those last.
- **One person working alone** takes Wave 0, then K, then L1 to L4.

### What only you can do, alongside all of it
- Run `python manage.py snapshot_scores --backfill` (writes only our own table) so the trend has history.
- Check the N-central connection (`Test-NetConnection ncentral.ipdimension.net -Port 443`), and say whether you want OVER-05 (the "data as of" line, so a page can show how old its data is).
- Autotask: fix the 401 and 500 so tickets load, then enable the job.
- Fill in the `EMAIL_*` settings, which ACC-03 needs to email set-password links.
- Pending decisions and approvals: NC-01 (whether a patch run with only device-not-found warnings is healthy), DATA-01 and TICK-02 (changing the live schemas). SCORE-02, what "healthy" means, you have deferred.

### How each item is finished
Pure tests, database tests written, the documentation and its checks updated, the checklist extended; every access change ships switched off until you turn it on, as access limiting does now.

## Stage 2 (needs Stage 1): built, waiting on you
- **All-client overview (OVER-01): done.** The Patches page is the Overview, with every client's own scores and the client heading as the filter.
- **Client-scoped access (ACC-01): done, off by default.** Give people their clients on the Access page, check with a test account, then set `DJANGO_CLIENT_ACCESS_ENFORCED=1`. See `docs/05_OPERATIONS.md`, "Turning on client access".
- **Real-data verification: the tool is built** (`python manage.py verify_data`). Running it is yours: it is how the sections meet your real databases for the first time. Send me the output.

## Stage 3 (release gates)
Confirm deployed version; production build; unattended scheduler run; backup/restore drill; named owners; N-central 404 policy.
