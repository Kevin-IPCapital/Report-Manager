# 7. Environment verification and signoff checklist

Run in approved **development or test** environments first. Do not run source imports without upstream API permission. Do not put secrets in screenshots or reports.

## A. Code and version provenance
- [ ] The deployed code matches the latest archive; note local edits.
- [ ] `python manage.py makemigrations --check` reports no changes, and `migrate` has been run (migration `0003` is hand-written).
- [ ] The N-central, Autotask, SentinelOne and Cove folders contain the intended collector versions.
- [ ] `backend/collector_jobs.json` exists (copy it from `collector_jobs.example.json` on a new install) and shows the intended `enabled` and `schedule_enabled` values.
- [ ] `python manage.py dbcheck` shows the expected user, database and schema, with `can_create_tables=True` where migrations run.

## B. Authentication and access
- [ ] `DJANGO_DEBUG=0` and a real `DJANGO_SECRET_KEY` in production; `python manage.py check --deploy` is clean.
- [ ] `DJANGO_DEV_LOGIN_BYPASS` and `DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL` are unset or `0` wherever real users exist.
- [ ] A wrong password returns 401; the sixth wrong attempt returns 429 (run `python manage.py test api.tests`).
- [ ] Registration is closed (or intentionally open); a weak password is refused.
- [ ] A non-staff user gets 403 on `/api/collectors/*`, `/api/clients/resolution`, `/api/clients/resolve` and client edits.
- [ ] Per-client access policy decided before exposing data to external clients (ACC-01).
- [ ] Credentials that were in shared archives have been rotated.

## C. Data correctness
- [ ] Latest captures and success flags in `autotask.snapshot`, `ncentral.patch_run`, `sentinelone.snapshot`, `cove.snapshot`.
- [ ] Every column used by `api/data/` exists and the role can SELECT.
- [ ] A sample ticket and patch count agree with the source reports.
- [ ] A device whose inventory failed (404) is not counted as compliant.
- [ ] `ncentral.device.org_unit_id` holds the customer org unit on live rows (the collector code writes it that way; the Patches client filter relies on it).
- [ ] The patch KPI definition is agreed (entries installed, versus devices with no missing security patch) and the report figures use it (SCORE-02).
- [ ] The Patches client list matches the customers you expect; linked customers appear under the client's name.
- [ ] Snapshots exist after a patch collection (or `snapshot_scores --backfill`); the trend shows real points or an empty chart, never invented ones.
- [ ] Timestamps agree with explicit time-zone conventions.
- [ ] PDF counts and charts agree with the API payloads.

## D. Clients
- [ ] Client Resolution lists every unlinked customer; accepted groups are ones you agree are the same company.
- [ ] Names that differ by more than punctuation and case (for example "Ltd") are linked by hand or by alias.
- [ ] Discrepancies shown (client missing from a source, link to a missing customer) are understood.

## E. Scheduler and alerts
- [ ] `python manage.py run_collection ncentral_inventory` works for a controlled customer.
- [ ] Either `run_scheduler` or the two scheduled tasks run without a terminal; the Data Collection page shows a recent "Worker last ran".
- [ ] Queue moves QUEUED, RUNNING, SUCCESS/PARTIAL/FAILED; a second enqueue returns 409.
- [ ] A long collection does not raise a "worker silent" alert.
- [ ] `python manage.py collection_alerts --test-email` reaches the recipients; a stopped worker produces an email within the heartbeat limit.
- [ ] Autotask stays disabled while 401/500 are unresolved; healthy jobs continue.
- [ ] Each job has an agreed request budget and retention.
- [ ] Next-run times survive a browser refresh and a reboot.
- [ ] Cove: `schema.sql` applied, `cove.customers` filled, one manual run succeeds before it is enabled.

