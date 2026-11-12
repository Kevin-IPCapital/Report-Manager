# Front-end guide

The front end is the part you see in the browser. This guide explains how it is put together, where everything is, and how to change it. The backend is in [`03_CODEBASE_AND_API.md`](03_CODEBASE_AND_API.md) and [`08_API_REFERENCE.md`](08_API_REFERENCE.md); the collectors are in [`11_COLLECTORS.md`](11_COLLECTORS.md).

> **Status, said plainly.** The pure logic (filters, ordering, dates, storage, report building) is tested with Node. The **pages and components themselves have never been run in a browser by the person who wrote them**: they have only been checked for balanced markup and for names that resolve. Expect to find small visual or behavioural faults when you first use a new page, and report them. `docs/07_VERIFICATION_CHECKLIST.md` lists what to try.

## What it is

- **Preact** (a small React-like library) with **preact-iso** for routing, built by **Vite**. There is no state library and no CSS framework: shared state is a few Preact contexts, and all the styling is one file, `src/styles.css`.
- It talks to the server **only** through `/api`, and only through one file, `src/api/adapter.js`.
- It builds to plain static files, which the web server serves next to the API.

## Run it, build it, test it

From the `frontend` folder:

| Command | What it does |
|---|---|
| `npm install` | Fetches the two libraries and the build tools (once) |
| `npm run dev` | Starts the development server with live reload. `/api` is forwarded to the backend at `API_PROXY_TARGET` from `frontend/.env`, or `http://localhost:8000` if that is not set |
| `npm run build` | Makes the production files in `dist/` |
| `npm test` | Runs `node --test`: every `*.test.js` file under `src` |
| `npm run check` | A real parse of every source file, to find what only shows up when the page is built: a syntax error, a name declared twice, a name used but never declared. Needs TypeScript, once: `npm install --save-dev typescript` |

**What the tests cover:** the pure modules and the report engine (see "Shared logic" below), the shared report-validation cases that the server also runs, a guard that every graph can be minimised (`charts.test.js`), and a guard that no name is declared twice at the top of a file or of a function (`declarations.test.js`: a build error that nothing else would see, because the tests never load the pages), and a guard on the layout causes that were only found by looking at the pages in a real browser (`styles.test.js`: it reads the stylesheet, so it cannot prove how a page looks, but it stops those specific causes from being removed by accident). **What they do not cover:** anything that needs a browser: the pages, the components, the styling. The guard test `src/charts.test.js` also reads the page source, so it must be run from a normal checkout.

## How the app starts

`main.jsx` draws `App` into the page and loads `styles.css`. `App.jsx` wraps everything in these providers, outermost first:

1. `SettingsProvider`: theme, accent colour, priority colours, corners, shadows, sidebar state (kept in the browser, applied to the page).
2. `LoadingProvider`: counts requests in flight, for the loading overlay.
3. `AuthProvider`: the signed-in person.
4. `LocationProvider`: the address (preact-iso).
5. `Shell`: if nobody is signed in it shows the sign-in pages (`/register`, `/forgot`, `/reset`, otherwise `Login`); if someone is, it shows the `Layout` with the routes.
6. Inside `Shell`, for a signed-in person: `DateRangeProvider`, **keyed by username**, so the date range is remembered separately for each person and starts afresh for whoever signs in.

**Pages** are listed in `routes.js` as `{ group, path, title, component, staffOnly? }`. **Adding a page is adding a line.** `group` is the heading in the sidebar (`Reports`, `Manage`, `Preferences`); `staffOnly` hides the page and its route from anyone who is not an administrator (the server enforces the real permission as well).

## Talking to the server

`src/api/adapter.js` is the only file that knows how data is fetched.

- **Tokens:** the short-lived access token is kept **in memory only**; the long-lived refresh token is an httpOnly cookie the browser manages. Every request carries `X-Requested-With: fetch`, which the server requires on cookie-using endpoints.
- **Expiry:** a `401` triggers one refresh and one retry; parallel `401`s share one refresh (and a Web Lock keeps tabs from racing). If the refresh fails the session is over and `onSessionExpired` signs the person out.
- **Errors:** a failed call throws an `Error` carrying the server's message (`AuthError` for `401`), which pages show with `ErrorNote`.

Two hooks wrap the calls for pages:

