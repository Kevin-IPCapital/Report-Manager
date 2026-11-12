# Cornel AutoTask CoPilot

A small Python client for the Autotask REST API: `autotask_client.py`, usable either as a CLI or as
a library.

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in:

```env
AUTOTASK_USERNAME=
AUTOTASK_SECRET=
AUTOTASK_INTEGRATION_CODE=
```

`AUTOTASK_BASE_URL` is optional. If it is blank, the client discovers the correct Autotask zone by
calling `zoneInformation`.

`.env` is read from the **current working directory**, so run the client from this folder.
Needs Python 3.10+ and uses only the standard library — no `pip install` step.

## CLI

```
python autotask_client.py <command> [options]
```

Commands: `zone`, `companies`, `company`, `tickets`, `fields`. All of them print JSON to stdout, so
you can redirect to a file:

```powershell
python autotask_client.py tickets --company "IP Capital" --days 30 > report.json
```

### `zone`

Discovers and prints the API base URL for your tenant. The quickest connectivity/credential test.

```powershell
python autotask_client.py zone
```

### `companies` — find companies

Lists companies (id, name, active flag), optionally filtered by name.

| Option | Default | Meaning |
| --- | --- | --- |
| `--limit N` | `5` | Maximum records to request |
| `--name TEXT` | *(none)* | Only companies whose name contains `TEXT` |
| `--include-inactive` | off | Include inactive companies as well as active ones |

```powershell
python autotask_client.py companies --limit 5
python autotask_client.py companies --name "IP Capital" --limit 50
python autotask_client.py companies --name "IP Capital" --limit 50 --include-inactive
```

Use this to find exact names and ids before running the other commands. For example,
`--name "IP Capital"` returns `IP Capital` (367), `IP Capital: BITCO` (809) and
`IP Capital: CCOD` (810). Raise `--limit` when searching — the default of 5 silently truncates.

### `company` — full detail for one company

Returns the whole company record: address, phone, web site, owner, classification, currency,
account number, user-defined fields, and everything else Autotask stores on `Companies`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--name NAME` | — | Company name to match (mutually exclusive with `--id`) |
| `--id N` | — | Company id, e.g. `367` (mutually exclusive with `--name`) |
| `--contacts` | off | Also include the company's contacts |

One of `--name` or `--id` is required.

```powershell
python autotask_client.py company --name "IP Capital"
python autotask_client.py company --id 809
python autotask_client.py company --name "IP Capital: CCOD" --contacts
python autotask_client.py companies --name "IP Capital" --limit 50 --include-inactive
```

`--contacts` appends a `contacts` array (id, first/last name, title, email, phone, mobile, active
flag), fetched across all pages. To see which company fields exist, use `fields Companies`.

### `tickets` — ticket report for one company

Recent tickets with SLA outcomes and business-hours elapsed time.

| Option | Default | Meaning |
| --- | --- | --- |
| `--company NAME` | `Vulpes Technologies` | Company name to match |
| `--days N` | `7` | How many days back to look (by ticket create date) |
| `--limit N` | `10` | Maximum tickets to request |
| `--timezone ID` | `Africa/Johannesburg` | Report time zone, IANA or Windows id |
| `--include-notes` | off | Include up to 10 recent ticket notes per ticket |

```powershell
python autotask_client.py tickets --company "IP Capital" --days 7 --limit 10
python autotask_client.py tickets --company "IP Capital: BITCO" --days 30 --limit 50
python autotask_client.py tickets --company "IP Capital: CCOD" --days 7 --include-notes
python autotask_client.py tickets --company "ACME" --timezone "South Africa Standard Time"
```

Each row includes: company name/ID, ticket number, title, description, resolution comments,
type/priority/issue/sub-issue labels, `slaMet`, `firstResponseMet`, `businessHours`, the business
hour window used, `holidaysExcluded`, first-response timestamps, created/completed dates and status.

**Company matching.** `--company` is a `contains` match. If several companies match, the client
prefers an exact (case-insensitive) name match and otherwise errors listing the candidates — so
`"IP Capital"` resolves to the parent company even though it is a prefix of the two subdivisions.
There is no way to report on a parent and its subdivisions in one call; run one command per company.

**Business hours.** Elapsed time counts only 08:00–17:00 on weekdays, excluding holidays from the
default internal location's holiday set. The hours are constants (`BUSINESS_START_HOUR` /
`BUSINESS_END_HOUR` near the top of `autotask_client.py`), not CLI options. If the holiday set or
SLA results cannot be read, the client warns on stderr and continues.

### `fields` — entity metadata

Prints field metadata for any entity — useful for discovering valid field names and picklist labels.

```powershell
python autotask_client.py fields Tickets
python autotask_client.py fields Companies
python autotask_client.py fields Tickets --picklists-only
```

`--picklists-only` prints just the integer-to-label maps for every picklist field on the entity.

## Using it as a library

Company details and contacts:

```python
from autotask_client import AutotaskClient

client = AutotaskClient.from_env()

details = client.company_details(name="IP Capital: BITCO", with_contacts=True)
print(details["companyName"], details.get("phone"))
for contact in details["contacts"]:
    print(contact["firstName"], contact["lastName"], contact["emailAddress"])
