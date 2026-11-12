"""N-central patch compliance."""
from .. import access, clients, scoring
from . import common as c

PATCH_TABLE = 'ncentral.patch_entry'
_PATCH_COLUMNS = ('run_id', 'device_id', 'title', 'category', 'category_id', 'result', 'is_missing', 'is_security',
                  'is_third_party', 'installed_at', 'kb')
_PATCH_COLS = ', '.join(_PATCH_COLUMNS)


def _inventory_cte(as_of=None):
    """Only a usable device inventory is eligible: don't infer that 404 / failed device fetches mean patches have
    been removed. Runs can be partial (ok=false), so check individual device results rather than discarding all
    valid devices in that run. `as_of` ignores runs after that run ID, to rebuild the position at that time."""
    later = f'\n      AND d.run_id <= {int(as_of)}' if as_of is not None else ''
    return f"""WITH successful_inventories AS (
    SELECT d.run_id, d.device_id,
           ROW_NUMBER() OVER (PARTITION BY d.device_id ORDER BY d.run_id DESC) AS inventory_rank
    FROM ncentral.device_patch d
    JOIN ncentral.patch_run r ON r.run_id = d.run_id
    WHERE r.finished_at IS NOT NULL
      AND d.error IS NULL
      AND d.coverage IS DISTINCT FROM 'unavailable'{later}
)"""


def _id_list(values):
    return ', '.join(str(int(v)) for v in values)  # every value is cast to int: nothing user-typed reaches the SQL


def inventory_query(rank, as_of=None, org_units=None, with_org=False):
    """Rank complete device inventories, not individual patch-entry rows. Some devices have no patch entries, and
    should not corrupt the ranking of other devices. `org_units` limits the devices to those customers' org units
    (an empty list matches nothing). `with_org` also returns each row's customer (org unit)."""
    join = scope = ''
    if org_units is not None or with_org:
        join = ' JOIN ncentral.device dev ON dev.device_id = p.device_id'
    if org_units is not None:
        scope = f' AND dev.org_unit_id IN ({_id_list(org_units)})' if org_units else ' AND FALSE'
    return (f'{_inventory_cte(as_of)} '
            f'SELECT {", ".join("p." + col for col in _PATCH_COLUMNS)}{", dev.org_unit_id" if with_org else ""} '
            f'FROM ncentral.patch_entry p JOIN successful_inventories i '
            f'ON (i.run_id = p.run_id AND i.device_id = p.device_id){join} '
            f'WHERE i.inventory_rank = {int(rank)}{scope}')


_TYPE_SQL = "CASE WHEN is_third_party THEN 'third-party' WHEN is_security THEN 'windows-security' ELSE 'windows-patching' END"
_PATCH_TYPES = [('windows-security', 'Windows Security Patching'), ('windows-patching', 'Windows Patching'),
                ('third-party', 'Third Party Patching')]


def groups(previous=False, as_of=None, org_units=None):
    """Installed/missing totals per patch type and category, from each device's newest (or second-newest) inventory."""
    inventory = inventory_query(2 if previous else 1, as_of=as_of, org_units=org_units)
    return c.query(f'WITH latest AS ({inventory}) '
                   f'SELECT {_TYPE_SQL} AS patch_type, category_id, category, COUNT(*) AS total, '
                   f'COUNT(*) FILTER (WHERE is_missing) AS missing FROM latest GROUP BY 1, 2, 3')


def groups_by_org(previous=False, org_units=None):
    """The same totals as groups(), but per customer (org unit): one query for every client at once, not one query per client."""
    inventory = inventory_query(2 if previous else 1, org_units=org_units, with_org=True)
    return c.query(f'WITH latest AS ({inventory}) '
                   f'SELECT org_unit_id, {_TYPE_SQL} AS patch_type, category_id, category, COUNT(*) AS total, '
                   f'COUNT(*) FILTER (WHERE is_missing) AS missing FROM latest GROUP BY 1, 2, 3, 4')


def _merge_by_client(rows, key_of):
    """{client key: [group dicts]}: the groups of every org unit that belongs to the same client are added together (counts, not
    averages, so compliance is recomputed from the sums). A device with no customer cannot be placed and is left out."""
    merged = {}
    for r in rows:
        if r['org_unit_id'] is None:
            continue
        slot = merged.setdefault(key_of(r['org_unit_id']), {})
        g = slot.setdefault((r['patch_type'], r['category_id'], r['category']),
                            {'patch_type': r['patch_type'], 'category_id': r['category_id'], 'category': r['category'], 'total': 0, 'missing': 0})
        g['total'] += int(r['total'])
        g['missing'] += int(r['missing'])
    return {key: list(slot.values()) for key, slot in merged.items()}


def _compliance(g):
    return round((g['total'] - g['missing']) / g['total'] * 100, 2) if g and g['total'] else None