## Server Health and Data Collection
- [ ] Server Health shows the tiles; clicking Servers, Infected and so on filters the cards, and the bubble removes the filter. Connected and Disconnected cannot both be on.
- [ ] Typing part of a device name on Server Health and on Backups narrows the list; several words must all appear.
- [ ] With more than 12 endpoints the cards are paged; each card says "No data" for CPU, RAM and disk.
- [ ] A job that last worked days ago and failed today shows "Stale" with the last good run's time and the number of failures since; it does not show Unknown, and a fresh-looking database timestamp does not hide it.

## Order and remembered inputs
- [ ] Tickets: late or flagged tickets are at the top, then open ones with the oldest first. Server Health: infected, then disconnected, then the oldest data. Patches categories: lowest compliance first. Backups: critical first.
- [ ] Click a column heading's sort on a table, reload the page: the sort is still there. "Reset Sort" returns to the starting order.
- [ ] Change the date range and press Refresh, sign out and in again: the same length of range is back (a "last 30 days" range is the last 30 days from today, not the old dates).
- [ ] Set a ticket filter and a device filter, leave the page and come back: they are still on, shown as bubbles that remove them.
- [ ] Sign in as a different user on the same browser: they do not see the first person's filters or range.
- [ ] Settings, Remembered Inputs, Forget: pages start from their defaults on the next load.

> **What has and has not been looked at.** On 12 Nov I checked the pages below in headless Chromium, using the real backend's output for test data (VERIFY-02 in `06_STATUS_AND_BACKLOG.md`): Server Health with its three sections, Expand All and Collapse All, the Disconnected badge, the Overview with one client chosen and the working for its score, the Data Collection page's Partial badge, saved date ranges (add, appear on the Client Tickets date control, survive a reload, remove), the keyboard, and the dark theme. That found and fixed four layout defects. It was **not** your live data, your browser or the Vite build, so the boxes below are still yours to tick.

## Access: client groups and new people (12 Nov)
The endpoints behind these passed their database tests on your machine on 12 Nov (`python manage.py migrate` adds client groups).
- [ ] Access has three tabs in this order: **Overview**, **Employees**, **Client Groups**, and it opens on the one you used last. Arrow keys, Home and End move between the tabs.
- [ ] **Overview** starts with the heading "Overview" and, under it, the subheading "People And What They Can See" over the people table (the first thing on the page). The table has an **Access** column: for the platform owner it is a dropdown (Employee or Admin) that changes at once; for an ordinary admin it is plain text; the owner's row and your own row have no dropdown. Companies (platform owner only) comes after the table.
- [ ] **Employees** has **New Employee** and **Employee Accounts** (email, access, status, New Link, Deactivate or Reactivate).
- [ ] Make a client group for a company with two clients: only that company's clients are offered. A second group with the same name (any case) is refused. An "All clients" group hides the client picker. Edit renames it; Delete asks first.
- [ ] Make a person with a username, email and a client group. A notice says what happened to the email and shows a link; with the default email backend it says no email was sent and to give them the link. Open the link: it is the choose-a-password page, and after choosing one the person can sign in. Using the link a second time is refused.
- [ ] A person with a bad username or email is stopped at once with a reason. A username that is taken (any case) is refused.
- [ ] In the Overview table, add a group to someone and Save; it is kept after a reload. Save is greyed until something changed. Deactivate dims the row and the person cannot sign in; Reactivate brings them back; New Link makes a fresh link. There is no Deactivate button on your own row.
- [ ] As an administrator (not the platform owner): no Companies panel, no company dropdowns, no Access dropdowns or Access choice when making an employee (a new employee is an Employee), nobody from another company, and everything you make is in your own company.
- [ ] With the limits on (`DJANGO_CLIENT_ACCESS_ENFORCED=1`), a person sees exactly the clients of their groups plus any given one by one, and never another company's.