```

Ticket report:

```python
from autotask_client import AutotaskClient

client = AutotaskClient.from_env()
rows = client.company_tickets_report(
    name="IP Capital: BITCO",
    last_days=30,
    max_records=50,
    with_notes=True,
)
for row in rows:
    print(row["ticketNumber"], row["status"], row["businessHours"])
```

Query a single entity by ID:

```python
company = client.get_by_id("Companies", 367)
```

Run a filtered query:

```python
result = client.query(
    "Companies",
    [{"op": "eq", "field": "CompanyName", "value": "ACME Corp"}],
)
print(result["items"])
```

Fetch all pages:

```python
companies = client.query_all(
    "Companies",
    [{"op": "eq", "field": "IsActive", "value": True}],
)
print(len(companies))
```

Other useful methods: `discover_base_url()`, `entity_fields(entity)`,
`picklist_maps(entity, fields)`, `find_company(name)`, `company_contacts(company_id)`,
`ticket_sla_result(ticket_id)`, `ticket_notes_summary(ticket_id)`, and the module-level
`business_hours_between(...)` helper. Anything that fails raises `AutotaskError`.

## PostgreSQL loader

Three extra files mirror the Cove-main layout:

| File | Role |
| --- | --- |
| `autotask_dataset.py` | Flattens the API's nested dicts into database-shaped tables |
| `autotask_db.py` | Inserts those tables into PostgreSQL, one snapshot per load |
| `sql/schema.sql` | Schema definition — tables, indexes, views |
| `sql/queries.sql` | Reference read queries |

### Setup

```powershell
pip install -r requirements.txt
```

Add the connection string to `.env`:

```env
AUTOTASK_PG_DSN=postgresql://user:password@localhost:5432/ipc_reporting
```

Create the schema (idempotent, safe to re-run):

```powershell
psql "$env:AUTOTASK_PG_DSN" -f sql/schema.sql
```

### The customer list that drives the loop

`autotask.customers` is the driver table. `schema.sql` seeds it with the three
IP Capital companies; `sync-customers` adds any others from the API and refreshes their ids:

```powershell
python autotask_db.py sync-customers "IP Capital"
python autotask_db.py customers
```

`sync-customers` upserts every **active** company whose name contains the fragment, matching on
name, so re-running keeps ids current without creating duplicates. Rows it does not match are left
alone — set `is_active = FALSE` by hand to stop loading a customer (don't delete the row, or old
snapshots lose their context).

### Loading

```powershell
python autotask_db.py load "IP Capital: BITCO" --days 30
python autotask_db.py load-active --days 30
python autotask_db.py load-active --days 90 --no-notes
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--days N` | `30` | Lookback window, by ticket create date |
| `--limit N` | `500` | Maximum tickets per company |
| `--timezone ID` | `Africa/Johannesburg` | Time zone for the business-hours maths |
| `--no-notes` | off | Skip ticket notes — much faster, one less API call per ticket |
| `--no-contacts` | off | Skip company contacts |
| `--notes-text` | — | Free text stored on the snapshot row, e.g. `"July board pack"` |

`load-active` loops over every active customer, one API fetch and one snapshot each, reusing a
single client so the zone URL is discovered once. A customer that fails is reported and skipped
rather than taking the run down; the exit code is 1 if any failed.

### Data model

Every load inserts one `snapshot` row and stamps the child rows with its `snapshot_id`, so loads
accumulate history instead of overwriting. Nothing is ever updated in place — the same ticket can
appear in several snapshots as its status and resolution change.

| Table | Grain |
| --- | --- |
| `snapshot` | One row per load run (company, lookback window, ticket count, notes) |
| `company` | The company the snapshot is for, with the full API record in a `details` JSONB column |
| `ticket` | One row per ticket: classification labels, SLA outcome, business hours, timeline |
| `ticket_note` | One row per note on those tickets |
| `contact` | One row per company contact |

Unlike Cove, snapshots here are **per company**, not per estate — so "latest" has to be resolved per
company. `v_latest_snapshot` does that with `DISTINCT ON (company_filter)`, and
`v_ticket_latest` / `v_company_latest` / `v_contact_latest` / `v_ticket_note_latest` build on it.
`v_company_summary` is the per-customer scorecard: ticket counts, SLA met percentage, average and
worst business hours.

### Reading it back

`sql/queries.sql` is a reference file of 13 queries — scorecard, SLA breaches, first-response
misses, ageing open tickets, issue mix, resolution time by priority, monthly trend, snapshot
comparison, ticket-with-notes narrative, company detail out of the JSONB, contacts, and
housekeeping/pruning. Paste them individually rather than running the file as a script.

### Library use

```python
from autotask_dataset import fetch_dataset
from autotask_db import save_dataset, load_active_customers

dataset = fetch_dataset("IP Capital: CCOD", last_days=30, with_contacts=True)
print(dataset.summary())
dataset.to_csv_dir("out")            # or dataset.to_dataframes() with pandas installed

snapshot_id = save_dataset(dataset, notes="ad-hoc load")
load_active_customers(last_days=30)
```

## Other clients

`AutotaskClient.ps1` is an older PowerShell port covering only `Zone`, `Companies` and `Tickets`.
It has not been kept in step with the Python client — prefer `autotask_client.py`.