- `useApi(call, deps)` runs a call, tracks loading and error, and drives the loading overlay. The previous data stays in place while it reloads, so the page blurs instead of going blank.
- `useSection(name)` fetches a report section for the **applied** date range, and refetches only when the range is applied or Refresh is pressed, never because a filter changed.

### Every call

| Call | Server endpoint |
|---|---|
| `api.restoreSession` | `POST /api/auth/refresh` |
| `api.login`, `api.logout`, `api.register`, `api.forgotPassword`, `api.resetPassword` | `/api/auth/login`, `/logout`, `/register`, `/forgot`, `/reset` |
| `api.getSection` | `GET /api/sections/{name}` with the parameters the section takes |
| `api.getFreshness` | `GET /api/freshness` (when each page's data was collected; open to everyone signed in) |
| `api.getCollectionHealth`, `api.getCollectionExecution`, `api.startCollection`, `api.getCollectorLatestLog` | `/api/collectors/health`, `/execution`, `/run`, `/{job}/latest-log` |
| `api.getClients`, `api.saveClient`, `api.claimClient` | `/api/clients`, `/api/clients/{id}`, `/api/clients/{id}/claim` |
| `api.getResolution`, `api.resolveClients` | `/api/clients/resolution`, `/api/clients/resolve` |
| `api.getAccess`, `api.saveAccess` | `/api/access`, `/api/access/{username}` |
| `api.saveClientGroup`, `api.deleteClientGroup` | `/api/client-groups`, `/api/client-groups/{id}` |
| `api.createPerson`, `api.updatePerson`, `api.newSetupLink` | `/api/users`, `/api/users/{username}`, `/api/users/{username}/link` |
| `api.getCompanies`, `api.createCompany`, `api.setUserCompany` | `/api/companies`, `/api/users/{username}/company` |
| `api.getReports`, `api.saveReport`, `api.deleteReport` | `/api/reports`, `/api/reports/{id}` |
| `api.getWeights`, `api.saveWeights` | `/api/weights` |
| `api.onSessionExpired` | (registers the sign-out callback; no request) |

## What is remembered, and where

**Settings** (Settings page) are kept in the browser's `localStorage` under fixed names and applied to the page by `SettingsProvider`:

| Key | What |
|---|---|
| `ops_theme` | `system`, `light` or `dark` |
| `ops_accent` | the accent colour |
| `ops_priority_colours` | the colours of ticket priority rows |
| `ops_nav_collapsed` | the sidebar open or closed |
| `ops_square` | square corners |
| `ops_shadows` | card shadows |

**Remembered inputs** (dates, filters, sorting, page sizes) are different: they are kept **per signed-in person**, **checked on every read**, and can be forgotten from Settings. All of it goes through `storage.js` (entries are `rm:v1:<username>:<name>`), the hook `usePersistentState(name, initial, validate)` in `state/persist.js`, and the checks in `persisted.js`. Only a change made by the person is saved, so defaults are never frozen in. To keep these on the server later, replace `storage.js`; nothing else knows where they live.

| Name | What it remembers | Where |
|---|---|---|
| `range` | The date range last applied (a range ending today as its length) | `DateRangeContext` |
| `report:range` | The dates last used to generate a report | `Reports.jsx` |
| `patches:clients` | The clients chosen on the Overview | `Patches.jsx` |
| `patches:device` | The device filter on the patch list | `Patches.jsx` |
| `patches:trend-range` | The trend's range: all history, the last 3, 6 or 12 months, or one of your saved ranges (`saved:<name>`) | `Patches.jsx` |
| `ranges:saved` | The date ranges you named and saved (a length such as "last 3 months"; never cleared by forgetting remembered inputs) | `state/SavedRangesContext.jsx` |
| `servers:filters`, `servers:device`, `servers:sort`, `servers:size` | Server Health tile filters, device filter, sort and card size | `ServerHealth.jsx` |
| `backups:filters`, `backups:device` | Backups filters | `Backups.jsx` |
| `tickets:filters`, `technicians:filters` | The ticket filters on each ticket page (`<persistKey>:filters`) | `TicketExplorer.jsx` |
| `table:<name>:sort`, `table:<name>:size` | A table's sort and page size, for `tickets`, `technicians`, `backups`, `patch-categories` and `patch-entries` | `DataTable` |

## The pages

| Page | Route | Gets its data from | What it does |
|---|---|---|---|
| **Overview** (`Patches.jsx`) | `/` | sections `patches`, `patch-overview`, `patch-trend`, `patch-entries`; `api.getWeights` and `api.saveWeights` | The Overall Score card **for one client** (with none or several chosen it says "Please select a single client" instead of a combined figure) and a Clients card (choose clients; each becomes a removable bubble), the trend (also one client at a time, with the same prompt), Scores By Client (a group per client, every client or the chosen ones), the score weights panel (with "How is the score calculated?" and, for one client, the working), and the drill-down: a measure, then its categories, then its patches (with a device filter) |
| **Server Health** | `/server-health` | section `server-health` | Eight tiles, six of which filter when clicked (Endpoints, then Servers, SentinelOne Connected, SentinelOne Disconnected, N-central Connected, N-central Disconnected, Infected, Out Of Date), a device filter, a sort box, and paged cards; each card shows two connection statuses, **SentinelOne Status** and **N-central Status**, and has three sections that open on their own headings (**Expand All** and **Collapse All** above the cards set every section at once): **Performance** (CPU, RAM and disk: "No data" until a source exists), **Patches** (a table with a row for each of the three Overview categories and the columns Last Added, Next Due ("Not collected" for now) and an Up To Date tick, with a flag where something has been missing for more than 14 days; a device N-central cannot find shows a **Disconnected** badge on the heading, even when collapsed, and a note with the date of its last successful inventory) and **Data Collected** (a table: the latest date found for the device on a successful run of SentinelOne, N-central's device and patch inventories, and Cove's backup). No overall "healthy" is shown: that is not defined yet |
| **Backups** | `/backups` | sections `backups`, `backup-detail` | Backup health tiles, charts, a filterable table, and a detail card for a device |
| **Client Tickets** and **Technician Tickets** | `/tickets`, `/technicians` | section `tickets` | Both are `TicketExplorer`: tiles, charts and the ticket volume trend, filters, and a ticket table with a detail card |
| **Reports** | `/reports` | `api.getReports`, `saveReport`, `deleteReport`, `getWeights`, and `getSection` (through `report/fetch.js`) | My Reports and Templates, the editor, and generating a PDF for chosen dates |
| **Data Collection** (staff) | `/collection-health` | `api.getCollectionHealth`, `getCollectionExecution`, `startCollection`, `getCollectorLatestLog` | Each collector's last run and freshness, buttons to run one, and its latest log (polls the queue every 2.5 seconds) |
| **Clients** (staff) | `/clients` | `api.getClients`, `saveClient`, `claimClient` | The master clients, their links and aliases, and Unclaimed Clients to claim |
| **Client Resolution** (staff) | `/clients/resolution` | `api.getResolution`, `resolveClients` | Group and confirm unlinked customers, discrepancies with how long each has lasted, what was resolved recently |
| **Access** (staff) | `/access` | `api.getAccess`, `saveAccess`, `getCompanies`, `createCompany`, `setUserCompany`, `saveClientGroup`, `deleteClientGroup`, `createPerson`, `updatePerson`, `newSetupLink` | Four panels. **Companies** (platform owner). **Client Groups**: a named list of clients, or all of the company's, made, edited and deleted (a delete asks first). **New Person**: username, names, email, client groups (and, for the owner, a company and an Administrator tick); no password is chosen, and when the person is made a notice says what happened to the email and shows the one-time link with Copy and Dismiss. **People And What They Can See**: each person's company, client groups and clients given one by one, with Save, New Link, and Deactivate or Reactivate (never for yourself); a deactivated row is dimmed with a badge |
| **Settings** | `/settings` | none (the browser) | Theme, accent, priority colours, layout, your saved date ranges (add and remove), and forgetting remembered inputs |
| Sign-in pages | `/`, `/register`, `/forgot`, `/reset` | `api.login` and the others | Shown only while signed out; `Login`, `Register`, `ForgotPassword`, `ResetPassword`, built on `AuthCard` |

## Components

| Component | File | What it is |
|---|---|---|
| `Layout` | `components/Layout.jsx` | The sidebar, the header and the page frame; shows the note for a person limited to some clients |
| `PageHeader` | `components/PageHeader.jsx` | The page title (with a trail of what is drilled into) and the date range |
| `DateRange`, `Period` | `components/DateRange.jsx`, `Period.jsx` | The date range control (slider, boxes, quick buttons, Refresh) and a line saying which period is shown |
| `DataTable` | `components/ui.jsx` | The table: per-column search, sort, paging, **Reset Sort**, optional `persistKey` to remember sort and page size, and a row click |
| `StatCard`, `StatusBadge`, `FilterBubbles`, `ErrorNote`, `SectionBody` | `components/ui.jsx` | A tile that can act as a filter, a coloured status, removable filter bubbles, an error line, and the body of a section |
| `Pager` | `components/Pager.jsx` | Page controls and page size |
| `ChartCard`, `ChartControls` | `components/ChartCard.jsx` | **A card with a minimise button around every graph**, and Collapse All / Expand All for a group |
| `BarChart`, `Donut`, `TrendChart` | `components/BarChart.jsx`, `Donut.jsx`, `TrendChart.jsx` | The three kinds of graph, drawn as SVG with no library; the first two wrap themselves in a `ChartCard` |
| `TicketExplorer` | `components/TicketExplorer.jsx` | Everything on a ticket page; the two ticket pages are one line each |
| `DetailModal` | `components/DetailModal.jsx` | The detail card used for a ticket, a device, a patch |
| `MultiFilter`, `ChoiceFilter`, `DeviceFilter` | `components/MultiFilter.jsx`, `DataFilter.jsx`, `DeviceFilter.jsx` | A filter holding several values as removable bubbles (with a limit you can set; with `restrict` only the listed options can be chosen, as for a person's client groups), a single-choice filter, and the device-name search |
| `WeightFields` | `components/WeightFields.jsx` | The score-weight inputs, shared by the Overview and the report editor |
| `ScoreWorking` | `components/ScoreWorking.jsx` | Under Score Weights on the Overview: how the weights make the score, in plain steps, and, when one client is chosen, the working for that client's scores with its real numbers |
| `DataAsOf` | `components/DataAsOf.jsx` | The line under a page's heading saying when its data was last collected and whether that is stale ("Patch data as of 9 Nov 2026 12:29 (2 days ago). Stale: the last 3 collections failed"); on the Overview, Server Health, Backups and both ticket pages |
| `AuthCard`, `Field`, `useSubmit` | `components/AuthCard.jsx` | The card the sign-in pages are built on, with its `Field` input and the `useSubmit` helper |
| `useApi`, `useSection` | `components/useApi.js`, `useSection.js` | The two data hooks described above |

## Shared logic (pure, and tested)

These modules have no screen in them, so they are tested with Node. **Most of what can go wrong is here, so most of the testing is here.**

| Module | What it decides | Tests |
|---|---|---|
| `worst.js` | The order each page starts in (worst first) and the Server Health sort choices | `worst.test.js` |
| `filter.js` | The column search grammar (`>`, `<`, `=`, `!=`, ranges, units) and the device-name filter | `filter.test.js` |
| `overview.js` | The Overview's client filter: the choices, the request parameters, what a click on a measure does | `overview.test.js` |
| `trend.js` | Trend ranges (the built-in ones and yours, all counted the same way), and the ticket volume trend (per day, or per week for a long period) | `trend.test.js` |
| `serverHealth.js` | The Server Health tiles, their counts and combined filters, and the wording of a card's patching | `serverHealth.test.js` |
| `scoreWorking.js` | The rule for how weights make the score, in words, and the server's working turned into rows and formulas | `scoreWorking.test.js` |
| `ranges.js` | Date ranges you name and save: the checks, adding and removing, and the quick buttons they make | `ranges.test.js` |
| `access.js` | The Access page's logic: checking a new person (the server's rules, so a mistake shows at once), what is sent to make a person or a group, how a group's clients read, and what to tell the administrator after a person is made (it is honest about whether an email was really sent) | `access.test.js` |
| `freshness.js` | How a collector's freshness is worded and coloured, and the "data as of" line | `freshness.test.js` |
| `gaps.js` | The wording of the record of client gaps | `gaps.test.js` |
| `glossary.js` | The hover definitions, scoped by page | `glossary.test.js` |
| `storage.js`, `persisted.js`, `rangeMemory.js`, `dateMath.js` | What is remembered, its checks, remembering a date range as a length, date arithmetic (including `durationStart`, the one place "the last N days, weeks or months" is counted) | `storage.test.js`, `persisted.test.js`, `rangeMemory.test.js` |
| `pagination.js`, `text.js`, `theme.js` | Page slicing, title case and number formatting, colours | `pagination.test.js`, `text.test.js` (the colours are data) |

### The report engine (`src/report/`)

A saved report is a definition (blocks, each with its own filters), and making a PDF is a pipeline of small modules, all pure except the last:

| Module | Job |
|---|---|
| `report/config.js` | What a definition is, and its validation. **The same rules, in the same words, as `backend/api/reports.py`**: the cases in `report/template-cases.json` are run by both |
| `report/dates.js` | The date presets, durations ("the last 3 months") and which dates a run uses |
| `report/fetch.js` | Fetches what each section needs, in parallel |
| `report/extract.js` | Turns the fetched data into the figures, tables and notes of each section |
| `report/lists.js` | Personal reports and templates, and what each person may do with one |
| `report/pdf.js` | Draws the PDF itself (cover, contents with page numbers, sections, annex) with no library, and saves it |

`docs/08_API_REFERENCE.md` ("How a report is made") describes the flow, and `report/fixtures/api-responses.json` holds real response shapes (generated from the backend code) that the tests use.

## Tables, filters and sorting

- **Every table is a `DataTable`.** It searches per column using the grammar in `filter.js` (a column of numbers takes `>5`, `<=10`, `5..20`; a duration takes `>2h`, `<=30m`, `1h..4h`; a size takes `>100gb`; a date takes `>2026-10-01` or `2026-10-01..2026-10-31`), sorts on a column click, pages with a `Pager`, and, when given a `persistKey`, remembers its sort and page size.
- **Pages start worst first** (`worst.js`): Tickets late or flagged first, Server Health infected then not connected then oldest data, Patches categories lowest compliance first, Backups critical first. A column sort overrides it and **Reset Sort** returns to it.
- **Filters show as bubbles** (`FilterBubbles`); each bubble removes just its own filter. Filters that hold several values use `MultiFilter`.
- **Hover definitions:** a term in a heading or label gets a dotted underline and a definition. The definitions are entries in the `TERMS` table in `glossary.js` (keyed by the lower-case term, or `scope|term` for one that means something different on one page); `define(term, scope)` looks one up and `hint(term, className, scope)` returns the props that show it. `glossary.test.js` lists the terms that must have a definition.

## Graphs

- **Every graph can be minimised.** `ChartCard` wraps a graph in a card with a title, a count and a minimise button; `ChartControls` adds Collapse All and Expand All for a group. The bar and donut charts wrap themselves; **anything that draws a `TrendChart` must also use a `ChartCard`**, and `src/charts.test.js` fails if a file does not.
- The three charts are drawn as plain SVG. `TrendChart` takes `{ heading, type, x, y, showHeading }`; pass `showHeading={false}` when a `ChartCard` above already shows the title.

## Styling

- **One file, `src/styles.css`.** Colours and sizes are variables on `:root` (`--bg`, `--surface`, `--text`, `--muted`, `--line`, `--accent`, `--good`, `--warn`, `--bad`, the radii and the shadow). The dark theme redefines them under `:root[data-theme="dark"]`.
- **Settings reach the page as attributes and variables:** `SettingsProvider` sets `data-theme`, `data-corners` and `data-shadows` on the page, and sets `--accent` and `--priority-*` as variables. Style with the variables, never with fixed colours, so both themes and every accent work.
- **Row colours** are set on cells (`tr.row-bad td`, `tr.tone-high td`, `.scores tr.selected td`, `tr.clickable:hover td`). The **alternating row stripe** is written with `:where()` so that it has the lowest priority and every one of those still wins. Keep it that way when adding row colours.
- **Common classes:** `panel`, `panel-head`, `panel-actions`, `toolbar`, `table-wrap`, `note`, `count`, `btn` (with `small`, `primary`), `icon-btn`, `bubble`/`bubbles`, `chip`, `field`, `badge`.
- Text is title-cased by `titleCase` in `text.js` where a heading is built from data.

## Conventions worth keeping

- **Buttons do things, links go places.** Use a `button` for any action, with `type="button"` inside a form, and a visible label or `aria-label`. State is announced with `aria-expanded`, `aria-pressed`, `role="status"` (for a note that appears) and `role="alert"` (for an error).
- **A page never invents data.** A missing value is shown as `—` or "No data", never as zero.
- **Errors are shown, not swallowed:** put `ErrorNote` where the data would be.
- **Anything remembered goes through `usePersistentState`** with a check in `persisted.js`, so a damaged entry is ignored instead of breaking a page.
- **Pure logic goes in a module of its own with a test**, not inside a page.

## Recipes

**Add a page.** Make `src/pages/Name.jsx` exporting `Name({ title })`, import it in `routes.js` and add a line (set `staffOnly` for an administrators' page). Add a row to the pages table above.

**Add a filter to a page.** Hold the chosen values with `usePersistentState` and a check from `persisted.js`; show them with `FilterBubbles`; narrow the rows before passing them to `DataTable`. If it can hold several values use `MultiFilter`.

**Remember something.** `const [x, setX] = usePersistentState('page:thing', defaultValue, isValid)`. Add a check to `persisted.js` if there is not one, add a row to the table above, and note it for the Settings page's forget button (which clears every entry without any change).

**Add a graph.** Draw it with `BarChart`, `Donut` or `TrendChart` inside a `ChartCard` (inside `ChartControls` if there is a group). Give its title an entry in `TERMS` in `glossary.js` if it needs a definition.

**Add a hover definition.** Add an entry to `TERMS` in `glossary.js` (`'term': 'what it is in this dashboard'`, or `'scope|term'` for a page-specific one), spread `hint('Term')` onto the element, and add the term to the list in `glossary.test.js`.

**Add a report section type.** Add it to `report/config.js` (and the matching rules in `backend/api/reports.py`, with a case in `template-cases.json`), fetch its data in `report/fetch.js`, and turn it into figures in `report/extract.js`.

## Where everything is

Every source file under `frontend/src`, with what it is for. `docs/SOURCE_INDEX.md` lists the backend files in the same way. A test fails if a file is missing from this table.

| File | What it is |
|---|---|
| `src/App.jsx` | Assembles the providers and decides what to show: the sign-in pages when signed out, otherwise the layout and the routes |
| `src/api/adapter.js` | The only file that talks to the server: tokens, refresh and every API call |
| `src/auth/AuthContext.jsx` | Holds the signed-in person, restores the session on load, signs out when it expires |
| `src/components/AuthCard.jsx` | The card the sign-in pages are built on, with its field and submit helpers |
| `src/components/BarChart.jsx` | A bar chart, wrapped in a `ChartCard` |
| `src/components/ChartCard.jsx` | `ChartCard` (a minimisable card for a graph) and `ChartControls` (Collapse All, Expand All) |
| `src/components/DataFilter.jsx` | `ChoiceFilter`: a single-choice filter where each choice has a value and a label |
| `src/components/DataAsOf.jsx` | The "data as of" line under a page's heading |
| `src/components/ScoreWorking.jsx` | The explanation of how the weights make the score, with the working for one client |
| `src/components/DateRange.jsx` | The date range control: slider, boxes, quick buttons, Refresh |
| `src/components/DetailModal.jsx` | The detail card used for a ticket, a device and a patch |
| `src/components/DeviceFilter.jsx` | The device-name search with suggestions |
| `src/components/Donut.jsx` | A donut chart, wrapped in a `ChartCard` |
| `src/components/Layout.jsx` | The sidebar, header and page frame |
| `src/components/MultiFilter.jsx` | A filter that holds several values as removable bubbles |
| `src/components/PageHeader.jsx` | The page title with its trail, and the date range |
| `src/components/Pager.jsx` | Page controls and page size |
| `src/components/Period.jsx` | A line saying which period is shown |
| `src/components/TicketExplorer.jsx` | Everything on a ticket page (both ticket pages are one line each) |
| `src/components/TrendChart.jsx` | A line or bar trend, drawn as SVG |
| `src/components/WeightFields.jsx` | The score-weight inputs, shared by the Overview and the report editor |
| `src/components/ui.jsx` | `DataTable`, `StatCard`, `StatusBadge`, `FilterBubbles`, `ErrorNote`, `SectionBody` |
| `src/components/useApi.js` | Runs a call, tracks loading and error, drives the loading overlay |
| `src/components/useSection.js` | Fetches a section for the applied date range |
| `src/dateMath.js` | Date arithmetic on `YYYY-MM-DD` |
| `src/filter.js` | The column search grammar, and the device-name filter |
| `src/freshness.js` | The wording and tone for how fresh a collector's data is, and the "data as of" line |
| `src/gaps.js` | The wording for the record of client gaps |
| `src/glossary.js` | The hover definitions (`TERMS`), `define` and `hint` |
| `src/main.jsx` | Starts the app: draws `App` into the page and loads `styles.css` |
| `src/overview.js` | The Overview's client filter: choices, request parameters, what a click on a measure does |
| `src/pages/Access.jsx` | Which clients each person may see; companies for the platform owner |
| `src/pages/Backups.jsx` | Backup health: tiles, charts, a table and a device card |
| `src/pages/ClientResolution.jsx` | Group and confirm unlinked customers, discrepancies, what was resolved |
| `src/pages/Clients.jsx` | The master clients, links, aliases and Unclaimed Clients |
| `src/pages/CollectionHealth.jsx` | Each collector's last run and freshness, run buttons and the latest log |
| `src/pages/ForgotPassword.jsx` | Ask for a password-reset email |
| `src/pages/Login.jsx` | Sign in |
| `src/pages/Patches.jsx` | The Overview: Overall Score, Clients card, trend, Scores By Client, weights and the drill-down |
| `src/pages/Register.jsx` | Create an account (only while registration is open) |
| `src/pages/Reports.jsx` | My Reports and Templates, the report editor and generating a PDF |
| `src/pages/ResetPassword.jsx` | Choose a new password from the emailed link |
| `src/pages/ServerHealth.jsx` | Server Health: tiles as filters, device filter, sort, paged cards |
| `src/pages/Settings.jsx` | Theme, accent, priority colours, layout, and forgetting remembered inputs |
| `src/pages/TechnicianTickets.jsx` | Tickets by technician (a `TicketExplorer`) |
| `src/pages/Tickets.jsx` | Tickets by client (a `TicketExplorer`) |
| `src/pagination.js` | Page sizes, and slicing rows into pages |
| `src/persisted.js` | The checks a remembered value must pass |
| `src/rangeMemory.js` | Remembering a date range as its length when it ended today |
| `src/report/config.js` | What a report definition is, and its validation (the same rules as the server) |
| `src/report/dates.js` | Report date presets, durations, and which dates a run uses |
| `src/report/extract.js` | Turns fetched data into the figures, tables and notes of each section |
| `src/report/fetch.js` | Fetches the data each report section needs |
| `src/report/lists.js` | Personal reports and templates, and what each person may do |
| `src/report/pdf.js` | Draws the PDF (cover, contents, sections, annex) and saves it, with no library |
| `src/report/template-cases.json` | The cases the browser and the server both validate report definitions against |
| `src/routes.js` | The list of pages (path, title, sidebar group, `staffOnly`); adding a page is adding a line |
| `src/serverHealth.js` | The Server Health tiles, their counts and combined filters, and the wording of a card's patching |
| `src/scoreWorking.js` | How the weights make the score, in words, and the working turned into text |
| `src/ranges.js` | Date ranges you name and save |
| `src/access.js` | The Access page's checks, requests and wording |
| `src/state/DateRangeContext.jsx` | The date range at the top: the draft, the applied range, Refresh, and remembering the last range |
| `src/state/LoadingContext.jsx` | Counts requests in flight, for the loading overlay |
| `src/state/SettingsContext.jsx` | Theme, accent, priority colours, corners, shadows and sidebar state: kept in the browser (`ops_*`) and applied to the page |
| `src/state/SavedRangesContext.jsx` | The one shared list of your saved date ranges |
| `src/state/persist.js` | `usePersistentState`, and forgetting everything that is remembered |
| `src/storage.js` | Where remembered values live: the browser, per person, checked on every read |
| `src/text.js` | Title case, and formatting durations and byte sizes |
| `src/theme.js` | The accent colours and the default ticket-priority colours |
| `src/styles.css` | All of the styling: tokens, themes, every component's classes |
| `src/trend.js` | Trend ranges, and the ticket volume trend |
| `src/worst.js` | The order each page starts in (worst first), and the Server Health sort choices |

## Known limits

- The pages and components are untested in a browser (see the note at the top).
- Remembered inputs live in one browser; they do not follow a person to another computer until `storage.js` is replaced by something on the server.
- The charts are drawn by hand with no library: they have no zoom, and their tooltips are the browser's own.
- The PDF is drawn by hand: it has no images and uses the standard fonts only.
