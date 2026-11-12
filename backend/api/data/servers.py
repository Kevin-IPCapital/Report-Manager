"""SentinelOne endpoint health, with each server's patching from N-central."""
import logging
import re
from datetime import datetime

from .. import clients
from . import backups, common as c, patches

log = logging.getLogger(__name__)

# A patch that has been missing for longer than this is one that was expected and has not been applied. There is no patch schedule in
# the collected data, so this one grace period (for all three categories) stands in for "expected".
PATCH_GRACE_DAYS = 14

# Latest row per (customer, agent), then drop agents whose newest state is decommissioned.
# `is_active` is returned because the Reports page counts active agents.
_AGENT_SQL = """SELECT a.agent_id, a.customer_name, a.site_id, a.computer_name, a.network_status,
                       a.machine_type, a.os_type, a.agent_version, a.mitigation_mode,
                       a.is_up_to_date, a.infected, a.is_active, a.is_decommissioned, s.captured_at
                FROM (SELECT DISTINCT ON (customer_name, agent_id) *
                      FROM sentinelone.agent
                      ORDER BY customer_name, agent_id, snapshot_id DESC) a
                JOIN sentinelone.snapshot s ON s.snapshot_id = a.snapshot_id
                WHERE NOT COALESCE(a.is_decommissioned, FALSE)"""


def server_health():
    """Latest SentinelOne agent snapshot per customer/device, with age of source capture."""
    now = datetime.now(c.UTC)
    resolver = clients.load_resolver()
    servers = [{'agent_id': a['agent_id'], 'customer_name': a['customer_name'],
                'computer_name': a['computer_name'], 'network_status': a['network_status'],
                'last_collected_at': c.iso(a['captured_at']),
                'data_age_hours': c.hours(c.aware(a['captured_at']), now),
                'machine_type': a['machine_type'], 'os_type': a['os_type'],
                'agent_version': a['agent_version'], 'mitigation_mode': a['mitigation_mode'],
                'is_up_to_date': a['is_up_to_date'], 'infected': a['infected'], 'is_active': a['is_active'],
                'client_id': resolver.client_id('sentinelone', a['site_id'], a['customer_name'])}
               for a in c.query(_AGENT_SQL)]
    summary = _patching(servers, now)
    collected = collected_summary(servers, summary, _cove(), now)
    for server in servers:
        server['patches'] = summary[server['agent_id']] if summary is not None else {'status': 'unavailable'}
        server['collected'] = collected[server['agent_id']]
    return {'servers': sorted(servers, key=lambda a: (a['customer_name'] or '', a['computer_name'] or '')),
            'patch_info': {'available': summary is not None, 'grace_days': PATCH_GRACE_DAYS,
                           'matched': sum(1 for p in (summary or {}).values() if p['status'] == 'matched'),
                           'disconnected': sum(1 for p in (summary or {}).values() if p['status'] == 'matched' and p['collection']['state'] == 'disconnected')}}


# ---- patching: the N-central device behind a SentinelOne endpoint ----
# The two systems share no ID, only a computer name, so a server is matched to a device by NAME INSIDE ITS OWN CLIENT (the client comes from
# each system's own customer link). Anything less certain shows no patch data, never a guess. The comment at the start of each query is a
# marker, so a test double can recognise it.
_DEVICES_SQL = '/* server-patch-devices */ SELECT device_id, device_name, org_unit_id, last_seen FROM ncentral.device WHERE device_name IS NOT NULL'
# each device's latest patch collection attempt, good or bad: this is how a device N-central cannot find (404) is known
_ATTEMPT_SQL = ('/* server-patch-latest */ SELECT DISTINCT ON (d.device_id) d.device_id, d.captured_at, d.coverage, d.error FROM ncentral.device_patch d '
                'JOIN ncentral.patch_run r ON r.run_id = d.run_id WHERE r.finished_at IS NOT NULL ORDER BY d.device_id, d.run_id DESC')
_BACKLOG_SQL = (f'/* server-patch-backlog */ SELECT device_id, {patches._TYPE_SQL} AS patch_type, COUNT(*) AS missing, MIN(first_missing_at) AS oldest_missing_at '
                'FROM ncentral.patch_backlog WHERE resolved_at IS NULL GROUP BY 1, 2')


def _installed_sql():
    return (f'/* server-patch-installed */ WITH latest AS ({patches.inventory_query(1)}) SELECT device_id, {patches._TYPE_SQL} AS patch_type, '
            'MAX(installed_at) FILTER (WHERE NOT is_missing) AS last_installed_at, COUNT(*) FILTER (WHERE is_missing) AS missing_now, COUNT(*) AS entries '
            'FROM latest GROUP BY 1, 2')


