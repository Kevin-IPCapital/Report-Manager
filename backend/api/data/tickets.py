"""Autotask tickets."""
from datetime import datetime, timedelta

from .. import clients
from . import common as c

TICKETS_TABLE = 'autotask.ticket'
_TICKET_FIELDS = ('snapshot_id', 'ticket_id', 'ticket_number', 'company_id', 'company_name', 'title', 'description',
                  'resolution', 'ticket_type', 'priority', 'issue_type', 'sub_issue_type', 'status', 'sla_met',
                  'first_response_met')
_TICKET_TIMES = ('first_response_at', 'first_response_due_at', 'created_at', 'completed_at')
_GROUP_FIELDS = ('company_name', 'ticket_type', 'priority', 'issue_type', 'sub_issue_type')  # the charts group by these


def technician_support():
    """Whether the reporting database can say who a ticket is assigned to. Today the Autotask collector does not capture it, so
    this is normally (False, False). It is checked on every request, so it starts working the moment the columns exist
    (see Collector/AutotaskLight-main/sql/proposed/technician.sql). Returns (has the assignment column, has resource names)."""
    found = c.query("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'autotask' "
                    "AND ((table_name = 'ticket' AND column_name = 'assigned_resource_id') "
                    "OR (table_name = 'resource' AND column_name IN ('resource_id', 'first_name', 'last_name')))")
    has_column = any(r['table_name'] == 'ticket' for r in found)
    resource_columns = {r['column_name'] for r in found if r['table_name'] == 'resource'}
    return has_column, {'resource_id', 'first_name', 'last_name'} <= resource_columns


def _technician_names():
    names = {}
    for r in c.query('SELECT resource_id, first_name, last_name FROM autotask.resource'):
        names[r['resource_id']] = ' '.join(part for part in (r['first_name'], r['last_name']) if part).strip()
    return names


def _technician(ticket, names):
    """The technician for a row. `names` is None when assignments are not collected (then there is nothing to show).
    An assignment with no known name is shown as 'Resource <id>'; no assignment is 'Unassigned'."""
    if names is None:
        return None
    resource_id = ticket.get('assigned_resource_id')
    return 'Unassigned' if resource_id is None else names.get(resource_id) or f'Resource {resource_id}'


def _select_tickets(start, end, with_assignment=False):
    """Tickets created on or between the two dates (either may be None), from the reporting database."""
    # One version per stable source ticket ID, then apply the requested date range.
    # DISTINCT ON avoids choosing different versions of the same ticket by ticket_number.
    where, params = [], []
    if start:
        where.append('created_at >= %s')
        params.append(datetime(start.year, start.month, start.day, tzinfo=c.UTC))
    if end:
        where.append('created_at < %s')
        params.append(datetime(end.year, end.month, end.day, tzinfo=c.UTC) + timedelta(days=1))
    columns = ', '.join(_TICKET_FIELDS + _TICKET_TIMES + (('assigned_resource_id',) if with_assignment else ()))
    predicate = ' WHERE ' + ' AND '.join(where) if where else ''
    return c.query(
        f"SELECT {columns} FROM ("
        f"SELECT DISTINCT ON (ticket_id) {columns} FROM {TICKETS_TABLE} "
        f"ORDER BY ticket_id, snapshot_id DESC"
        f") newest{predicate} ORDER BY created_at DESC", params)


def _human_delta(delta):
    d, rest = divmod(int(delta.total_seconds() // 60), 1440)
    h, m = divmod(rest, 60)
    return ' '.join(part for part in (f'{d}d' if d else '', f'{h}h' if h else '', f'{m}m' if m else '') if part) or '<1m'


def _row_reasons(t):
    """Red-row rules. Add a rule by appending a human-readable reason; any reason makes the row red."""
    reasons = []
    if t['first_response_at'] and t['first_response_due_at'] and t['first_response_at'] > t['first_response_due_at']:
        late = _human_delta(t['first_response_at'] - t['first_response_due_at'])
        reasons.append(f'First response {late} after the due time')
    return reasons


def _ticket_row(t, resolver, technicians=None):
    client_id = resolver.client_id('autotask', t['company_id'], t['company_name'])  # before NULLs are relabelled
    t = {**t, **{k: t[k] or 'Unspecified' for k in _GROUP_FIELDS}}  # NULLs would break the chart labels
    reasons = _row_reasons(t)
    return {
        **{k: t[k] for k in _TICKET_FIELDS}, **{k: c.iso(t[k]) for k in _TICKET_TIMES},
        'response_hours': c.hours(t['created_at'], t['first_response_at']),
        'resolution_hours': c.hours(t['created_at'], t['completed_at']),
        'rowTone': t['priority'].lower(),                 # frontend colours this from the Settings page
        'rowStatus': 'bad' if reasons else None,          # red; takes precedence over the priority tint
        'reason': '; '.join(reasons),
        'technician_id': t.get('assigned_resource_id'),
        'technician': _technician(t, technicians),        # None until the source collects assignments
        'client_id': client_id,                           # master client, or None when unmapped
    }


def tickets(start, end):
    """Every ticket created in [start, end] with all its fields. The frontend does the filtering, grouping,
    sorting and averaging, so this one response serves every chart, tile and the table."""
    has_column, has_names = technician_support()
    rows = _select_tickets(start, end, with_assignment=has_column)
    technicians = (_technician_names() if has_names else {}) if has_column else None
    rows = [{**t, **{k: c.aware(t[k]) for k in _TICKET_TIMES}} for t in rows]
    resolver = clients.load_resolver()
    return {'tickets': {
        'title': 'Tickets',
        'columns': [c.col('ticket_number', 'Ticket'), c.col('company_name', 'Client'), c.col('title', 'Title', wrap=True),
                    c.col('ticket_type', 'Type'), c.col('priority', 'Priority', type='priority'), c.col('status', 'Status', type='text'),
                    c.col('issue_type', 'Issue Type'), c.col('sub_issue_type', 'Sub Type'),
                    *([c.col('technician', 'Technician')] if has_column else []),
                    c.col('response_hours', 'Response Time', type='duration'), c.col('resolution_hours', 'Resolution Time', type='duration'),
                    c.col('sla_met', 'SLA Met', type='bool'), c.col('first_response_met', 'Response Met', type='bool')],
        'rows': [_ticket_row(t, resolver, technicians) for t in rows],
    }, 'technician_available': has_column}