def build_types(now, before):
    """Type -> categories with counts and compliance; `previous` comes from the earlier inventory (None if absent)."""
    previous = {(g['patch_type'], g['category_id']): g for g in before}
    types = []
    for key, label in _PATCH_TYPES:
        found = sorted((g for g in now if g['patch_type'] == key), key=lambda g: g['category'] or '')
        types.append({'key': key, 'label': label, 'categories': [
            {'category_id': g['category_id'], 'category': g['category'] or 'Uncategorised', 'installed': g['total'] - g['missing'],
             'missing': g['missing'], 'total': g['total'], 'compliance': _compliance(g),
             'previous': _compliance(previous.get((key, g['category_id'])))} for g in found]})
    return types


def _scope(params, allowed=None):
    """The N-central org units a request is limited to, or None for all devices. `client` is one or several master clients, comma
    separated (all the org units linked to each); `org` is one or several N-central customers, for customers not linked to a client yet.
    Both may be given and then add together. `allowed` (client IDs, or None for no limit) narrows it further to the customers of those
    clients, so someone limited to some clients never reads anyone else's devices."""
    client_ids, orgs = clients.parse_ids(params.get('client')), clients.parse_ids(params.get('org'))
    if client_ids or orgs:
        requested = sorted({unit for client_id in client_ids for unit in clients.org_units_for(client_id)} | set(orgs))
    else:
        requested = None
    if allowed is None:
        return requested
    mine = access.org_units_of(allowed)
    return mine if requested is None else [unit for unit in requested if unit in mine]


def patches(params=None, user=None, allowed=None):
    """Counts and compliance per category, plus the scores computed with this user's weights and the choices for the
    client filter. `mapped` is false when the chosen client has no N-central customer linked."""
    params = params or {}
    org_units = _scope(params, allowed)
    types = build_types(groups(org_units=org_units), groups(previous=True, org_units=org_units))
    override = params.get('weights')   # a dict, already checked by the view: a saved report's own weights
    weights = scoring.report_weights(override) if override else scoring.weights_for(user)
    rows, overall = scoring.compute_scores(types, weights)
    return {'types': types, 'rows': rows, 'overall': overall, 'calculation': scoring.explain(types, weights), 'client': params.get('client') or None, 'org': params.get('org') or None,
            'mapped': org_units is None or bool(org_units),
            'clients': [o for o in clients.build_client_options(clients.device_customers(), clients.ncentral_org_map()) if access.option_allowed(o, allowed)],
            'columns': [c.col('category', 'Category', type='link'), c.col('installed', 'Installed'), c.col('missing', 'Missing'),
                        c.col('total', 'Total'), c.col('compliance', 'Compliance', type='percent')]}


def overview(params=None, user=None, allowed=None):
    """Every client's own scores for the Overview: the three patch types and the overall score, with this person's weights, one group
    per client (customers not linked to a client appear under their N-central name). Ordered by name, not by how well they are doing.
    `allowed` (client IDs, or None for no limit) leaves out clients the person may not see, and the query itself is limited to their
    customers so nothing about anyone else is read."""
    org_map = clients.ncentral_org_map()
    options = clients.build_client_options(clients.device_customers(), org_map)
    org_units = None
    if allowed is not None:
        options = [o for o in options if access.option_allowed(o, allowed)]
        org_units = access.org_units_of(allowed)
    key_of = lambda org: f'client:{org_map[str(org)][0]}' if str(org) in org_map else f'org:{org}'   # noqa: E731
    now = _merge_by_client(groups_by_org(org_units=org_units), key_of)
    before = _merge_by_client(groups_by_org(previous=True, org_units=org_units), key_of)
    weights = scoring.weights_for(user)
    result = []
    for o in options:
        types = build_types(now.get(o['key'], []), before.get(o['key'], []))
        rows, overall = scoring.compute_scores(types, weights)
        result.append({'key': o['key'], 'label': o['label'], 'client': o['client'], 'org': o['org'], 'linked': o['linked'], 'devices': o['devices'],
                       'rows': [r for r in rows if not r.get('group')], 'overall': overall})
    return {'clients': result}


def patch_entries(params, allowed=None):
    """The patches of one type and category (latest run per device), fetched when that level is opened."""
    ptype, category_id = params.get('type'), params.get('category')
    if ptype not in dict(_PATCH_TYPES):
        return None
    inventory = inventory_query(1, org_units=_scope(params, allowed))
    rows = c.query(f'WITH latest AS ({inventory}), '
                   f'named AS (SELECT l.*, d.device_name FROM latest l LEFT JOIN ncentral.device d ON d.device_id = l.device_id) '
                   f'SELECT {_PATCH_COLS}, device_name FROM named WHERE {_TYPE_SQL} = %s '
                   f'AND category_id::text IS NOT DISTINCT FROM %s ORDER BY is_missing DESC NULLS LAST, title',
                   [ptype, category_id])
    rows = [{**r, 'device': r['device_name'] or str(r['device_id']),  # the name, falling back to the ID
             'installed_at': c.iso(r['installed_at']), 'installed': None if r['is_missing'] is None else not r['is_missing']}
            for r in rows]  # installed and missing are exact opposites
    return {'entries': {
        'title': 'Patches',
        'columns': [c.col('device', 'Device'), c.col('title', 'Title', wrap=True), c.col('kb', 'KB'),
                    c.col('installed', 'Installed', type='bool')],
        'rows': rows,
    }}