def _good_sql():
    """When each device's newest USABLE patch inventory was captured (the same rule as the Overview: a device that answered with an error, or
    as unavailable, has no inventory for that run)."""
    return ('/* server-patch-good */ ' + patches._inventory_cte() + ' SELECT d.device_id, d.captured_at FROM ncentral.device_patch d '
            'JOIN successful_inventories i ON (i.run_id = d.run_id AND i.device_id = d.device_id) WHERE i.inventory_rank = 1')


DISCONNECTED = re.compile(r'404|DeviceNotFound', re.I)


def collection_state(attempt):
    """How the newest patch collection went for one device: `ok`; `disconnected` (it could not be used and the reason is that N-central cannot
    find the device: a 404, DeviceNotFoundException, decided 12 Nov 2026, NC-01, to count as disconnected); `failed` (unusable for any other
    reason); or None (it has never been tried). Unusable means exactly what the Overview's rule means: an error, or coverage `unavailable`."""
    if not attempt:
        return None
    if attempt.get('error') is None and attempt.get('coverage') != 'unavailable':
        return 'ok'
    return 'disconnected' if DISCONNECTED.search(str(attempt.get('error') or '')) else 'failed'


def device_key(name):
    """What two spellings of one computer name have in common: lower case, without a domain ending (srv01.corp.local is srv01), unless the
    name is an IP address, where the dots are the address and not a domain."""
    text = (name or '').strip().lower()
    if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', text):
        return text
    return text.split('.')[0]


def _when(value, now, key='at'):
    """{at, days_ago} for a time, or None when there is none."""
    value = c.aware(value)
    return {key: c.iso(value), 'days_ago': _days(now, value)} if value else None


def _days(now, then):
    return round((now - then).total_seconds() / 86400, 1) if then else None


def patch_summary(servers, devices, installed, backlog, org_map, now, grace_days=PATCH_GRACE_DAYS, attempts=(), good=()):
    """Pure. For each server, {agent_id: {status, device_name, categories}}.

    status: `matched`, `unlinked` (the server is linked to no client, so there is nothing safe to match inside), `no-match` (no device with
    that name in its client) or `ambiguous` (more than one has it; none is guessed). `devices` are N-central devices (device_id, device_name,
    org_unit_id), `org_map` maps an org unit to (client ID, name), `installed` has each device's newest install date per category and
    `backlog` its open missing patches per category with the oldest first-missing time. For each of the three Overview categories a matched
    server gets: `last_installed_at` and `days_since_last` (the last added); `next_due` (None: no schedule is collected); `up_to_date` (True
    when its newest patch inventory lists patches of that kind and none is missing, False when some are, None when it lists none, so nothing
    can be said); `missing`, counted in that same inventory like the Overview does; `oldest_missing_days`, from the backlog; and `overdue`: some
    patch is missing and the oldest has been for more than `grace_days`. No overall "healthy" is decided here: that is for the owner to define.

    A matched server also says how its device's newest patch collection went (`collection.state`: ok, disconnected, failed, or None; see
    collection_state), and when N-central last saw the device (`seen`) and when its newest usable patch inventory was captured (`inventory`),
    each as {at, days_ago} or None. `attempts` and `good` are those two things per device."""
    by_name = {}
    for device in devices:
        client_id = (org_map.get(str(device['org_unit_id'])) or (None,))[0]
        key = device_key(device['device_name'])
        if client_id is not None and key:
            by_name.setdefault((client_id, key), []).append(device)
    inventory = {(r['device_id'], r['patch_type']): r for r in installed}
    missing = {(r['device_id'], r['patch_type']): r for r in backlog}
    latest = {r['device_id']: r for r in attempts}
    usable = {r['device_id']: r['captured_at'] for r in good}
    result = {}
    for server in servers:
        client_id, key = server.get('client_id'), device_key(server.get('computer_name'))
        found = by_name.get((client_id, key), []) if client_id is not None and key else []
        if client_id is None:
            result[server['agent_id']] = {'status': 'unlinked'}
        elif not found:
            result[server['agent_id']] = {'status': 'no-match'}
        elif len(found) > 1:
            result[server['agent_id']] = {'status': 'ambiguous'}
        else:
            device_id = found[0]['device_id']
            categories = []
            for kind, label in patches._PATCH_TYPES:
                seen = inventory.get((device_id, kind))
                installed_at = c.aware(seen['last_installed_at']) if seen else None
                missing_now = int(seen.get('missing_now') or 0) if seen else 0
                row = missing.get((device_id, kind))
                oldest = c.aware(row['oldest_missing_at']) if row else None
                oldest_days = _days(now, oldest)
                categories.append({'key': kind, 'label': label, 'last_installed_at': c.iso(installed_at), 'days_since_last': _days(now, installed_at), 'next_due': None,
                                   'up_to_date': (missing_now == 0) if seen and seen.get('missing_now') is not None else None, 'missing': missing_now,
                                   'oldest_missing_days': oldest_days, 'overdue': missing_now > 0 and oldest_days is not None and oldest_days > grace_days})
            attempt = latest.get(device_id)
            result[server['agent_id']] = {'status': 'matched', 'device_name': found[0]['device_name'], 'categories': categories,
                                          'collection': {'state': collection_state(attempt), **(_when(attempt['captured_at'], now, 'attempted_at') if attempt else {'attempted_at': None, 'days_ago': None})},
                                          'seen': _when(found[0].get('last_seen'), now), 'inventory': _when(usable.get(device_id), now)}
    return result


