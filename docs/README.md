# Report Manager (IP Capital reporting platform): technical documentation

**Document version:** 2.0 · **Review date:** 10 November 2026 · **Reference implementation:** the project archive these files ship in (`ops-dashboard-24`), which extends the supplied application archive and the earlier patch archives.

> **Scope and confidence.** This documents the **code in the archive**, not a deployed server. Statements about live behaviour (collector successes and failures, row counts) come from command output and logs you supplied, and are labelled as user-reported. Where something was written but never run (the Django database tests, SQL against a real PostgreSQL, the frontend build, the PowerShell script, real email delivery), [`06_STATUS_AND_BACKLOG.md`](06_STATUS_AND_BACKLOG.md) says so. No production API or database was accessed.

## Reading these documents in one place

Run **`python docs/serve.py`** from the project folder, or double-click **`docs/Open-Docs.cmd`** on Windows. A page opens with every document, diagram, image and PDF in this folder in one list, with full-text search and a contents list for the open document. Links between documents work inside the page, and diagrams are drawn.

- **New files appear on their own.** The folder is re-read continuously: add, rename or edit a file and the list (and the open document) updates within a few seconds, with no restart.
- **It needs only Python**, no packages, and listens on this computer only. It cannot be a plain HTML file because a page opened from disk is not allowed to list a folder or read its neighbours.
- **Diagrams** are drawn with Mermaid, from `docs/vendor/mermaid.min.js` if you put a copy there (for offline use), otherwise from the internet. Without either, the diagram source is shown instead.
- Word files are offered as downloads; PDFs, SVG and PNG images open in the page; SQL, Graphviz and other text files are shown as text.

## Read in this order

| File | Contents |
|---|---|
| [`01_SYSTEM_OVERVIEW.md`](01_SYSTEM_OVERVIEW.md) | Purpose, components, ownership proposal, trust boundaries, open decisions |
| [`02_FLOWCHARTS.md`](02_FLOWCHARTS.md) | Mermaid diagrams: architecture, rerun and worker, tick decisions, alerts, requests, authentication, client resolution, scores and trend, reports, relationships |
| [`03_CODEBASE_AND_API.md`](03_CODEBASE_AND_API.md) | Source tree, how the backend and frontend behave, pages, integrations, tests |
| [`04_DATA_MODEL.md`](04_DATA_MODEL.md) | Schemas, the application tables, SQL semantics, cross-source identity, planned master list |
| [`05_OPERATIONS.md`](05_OPERATIONS.md) | Configuration table, jobs, scheduler options, alerts, commands, technician data, runbooks |
| [`06_STATUS_AND_BACKLOG.md`](06_STATUS_AND_BACKLOG.md) | What is built and what was run, delivery gates, full backlog with statuses |
| [`07_VERIFICATION_CHECKLIST.md`](07_VERIFICATION_CHECKLIST.md) | Repeatable acceptance checklist and signoff table |
| [`08_API_REFERENCE.md`](08_API_REFERENCE.md) | **Every HTTP endpoint**: permissions, inputs, outputs, errors, generated examples |
| [`10_FRONTEND_GUIDE.md`](10_FRONTEND_GUIDE.md) | **The front end**: how it starts, the pages, components, shared logic, what is remembered, styling, conventions, and recipes for changing it |
| [`11_COLLECTORS.md`](11_COLLECTORS.md) | **The collectors**: what each of the four does, the jobs the dashboard runs, how a run is judged, the tables each fills, and how to add one |
| [`09_ER_DIAGRAMS.md`](09_ER_DIAGRAMS.md) | **Entity-relationship diagrams** for every schema, across schemas, and the planned master list; generated from the DDL and the Django models |
| [`SOURCE_INDEX.md`](SOURCE_INDEX.md) | Every module with its purpose line, and provenance |
| [`STAGED_PLAN.md`](STAGED_PLAN.md) | Working plan: decisions taken, tracks and their status |
| `serve.py`, `Open-Docs.cmd` | The documentation viewer described above |
| `IP_Capital_Technical_Handover.docx` / `.pdf` (source: `.md`) | Short stakeholder handover (architecture, ownership, status) |

## Key decisions

1. **One reporting database, several schemas.** Django reads `autotask`, `ncentral`, `sentinelone` and `cove` with schema-qualified SQL; `report_manager` holds the application's own tables. The collectors' source tables are never copied into `report_manager`.
2. **Collectors are separate processes**, never run in web request threads. An HTTP request only queues work.
3. **Something must tick the worker.** `python manage.py run_scheduler`, or two Windows scheduled tasks. A heartbeat shows it is alive; email alerts report failures, overdue data and a silent worker.
4. **Never fabricate data.** Missing history, assignments or metrics show as empty or "not collected", not as zeros or samples.
5. **Match clients by ID.** A client links to customers in each system by that system's own ID. Names match only through a client's name or alias, and grouping by name is always flagged as an inference.
6. **Scores are calculated on the server** (category-weighted, shared weights with personal overrides) and saved as snapshots after each patch collection, so trends are real history.
7. **Access control comes last.** Collector and client management are staff-only now; per-client access (Django groups plus a user-to-client link) is planned after everything else.

## Keeping these files true

`backend/api/test_docs.py` fails if a route in `config/urls.py` is missing from [`08_API_REFERENCE.md`](08_API_REFERENCE.md), if an environment variable read by `settings.py` is missing from [`05_OPERATIONS.md`](05_OPERATIONS.md), if a collector job or report section is not mentioned, or if the ER diagrams no longer match the DDL and the models (regenerate them with `python docs/tools/generate_er.py`), or if [`SOURCE_INDEX.md`](SOURCE_INDEX.md) is missing a module (`python docs/tools/generate_source_index.py`), or if [`10_FRONTEND_GUIDE.md`](10_FRONTEND_GUIDE.md) is missing a front-end file, page, API call, remembered setting or component, or if [`11_COLLECTORS.md`](11_COLLECTORS.md) is missing a collector folder, job, setting, table, shipped default or dry-run rule. Update the documents in the same change as the code.

## Viewing the diagrams

Standard [Mermaid](https://mermaid.js.org/): GitHub and Mermaid-aware Markdown viewers render them. They are also in `diagrams/` as `.mmd` files, with Graphviz sources and images for the architecture and worker diagrams, and PNG and SVG images of each schema with every column (`er_*.png`, `er_*.svg`). The author could not render Mermaid, so report a diagram that fails to draw.
