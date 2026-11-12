"""
Autotask — tabular dataset builder.

Turns the nested dicts produced by autotask_client into flat, database-shaped
tables. This is the layer between the API and PostgreSQL: it does no I/O of its
own beyond the ticket fetch, so it can also be used to feed CSV/Excel/pandas.

Entry points:
  fetch_dataset(company, ...)    Build all tables from live API data
  build_dataset(company, rows)   Build all tables from an already-fetched report
  DataTable                      Minimal list-of-dicts table (.rows/.columns/.to_csv/.to_dataframe)

Tables produced (names match the PostgreSQL tables in sql/schema.sql):
  company      one row — the company the snapshot is for
  ticket       one row per ticket
  ticket_note  one row per note on those tickets
  contact      one row per company contact (only when with_contacts=True)

Every table carries company_id; the loader stamps snapshot_id at insert time.
"""

from datetime import datetime, timezone

from autotask_client import (
    DEFAULT_TIMEZONE,
    AutotaskClient,
    parse_autotask_datetime,
)

# ---------------------------------------------------------------------------
# Column order per table — also the INSERT column order used by autotask_db
# ---------------------------------------------------------------------------

COMPANY_COLUMNS = [
    "company_id", "company_name", "is_active", "ticket_count", "details",
]

TICKET_COLUMNS = [
    "ticket_id", "ticket_number", "company_id", "company_name",
    "title", "description", "resolution",
    "ticket_type", "priority", "issue_type", "sub_issue_type", "status",
    "sla_met", "first_response_met",
    "business_hours", "business_hours_start", "business_hours_end",
    "holiday_set_id", "holidays_excluded",
    "first_response_at", "first_response_due_at",
    "created_at", "completed_at",
]

NOTE_COLUMNS = [
    "note_id", "ticket_id", "company_id",
    "title", "description", "note_type", "publish", "created_at",
]

CONTACT_COLUMNS = [
    "contact_id", "company_id",
    "first_name", "last_name", "title", "email", "phone", "mobile_phone",
    "is_active",
]

TABLE_COLUMNS = {
    "company":     COMPANY_COLUMNS,
    "ticket":      TICKET_COLUMNS,
    "ticket_note": NOTE_COLUMNS,
    "contact":     CONTACT_COLUMNS,
}

# company must come first — the others carry a foreign key to it.
TABLE_ORDER = ["company", "ticket", "ticket_note", "contact"]


# ---------------------------------------------------------------------------
# DataTable
# ---------------------------------------------------------------------------

class DataTable:
    """A named table of uniform rows. Dependency-free; pandas is optional."""

    def __init__(self, name: str, columns: list, rows: list = None):
        self.name    = name
        self.columns = list(columns)
        self.rows    = list(rows or [])

    def append(self, row: dict) -> None:
        self.rows.append({c: row.get(c) for c in self.columns})

    def tuples(self) -> list:
        """Rows as tuples in `columns` order — ready for executemany/copy."""
        return [tuple(r.get(c) for c in self.columns) for r in self.rows]

    def to_dataframe(self):
        import pandas as pd  # optional dependency
        return pd.DataFrame(self.rows, columns=self.columns)

    def to_csv(self, path: str) -> str:
        import csv
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self.columns)
            w.writeheader()
            w.writerows(self.rows)
        return path

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __repr__(self):
        return f"<DataTable {self.name}: {len(self.rows)} rows x {len(self.columns)} cols>"


class Dataset:
    """The four tables plus the metadata describing how they were captured."""

    def __init__(self, captured_at, company_filter=None, days_back=None):
        self.captured_at    = captured_at
        self.company_filter = company_filter
        self.days_back      = days_back
        self.tables         = {
            name: DataTable(name, cols) for name, cols in TABLE_COLUMNS.items()
        }

    def __getitem__(self, name) -> DataTable:
        return self.tables[name]

    @property
    def company(self):     return self.tables["company"]
    @property
    def ticket(self):      return self.tables["ticket"]
    @property
    def ticket_note(self): return self.tables["ticket_note"]
    @property
    def contact(self):     return self.tables["contact"]

    def to_dataframes(self) -> dict:
        return {name: t.to_dataframe() for name, t in self.tables.items()}

    def to_csv_dir(self, directory: str) -> list:
        import os
        os.makedirs(directory, exist_ok=True)
        return [t.to_csv(os.path.join(directory, f"{name}.csv"))
                for name, t in self.tables.items()]

    def summary(self) -> dict:
        return {
            "captured_at":    self.captured_at.isoformat(),
            "company_filter": self.company_filter,
            "days_back":      self.days_back,
            "row_counts":     {n: len(t) for n, t in self.tables.items()},
        }

    def __repr__(self):
        counts = ", ".join(f"{n}={len(t)}" for n, t in self.tables.items())
        return f"<Dataset {self.captured_at:%Y-%m-%d %H:%M} {self.company_filter!r} {counts}>"


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _dt(value):
    """Autotask timestamp → aware UTC datetime, or None. DB cols are TIMESTAMPTZ."""
    if value in (None, ""):
        return None
    try:
        return parse_autotask_datetime(value)
    except Exception:
        # A single unparseable timestamp should not sink the whole load.
        return None