## Backups and Clients (12 Nov)
- [ ] Backups has six tiles (Devices, Warning, Critical, Red Flags, Used Storage, Servers), three across and then three: no OK and no Microsoft 365 tile.
- [ ] Clicking **Red Flags** keeps only the devices with at least one red flag and adds a "Red Flags: Has red flags" bubble; clicking it again, or removing the bubble, shows everything. The **Clients** and **Devices** filters work as on Server Health.
- [ ] Order: critical, then warning, then OK; within each the longest since a good backup. Two devices that are the same on both now have the one with **more red flags first**. Check with two devices in the same health and with the same days.
- [ ] Clients: the **Linked Customers** column shows names only (no numbers). A customer in two systems with the same name appears once. A link whose name could not be read says, for example, "Cove: name not available".

## Settings and layout (12 Nov)
- [ ] Sizes adapt: make the browser window narrower and wider (down to a phone's width). The stat tiles go four across, then two, then one; the charts go two across, then one; the sidebar becomes a bar along the top on a narrow window; and nothing makes the page scroll sideways except a wide table inside its own box.
- [ ] Text size: set the browser's text size to Large or Very Large (or zoom to 150%). The page grows with it and the layout changes earlier (fewer columns across) instead of being squeezed. Check Server Health, Backups and Access.
- [ ] Phone width (about 360px): Server Health cards, the Access forms and the Data Collection table fit, and a long word (the setting name on the Access page) wraps.
- [ ] Settings: Theme and Accent Colour are dropdowns; the accent dot follows the choice; both are remembered after a reload.
- [ ] On a wide window (1920px or more), the "data as of" line on Server Health, Backups, Client Tickets and Technician Tickets starts at the same left edge as the page heading, in both themes and with the sidebar open or collapsed.

## Server patching and saved date ranges (12 Nov)
- [ ] Server Health has five tiles (Servers, SentinelOne Disconnected, N-central Disconnected, Infected, Out Of Date), three across and then two: there are no Endpoints, SentinelOne Connected or N-central Connected tiles. Above the cards are a **Clients** filter and a **Devices** filter: choose several of each and only those clients' and devices' servers remain; nothing chosen shows everything; both are still set after a reload, and they combine with the tiles. Each card shows **SentinelOne Status** and **N-central Status**, which can differ: a server can be connected to SentinelOne and disconnected from N-central. Clicking N-central Disconnected shows only the servers N-central cannot find; the choice is remembered after a reload. A server with no matching N-central device shows N-central Status "Unknown" (hover for the reason) and is not counted in the N-central tile.
- [ ] Server Health: each card has three headings, **Performance**, **Patches** and **Data Collected**, closed at first. Clicking one opens just that section. **Expand All** and **Collapse All** above the cards open and close them all, on every page of cards.
- [ ] Performance shows CPU, RAM and Disk, each "No data" for now.
- [ ] Patches is a **table**: a row for each of Windows Security Patching, Windows Patching and Third Party Patching, with the columns Last Added (a date and how many days ago), Next Due ("Not collected") and Up To Date (a tick).
- [ ] Data Collected is a table with a row each for SentinelOne, N-central (device inventory), N-central (patch inventory) and Cove (backup), each with the latest date found for that server, or a short reason in words (for example "No matching device in this client"). Check the SentinelOne date against the card's Data Age, and a Cove date against the Backups page.
- [ ] A server whose device N-central cannot find (the 404 devices from the partial patch run) shows a red **Disconnected** badge on its Patches heading, visible even when closed, and a note above the table with the date of its last successful inventory. Its Data Collected row for N-central's patch inventory shows that same last successful date, not today's failed attempt. The Data Collection page still says **Partial** for that run; hovering the badge gives the number of devices.
- [ ] The tick is ticked when nothing is missing in that category, empty when something is, and a dash when N-central lists no patches of that kind for the server. A category with something missing for more than 14 days also shows a red "⚠ Overdue"; a more recent one shows an amber "N missing". Hover for the numbers. Check a few servers against N-central.
- [ ] A server with no N-central device of the same name in its client (or with two) says so on the card instead of showing patch data. Compare a few cards against N-central for the same server.
- [ ] Settings, Saved Date Ranges: add "Board pack", last 3 months. It appears as a button on the date range at the top, in a report's Generate panel, in the Overview trend's range box, and under "Use A Saved Range" when a report's default period is "The last…". Pressing it fills the dates for today.
- [ ] Remove a saved range and it disappears from all four places. "Forget Remembered Inputs" does not remove your saved ranges.

## How the score is calculated, and the `imported_at` column (12 Nov)
- [ ] Overview, choose one client, press Score Weights: "How is the score calculated?" explains the weights in six short steps, and under it **The working for <client>** shows each type's categories with compliance, weight and share, the sum and the divisor, and the result. The type results and the overall result match the figures on the Overall Score card. Change a weight, save, and the working updates.
- [ ] With no client, or several, chosen, the working is replaced by a line asking you to choose a single client.
- [ ] A category with weight 0, or that lists no patches, is greyed with the reason, and the other categories' shares add up to 100%.
- [ ] After applying the `imported_at` migration, `python manage.py verify_data` shows `imported_at` as OK. Before it, EMPTY. Run a collector afterwards and check in the database that its new rows have an `imported_at` time and the old rows are empty.

## "Data as of" on every page (11 Nov)
- [ ] The Overview shows a line in its Overall Score card, and Server Health, Backups, Client Tickets and Technician Tickets show one under their heading, each naming its source ("Patch data", "Server data", "Backup data", "Ticket data").
- [ ] A source that has been collected recently reads "as of <date and time> (<how long> ago)" with a green dot; one that is overdue reads "Stale", says how many collections have failed since, and is red (the Overview should say this now, while N-central is failing).
- [ ] A source that has never been collected reads "no data collected yet", and says "(the collector is switched off)" if it is (Client Tickets, until Autotask works).
- [ ] Hovering the line shows where the time comes from. As a person who is not an administrator, the line still appears, but the Data Collection page does not.

## Small fixes and graphs (11 Nov)
- [ ] Overview, Scores By Client: no combined group at the top; with no client chosen every client is listed; choosing clients narrows it to them.
- [ ] Overview, Show Trend: the trend sits in a card with a minimise button (the – at the right of its title) and it stays minimised until you open it.
- [ ] Every table has alternating row shading in both the light and the dark theme, and a flagged (red), warning or selected row still shows its own colour; hovering a clickable row still highlights it.
- [ ] The sign-in and sign-up screens have a straight vertical edge between the blue and white sections, with no jagged diagonal.
- [ ] On Client Tickets, Backups and the Overview, every graph has a minimise button.

## Companies (11 Nov)
- [ ] After `migrate`: `python manage.py company list` shows one company holding all your people and clients, and the Clients page shows its name against every client.
- [ ] `python manage.py company owner <you>`, then the Access page shows a Companies panel and a company box for each person.
- [ ] Make a second company, put a test administrator and a test member in it (not yet enforcing), and give the company one client with `manage.py company claim`.
- [ ] Turn enforcement on in a test setup: the test administrator sees only their company's clients on every page (tickets, servers, backups, the Overview, the trend, the Clients page), never yours; you still see everything.
- [ ] A client with no company shows under Unclaimed Clients to administrators only; the test administrator claims it and it disappears from your other company's administrators.
- [ ] The test administrator can run a collector and sees its status, but opening a raw log is refused; you can open it.
- [ ] Creating a client with a name or link that another company's client already has gives an error that says "another client", not its name.
- [ ] A member of the test company sees only the clients you gave them on the Access page, and a note on every page says so.

## Overview, tickets chart and reports (11 Nov)
- [ ] Overview: the Overall Score card is first with the Clients card beside it; the trend (Show Trend) is directly below; no Client dropdown and no separate score table.
- [ ] Click two client names in the Clients card: both are highlighted, two bubbles appear, Scores By Client narrows to them, and the Clients card still lists everyone. Remove one bubble: only that client goes.
- [ ] Click a client's name in Scores By Client: the filter becomes that client alone. Click a measure under a client: that client is chosen and its categories appear below; click it again to close them.
- [ ] With no client chosen, or with two chosen, the Overall Score card says "Please select a single client" and, after Show Trend, so does the trend. Choose exactly one client: the card shows that client's name and score, and the trend is that client's. Reload the page: the chosen clients are remembered.
- [ ] Client Tickets: the "Tickets Created Per Day/Week" chart has a minimise button and obeys Collapse All and Expand All.
- [ ] Reports: My Reports and Templates are separate lists. As a non-admin you cannot change a template made by someone else, but you can Generate it with your own dates and "Copy To My Reports". As an admin you also see New Template.

## Stage 2: overview, access and verification
- [ ] `python manage.py verify_data` runs; every section is OK or EMPTY with a reason, and any FAILED is sent on (use `--verbose`).
- [ ] The Overview shows each client with its own scores; clicking a client's heading limits the rest of the page to it, and clicking it again returns to everyone.
- [ ] Access page: give a test (non-staff) account one client. With the limits still OFF it still sees every client.
- [ ] Set `DJANGO_CLIENT_ACCESS_ENFORCED=1` and restart: the test account sees only that client on every page (tickets, servers, backups, the Overview, the trend), cannot open another client's device, and gets a refusal, not an empty page, for another client in the address; staff still see everything.
- [ ] The test account sees a note that it is limited. An account with no clients sees no data and an empty client list.
- [ ] Set it back to `0` and restart: everyone sees every client again and the grants are still on the Access page.

## Filters, ranges and gaps
- [ ] In a report section, add two customers to the same filter: both appear as bubbles, clicking one removes it, and the report keeps the tickets of either customer.
- [ ] A report whose default period is "the last 3 months" generates with dates three months back from today; changing the dates in the Generate panel still works.
- [ ] The date range at the top has quick buttons; the Patches trend can be limited to the last 3, 6 or 12 months and the choice is still there next visit.
- [ ] Both ticket pages show tickets created per day (or per week for a long period) under the charts.
- [ ] On Patches, typing part of a device name in the patch list narrows it.
- [ ] Client Resolution shows "since …" beside gaps, and what was resolved recently after you link a customer.
- [ ] `python manage.py client_master_view --sql` prints the SQL; run it in a test database first, then `python manage.py client_master_view`, and join to a source with `= ANY (m.<column>)`. (The suite's PostgreSQL test does this for you.)

## Reports
- [ ] Create a report with two ticket sections for different customers; generate it: both sections appear, with the right counts.
- [ ] The contents page lists each section on the page where its heading is.
- [ ] Ticking the annex option adds the detail tables at the back, each on its own page; unticked, there are no tables.
- [ ] Generating with other dates changes the ticket period on the cover without editing the report.
- [ ] A report with its own patch weights gives the same overall score to two different users.
- [ ] A private report is invisible to other users; a shared one can be generated by them but not changed.

## F. Quality and documentation
- [ ] `python manage.py check`, `python manage.py test` (isolated writable test database), `npm test` and `npm run build` pass.
- [ ] Report PDF renders; section order matches the selection.
- [ ] The business owner confirms each KPI formula from the June 2026 reports.
- [ ] `python docs/tools/generate_er.py --check` reports the ER diagrams up to date.
- [ ] `python docs/tools/generate_fixtures.py --check` (needs Django; written but not run by its author) reports the report test fixtures up to date, so the report code still reads what the API returns.
- [ ] Named owners and escalation contacts are filled into [`01_SYSTEM_OVERVIEW.md`](01_SYSTEM_OVERVIEW.md).
- [ ] A backup and restore drill and a rollback procedure are recorded.

## Evidence signoff

| Gate | Owner name | Date | Evidence | Status |
|---|---|---|---|---|
| Source API permission and load budget | TBD | | | Pending |
| Authentication and security | TBD | | | Pending |
| Database integrity and grants | TBD | | | Pending |
| Scheduler, worker and alerts | TBD | | | Pending |
| Client mapping confirmed | TBD | | | Pending |
| Dashboard accuracy | TBD | | | Pending |
| PDF parity and templates | TBD | | | Pending |
| Production go-live | TBD | | | Pending |
