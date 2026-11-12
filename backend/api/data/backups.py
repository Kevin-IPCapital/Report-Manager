"""Cove backup health.

Which rows count as "current": each load of the Cove collector is a snapshot, and `load-active` makes one per customer
(`customer_filter`). A device is read from the newest snapshot of its load scope, so a device Cove no longer reports stops
appearing once its customer is loaded again, instead of staying forever in an old snapshot. If a device is in several of those
snapshots (a whole-estate load as well as a per-customer one) the newest wins.

`health` and the red flags are decided by the collector, not here: CRITICAL when a flag reports no successful backup or a
failed, aborted or interrupted session; WARNING for any other flag; OK for none."""
from datetime import datetime

from .. import clients
from . import common as c

REQUIRED_TABLES = {'snapshot', 'device'}
SEVERITY = {'CRITICAL': 0, 'WARNING': 1, 'OK': 2}

# The newest snapshot of each load scope. `IS NOT DISTINCT FROM` so that the whole-estate scope (NULL) is a scope too.
_LATEST = """latest_snapshots AS (
    SELECT s.snapshot_id, s.captured_at FROM cove.snapshot s
    WHERE s.snapshot_id = (SELECT MAX(x.snapshot_id) FROM cove.snapshot x WHERE x.customer_filter IS NOT DISTINCT FROM s.customer_filter)
)"""
_DEVICE_COLUMNS = ('snapshot_id', 'device_id', 'device_name', 'device_alias', 'customer', 'contact_email', 'hostname', 'os', 'os_type',
                   'account_type', 'client_version', 'client_old', 'os_eol', 'is_server', 'is_m365', 'used_storage_bytes',
                   'dedup_efficiency', 'storage_location', 'strict_location_policy', 'reporting_enabled', 'reporting_frequency',
                   'dr_testing', 'immutable_enabled', 'fips_enabled', 'passphrase_set', 'lsv_enabled', 'lsv_status', 'cloud_status',
                   'retention_days', 'last_success', 'days_since_success', 'health', 'red_flag_count', 'active_datasources')
_COLUMNS_SQL = ', '.join(f'd.{name}' for name in _DEVICE_COLUMNS)


def cove_tables():
    """The tables that exist in the cove schema."""
    found = c.query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'cove'")
    return {r['table_name'] for r in found}


def _device_type(d):
    return 'Microsoft 365' if d['is_m365'] else 'Server' if d['is_server'] else 'Workstation'


def _health(d):
    return str(d['health'] or 'OK').upper()


def _columns():
    return [c.col('customer', 'Customer'), c.col('device_name', 'Device'), c.col('device_type', 'Device Type'), c.col('health', 'Health', type='health'),
            c.col('red_flag_count', 'Red Flags'), c.col('last_success', 'Last Success', type='datetime'),
            c.col('days_since_success', 'Days Since Success'), c.col('used_storage_bytes', 'Used Storage', type='bytes'),
            c.col('retention_days', 'Retention (Days)'), c.col('cloud_status', 'Cloud Storage'), c.col('lsv_status', 'LSV'),
            c.col('immutable_enabled', 'Immutable', type='flag')]


def _cove_ids(tables):
    """{normalised customer name: Cove customer id}, so a device can be matched to a client through the Cove customer's own ID."""
    if 'customers' not in tables:
        return {}
    return {clients.normalise(r['customer_name']): r['id'] for r in c.query('SELECT id, customer_name FROM cove.customers')}


def _client_of(customer, resolver, cove_ids):
    # The Cove customer's own ID first, found by name; then the client's name or an alias. None when unmapped, never guessed.
    return resolver.client_id('cove', cove_ids.get(clients.normalise(customer)), customer)


def _row(d, flags, resolver, cove_ids, now):
    customer = d['customer'] or 'Unspecified'
    health = _health(d)
    return {
        'snapshot_id': d['snapshot_id'], 'device_id': d['device_id'], 'device_name': d['device_name'] or d['hostname'] or str(d['device_id']),
        'customer': customer,
        'client_id': _client_of(d['customer'], resolver, cove_ids),
        'hostname': d['hostname'], 'os_type': d['os_type'], 'account_type': d['account_type'], 'device_type': _device_type(d),
        'is_server': d['is_server'], 'is_m365': d['is_m365'], 'health': health,
        'red_flag_count': d['red_flag_count'], 'flags': flags, 'last_success': c.iso(d['last_success']),
        'days_since_success': d['days_since_success'], 'used_storage_bytes': d['used_storage_bytes'],
        'retention_days': d['retention_days'], 'lsv_enabled': d['lsv_enabled'], 'lsv_status': d['lsv_status'],
        'cloud_status': d['cloud_status'], 'immutable_enabled': d['immutable_enabled'], 'client_old': d['client_old'],
        'os_eol': d['os_eol'], 'active_datasources': d['active_datasources'], 'captured_at': c.iso(d['captured_at']),
        'data_age_hours': c.hours(c.aware(d['captured_at']), now),
        'rowStatus': {'CRITICAL': 'bad', 'WARNING': 'warn'}.get(health),
        'reason': '; '.join(f['flag'] for f in flags),
    }