def _item(key, label, what, state, when=None):
    return {'key': key, 'label': label, 'what': what, 'state': state, 'at': when['at'] if when else None, 'days_ago': when['days_ago'] if when else None}


def collected_summary(servers, patching, cove, now):
    """Pure. For each server, {agent_id: [items]}: the latest date found for that device on a successful run of each collector.

    SentinelOne: when its agent record was captured (the server is from there, so it is always found). N-central: when the device was last
    seen by the inventory collector, and when its newest usable patch inventory was captured (a device N-central cannot find keeps the date of
    its last SUCCESSFUL one: the failed attempts are not dates its data was collected). Cove: when the matching backup device was captured,
    matched like the N-central device, by name inside the same client. Each item's `state` is `found`; `none` (matched, but no date has been
    recorded); `no-match`, `ambiguous` or `unlinked` (see patch_summary); `not-collected` (Cove has collected nothing yet); or `unavailable`
    (it could not be read). `patching` is patch_summary's answer, or None if it could not be read; `cove` is {'state': ok | not-collected |
    unavailable, 'rows': the backup devices}."""
    by_name = {}
    for row in cove['rows']:
        for name in {row.get('device_name'), row.get('hostname')}:
            key = device_key(name)
            if row.get('client_id') is not None and key:
                by_name.setdefault((row['client_id'], key), {})[(row.get('snapshot_id'), row.get('device_id'))] = row
    result = {}
    for server in servers:
        hours = server.get('data_age_hours')
        sentinel = {'at': server['last_collected_at'], 'days_ago': round(hours / 24, 1)} if server.get('last_collected_at') and hours is not None else None
        items = [_item('sentinelone', 'SentinelOne', 'Security agent', 'found' if sentinel else 'none', sentinel)]
        p = patching.get(server['agent_id']) if patching is not None else {'status': 'unavailable'}
        if p['status'] == 'matched':
            for key, what, when in (('ncentral-device', 'Device inventory', p['seen']), ('ncentral-patches', 'Patch inventory', p['inventory'])):
                items.append(_item(key, 'N-central', what, 'found' if when else 'none', when))
        else:
            items += [_item('ncentral-device', 'N-central', 'Device inventory', p['status']), _item('ncentral-patches', 'N-central', 'Patch inventory', p['status'])]
        client_id, key = server.get('client_id'), device_key(server.get('computer_name'))
        if cove['state'] != 'ok':
            items.append(_item('cove', 'Cove', 'Backup', cove['state']))
        elif client_id is None:
            items.append(_item('cove', 'Cove', 'Backup', 'unlinked'))
        else:
            found = list(by_name.get((client_id, key), {}).values()) if key else []
            if len(found) == 1:
                row = found[0]
                when = {'at': row['captured_at'], 'days_ago': round(row['data_age_hours'] / 24, 1)} if row.get('captured_at') and row.get('data_age_hours') is not None else None
                items.append(_item('cove', 'Cove', 'Backup', 'found' if when else 'none', when))
            else:
                items.append(_item('cove', 'Cove', 'Backup', 'ambiguous' if found else 'no-match'))
        result[server['agent_id']] = items
    return result


def _cove():
    """The Cove backup devices, or a state saying why not. The page must still load, so a problem is logged, never raised or sent."""
    try:
        body = backups.backups()
        return {'state': 'ok' if body.get('available') and body.get('loaded') else 'not-collected', 'rows': body.get('rows', [])}
    except Exception:
        log.exception('Could not read the Cove backup devices for the servers')
        return {'state': 'unavailable', 'rows': []}


def _patching(servers, now):
    """The patching of every server, or None if it cannot be read: the page must still load, so a problem here is logged and the page
    simply shows no patch data. (The reason is not sent to the browser: it can name tables.)"""
    try:
        return patch_summary(servers, c.query(_DEVICES_SQL), c.query(_installed_sql()), c.query(_BACKLOG_SQL), clients.ncentral_org_map(), now,
                             attempts=c.query(_ATTEMPT_SQL), good=c.query(_good_sql()))
    except Exception:
        log.exception('Could not read the patching of the servers')
        return None
