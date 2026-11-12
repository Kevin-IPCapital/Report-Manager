"""Check every dashboard section against the real data, read-only, and say in plain words what worked, what is empty and what failed.

The sections have been tested against stand-in data; this is how to find out about the real databases (a column that is not there, a
table that is empty, customers that are linked to nothing). `run_checks` takes the function that fetches a section, so it can be tested
with any data; `python manage.py verify_data` runs it against the live ones."""
import time
from datetime import date, timedelta

OK, EMPTY, FAILED, SKIPPED = 'OK', 'EMPTY', 'FAILED', 'SKIPPED'


def _linked(rows, label):
    mapped = sum(1 for r in rows if r.get('client_id'))
    return f'{len(rows)} {label}, {mapped} linked to a client' + ('' if mapped == len(rows) else f' ({len(rows) - mapped} not linked)')


def _tickets(payload):
    rows = payload['tickets']['rows']
    return len(rows), _linked(rows, 'tickets')


def _patches(payload):
    categories = [c for t in payload['types'] for c in t['categories']]
    entries = sum(c['total'] for c in categories)
    current = payload['overall']['current']
    return entries, f"{len(categories)} categories, {entries} patch entries, overall score {'none' if current is None else f'{current}%'}"


def _overview(payload):
    clients = payload['clients']
    return len(clients), f"{len(clients)} clients/customers, {sum(1 for c in clients if not c['linked'])} not linked to a client"


def _entries(payload):
    rows = payload['entries']['rows']
    return len(rows), f'{len(rows)} patches in the first category'


def _servers(payload):
    rows = payload['servers']
    info = payload.get('patch_info') or {}
    if rows and not info.get('available'):   # the page still loads without patching, which is exactly why this check must not let it pass
        raise RuntimeError("the servers loaded but their patching could not be read (the reason is in the server's log, and is usually a missing N-central table or column)")
    extra = (f", {info.get('matched', 0)} matched to an N-central device for patching, {info.get('disconnected', 0)} of them disconnected (N-central cannot find them)" if rows else '')
    return len(rows), _linked(rows, 'endpoints') + extra


def _backups(payload):
    rows = payload['rows']
    if not payload['available']:
        return 0, 'the cove schema was not found'
    return len(rows), _linked(rows, 'devices') if rows else 'nothing collected yet (the Cove job is disabled until its first run)'


def _backup_detail(payload):
    return 1, f"{len(payload['datasources'])} data sources, {len(payload['flags'])} red flags"


def _trend(payload):
    return len(payload['y']), f"{len(payload['y'])} saved scores" + ('' if payload['y'] else ' (saved after each patch collection, or `manage.py snapshot_scores --backfill`)')


_IMPORTED_AT_SQL = ("SELECT t.table_schema, COUNT(*) AS tables, COUNT(c.column_name) AS with_column FROM information_schema.tables t "
                    "LEFT JOIN information_schema.columns c ON c.table_schema = t.table_schema AND c.table_name = t.table_name AND c.column_name = 'imported_at' "
                    "WHERE t.table_schema IN ('ncentral', 'autotask', 'sentinelone', 'cove') AND t.table_type = 'BASE TABLE' GROUP BY t.table_schema ORDER BY t.table_schema")


def check_imported_at(query, clock=time.monotonic):
    """Has the imported_at migration (DATA-01) been applied to the collectors' schemas? Read-only: it looks at the column names only. OK when every
    table of every schema found has the column; EMPTY when none has (not applied yet); FAILED when only some have (applied halfway)."""
    started = clock()
    try:
        rows = query(_IMPORTED_AT_SQL)
        have = {r['table_schema']: (int(r['tables']), int(r['with_column'])) for r in rows}
        if not have:
            status, detail = EMPTY, 'none of the collector schemas was found'
        elif all(with_column == 0 for _, with_column in have.values()):
            status, detail = EMPTY, 'not applied yet: run each collector\'s sql/migrations/2026-11-12_imported_at.sql (see docs/05_OPERATIONS.md)'
        else:
            short = {schema: f'{with_column} of {tables} tables' for schema, (tables, with_column) in have.items() if with_column != tables}
            status = FAILED if short else OK
            detail = ('applied halfway, so run the migration again: ' + ', '.join(f'{k} has it on {v}' for k, v in short.items())) if short \
                else 'every table has it (' + ', '.join(f'{schema} {tables}' for schema, (tables, _) in have.items()) + ')'
    except Exception as exc:   # noqa: BLE001 - reported, like every other check
        status, detail = FAILED, f'{type(exc).__name__}: {str(exc)[:240]}'
    return {'name': 'imported_at', 'status': status, 'detail': detail, 'seconds': clock() - started}


def run_checks(get_section, today=None, days=90, clock=time.monotonic, query=None):
    """A list of {name, status, detail, seconds}. Never raises: a section that fails is reported as FAILED with its error."""
    today = today or date.today()
    results = []

    def check(name, summarise, *args, **kwargs):
        started = clock()
        try:
            payload = get_section(name, *args, **kwargs)
            if payload is None:
                raise LookupError('the section answered "not found"')
            count, detail = summarise(payload)
            results.append({'name': name, 'status': OK if count else EMPTY, 'detail': detail, 'seconds': clock() - started})
            return payload
        except Exception as exc:   # noqa: BLE001 - the point is to report every kind of failure, not to stop at the first
            results.append({'name': name, 'status': FAILED, 'detail': f'{type(exc).__name__}: {str(exc)[:240]}', 'seconds': clock() - started})
            return None

    def skip(name, why):
        results.append({'name': name, 'status': SKIPPED, 'detail': why, 'seconds': 0.0})

    check('tickets', _tickets, today - timedelta(days=days), today)
    patches = check('patches', _patches)
    check('patch-overview', _overview)
    first = next(((t['key'], c['category_id']) for t in (patches or {'types': []})['types'] for c in t['categories'] if c['total']), None)
    if first:
        check('patch-entries', _entries, params={'type': first[0], **({'category': str(first[1])} if first[1] is not None else {})})
    else:
        skip('patch-entries', 'no patch category to open')
    check('patch-trend', _trend, params={})
    check('server-health', _servers)
    backups = check('backups', _backups)
    device = next((r['device_id'] for r in (backups or {'rows': []})['rows']), None)
    if device is not None:
        check('backup-detail', _backup_detail, params={'device': str(device)})
    else:
        skip('backup-detail', 'no backup device to open')
    if query is not None:   # given when checking the real databases, so the migration's state is shown with the rest
        results.append(check_imported_at(query, clock))
    return results


def summary(results):
    counts = {status: sum(1 for r in results if r['status'] == status) for status in (OK, EMPTY, FAILED, SKIPPED)}
    return counts, f"{counts[OK]} OK, {counts[EMPTY]} empty, {counts[FAILED]} failed, {counts[SKIPPED]} skipped"