def backups():
    """Every current Cove device with its health and red flags. `available` is false when the cove schema is not there at all;
    `loaded` is false when it is there but nothing has been collected yet."""
    tables = cove_tables()
    if not REQUIRED_TABLES <= tables:
        return {'available': False, 'loaded': False, 'title': 'Backup devices', 'columns': _columns(), 'rows': []}
    devices = c.query(f'WITH {_LATEST} SELECT DISTINCT ON (d.device_id) {_COLUMNS_SQL}, l.captured_at '
                      f'FROM cove.device d JOIN latest_snapshots l ON l.snapshot_id = d.snapshot_id '
                      f'ORDER BY d.device_id, d.snapshot_id DESC')
    flags = {}
    if 'red_flag' in tables and devices:
        for f in c.query(f'WITH {_LATEST} SELECT r.snapshot_id, r.device_id, r.category, r.flag FROM cove.red_flag r '
                         f'JOIN latest_snapshots l ON l.snapshot_id = r.snapshot_id ORDER BY r.red_flag_id'):
            flags.setdefault((f['snapshot_id'], f['device_id']), []).append({'category': f['category'] or 'session_failure', 'flag': f['flag']})
    cove_ids = _cove_ids(tables)
    resolver, now = clients.load_resolver(), datetime.now(c.UTC)
    rows = [_row({**d, 'captured_at': c.aware(d['captured_at'])}, flags.get((d['snapshot_id'], d['device_id']), []), resolver, cove_ids, now) for d in devices]
    rows.sort(key=lambda r: (SEVERITY.get(r['health'], 3), r['customer'].casefold(), r['device_name'].casefold()))
    return {'available': True, 'loaded': bool(rows), 'title': 'Backup devices', 'columns': _columns(), 'rows': rows}


def backup_detail(params):
    """One device with its data source sessions, Microsoft 365 sources and red flags, from the same snapshot the list uses."""
    tables = cove_tables()
    if not REQUIRED_TABLES <= tables:
        return None
    found = c.query(f'WITH {_LATEST} SELECT {_COLUMNS_SQL}, l.captured_at FROM cove.device d JOIN latest_snapshots l ON l.snapshot_id = d.snapshot_id '
                    f'WHERE d.device_id = %s ORDER BY d.snapshot_id DESC LIMIT 1', [int(params.get('device') or 0)])
    if not found:
        return None
    d = found[0]
    key = [d['snapshot_id'], d['device_id']]

    def fetch(table, columns, order):
        if table not in tables:
            return []
        rows = c.query(f'SELECT {columns} FROM cove.{table} WHERE snapshot_id = %s AND device_id = %s ORDER BY {order}', key)
        return [{k: c.iso(v) if isinstance(v, datetime) else v for k, v in r.items()} for r in rows]

    device = {k: c.iso(v) if isinstance(v, datetime) else v for k, v in d.items()}
    device['health'] = _health(d)
    device['device_type'] = _device_type(d)
    device['client_id'] = _client_of(d['customer'], clients.load_resolver(), _cove_ids(tables))   # used to limit who may open the device
    return {
        'device': device,
        'flags': fetch('red_flag', 'category, flag', 'red_flag_id'),
        'datasources': fetch('datasource_session', 'ds_code, ds_name, status, status_code, error_count, duration_seconds, sent_bytes, protected_bytes, '
                             'item_count, retention_days, last_success, last_session, ok_days, failed_days, warning_days, missed_days, max_fail_streak, '
                             'recurring_failures, chronic_warnings, chronic_missed, imm_active, imm_last_status', 'ds_name'),
        'm365': fetch('m365_source', 'source_label, enabled, coverage, status_code, last_success, item_count, user_count, ok_days, failed_days, missed_days', 'source_label'),
    }