def _num(value):
    """Loose numeric → float, or None."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    if value in (None, "", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bool(value):
    """Autotask sends booleans, 0/1 and occasionally strings. None stays None."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_dataset(
    company: dict,
    ticket_rows: list,
    contacts: list = None,
    details: dict = None,
    days_back: int = None,
    captured_at=None,
) -> Dataset:
    """
    Flatten one company's ticket report into a Dataset.

    company      {"id": ..., "companyName": ..., "isActive": ...}
    ticket_rows  rows as returned by AutotaskClient.company_tickets_report
    contacts     rows as returned by AutotaskClient.company_contacts
    details      the full Companies record, stored as JSON on the company row
    """
    company_id   = _int(company.get("id"))
    company_name = company.get("companyName")
    ds = Dataset(
        captured_at or datetime.now(tz=timezone.utc),
        company_filter=company_name,
        days_back=days_back,
    )

    ds.company.append({
        "company_id":   company_id,
        "company_name": company_name,
        "is_active":    _bool(company.get("isActive")),
        "ticket_count": len(ticket_rows),
        "details":      details,
    })

    for row in ticket_rows:
        ticket_id = _int(row.get("ticketID"))
        if ticket_id is None:
            continue                      # cannot key a row without the ticket id

        ds.ticket.append({
            "ticket_id":             ticket_id,
            "ticket_number":         row.get("ticketNumber"),
            "company_id":            company_id,
            "company_name":          company_name,
            "title":                 row.get("title"),
            "description":           row.get("description"),
            "resolution":            row.get("resolutionComments"),
            "ticket_type":           row.get("ticketType"),
            "priority":              row.get("ticketPriority"),
            "issue_type":            row.get("ticketIssue"),
            "sub_issue_type":        row.get("ticketSubIssue"),
            "status":                row.get("status"),
            "sla_met":               _bool(row.get("slaMet")),
            "first_response_met":    _bool(row.get("firstResponseMet")),
            "business_hours":        _num(row.get("businessHours")),
            "business_hours_start":  row.get("businessHoursStart"),
            "business_hours_end":    row.get("businessHoursEnd"),
            "holiday_set_id":        _int(row.get("holidaySetID")),
            "holidays_excluded":     _int(row.get("holidaysExcluded")),
            "first_response_at":     _dt(row.get("firstResponseDateTime")),
            "first_response_due_at": _dt(row.get("firstResponseDueDateTime")),
            "created_at":            _dt(row.get("createdDate")),
            "completed_at":          _dt(row.get("completedDate")),
        })

        for note in (row.get("ticketNotes") or []):
            note_id = _int(note.get("id"))
            if note_id is None:
                continue
            ds.ticket_note.append({
                "note_id":     note_id,
                "ticket_id":   ticket_id,
                "company_id":  company_id,
                "title":       note.get("title"),
                "description": note.get("description"),
                "note_type":   str(note.get("noteType")) if note.get("noteType") is not None else None,
                "publish":     str(note.get("publish")) if note.get("publish") is not None else None,
                "created_at":  _dt(note.get("date")),
            })

    for contact in (contacts or []):
        contact_id = _int(contact.get("id"))
        if contact_id is None:
            continue
        ds.contact.append({
            "contact_id":   contact_id,
            "company_id":   company_id,
            "first_name":   contact.get("firstName"),
            "last_name":    contact.get("lastName"),
            "title":        contact.get("title"),
            "email":        contact.get("emailAddress"),
            "phone":        contact.get("phone"),
            "mobile_phone": contact.get("mobilePhone"),
            "is_active":    _bool(contact.get("isActive")),
        })

    return ds


def fetch_dataset(
    company: str,
    last_days: int = 30,
    max_records: int = 500,
    timezone_name: str = DEFAULT_TIMEZONE,
    with_notes: bool = True,
    with_contacts: bool = False,
    with_details: bool = True,
    client: AutotaskClient = None,
) -> Dataset:
    """
    Fetch one company's tickets from the Autotask API and return them as flat tables.

    company        company name; resolved with the client's exact-match-wins rule
    last_days      how far back to look, by ticket create date
    max_records    ticket cap for the query
    with_notes     also fetch recent notes per ticket (one API call per ticket)
    with_contacts  also fetch the company's contacts
    with_details   also fetch the full company record, stored as JSON

    Pass `client` to reuse one client (and its resolved zone URL) across companies.
    """
    client = client or AutotaskClient.from_env()

    resolved = client.find_company(company)
    details  = client.company_details(company_id=resolved["id"]) if with_details else None
    if details:
        # The detail record is authoritative for name/active state.
        resolved = {**resolved, **{k: details.get(k) for k in ("companyName", "isActive")
                                   if details.get(k) is not None}}

    ticket_rows = client.company_tickets_report(
        name=resolved["companyName"],
        last_days=last_days,
        max_records=max_records,
        timezone_name=timezone_name,
        with_notes=with_notes,
    )

    contacts = client.company_contacts(resolved["id"]) if with_contacts else None

    return build_dataset(
        company=resolved,
        ticket_rows=ticket_rows,
        contacts=contacts,
        details=details,
        days_back=last_days,
    )


if __name__ == "__main__":
    import sys
    name = " ".join(sys.argv[1:])
    if not name:
        print("usage: python autotask_dataset.py <company name>")
        raise SystemExit(1)
    ds = fetch_dataset(name)
    print(ds)
    for table_name, table in ds.tables.items():
        print(f"  {table_name:<20} {len(table):>6} rows")
