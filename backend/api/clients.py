"""The master client list: which customers in which source systems belong to which client.

Rule: a source customer is matched to a client by that system's own ID (ClientSourceMap). A name is only a fallback,
and only through an explicit alias (or the client's own name). Names alone are never a join key in SQL.
No ORM import at module level, so the pure parts can be tested without a database."""
import re

SOURCES = ('autotask', 'ncentral', 'sentinelone', 'cove')
SOURCE_LABELS = {'autotask': 'Autotask', 'ncentral': 'N-central', 'sentinelone': 'SentinelOne', 'cove': 'Cove'}

# Where each source keeps its customer list: (stable ID, name). Read-only, static SQL.
SOURCE_CUSTOMER_SQL = {
    'autotask': 'SELECT autotask_company_id AS external_id, customer_name FROM autotask.customers '
                'WHERE autotask_company_id IS NOT NULL AND is_active',
    'ncentral': 'SELECT org_unit_id AS external_id, customer_name FROM ncentral.customers WHERE is_active',
    'sentinelone': 'SELECT site_id AS external_id, customer_name FROM sentinelone.customers WHERE is_active',
    'cove': 'SELECT id AS external_id, customer_name FROM cove.customers WHERE is_active',
}


def normalise(name):
    """'IP Capital:  BITCO' and 'ip capital bitco' compare equal. Letters and digits only, case-folded."""
    return re.sub(r'[\W_]+', ' ', str(name or '').casefold()).strip()


class Resolver:
    """mapping: {(source, 'external id'): client_id}; names: {normalised name or alias: client_id}."""

    def __init__(self, mapping=None, names=None):
        self.mapping = mapping or {}
        self.names = names or {}

    def client_id(self, source, external_id=None, name=None):
        """The client for a source customer: ID mapping first, then name or alias, else None (unmapped)."""
        if external_id is not None:
            found = self.mapping.get((source, str(external_id)))
            if found is not None:
                return found
        return self.names.get(normalise(name)) if name else None


def tokenise(name):
    """The words of a name, lower-cased, punctuation ignored, in order. 'IP Capital: BITCO' and 'IP Capital (BitCo)' both
    give ('ip', 'capital', 'bitco'). One extra, missing or reordered word gives a different result, which is the rule:
    names group only when they have exactly the same words in the same order."""
    return tuple(normalise(name).split())


def customer_names(rows):
    """{(source, external ID as text): customer name} from what source_customers() read, so a client's linked customer can be shown by name and not only by ID.
    A customer with no name is left out, and a source that could not be read contributes nothing (its links then have no name)."""
    return {(source, str(r['external_id'])): r['customer_name'] for source, items in rows.items() for r in items if r.get('customer_name')}


def _label_list(sources):
    return ', '.join(SOURCE_LABELS[s] for s in sources)


def _display_name(member):
    return member['customer_name'] or f"{SOURCE_LABELS[member['source']]} {member['external_id']}"  # a customer with no name


def group_customers(source_rows, mapped, known, client_sources, available):
    """Propose clients from customers that are not linked yet, by grouping identical token sequences across all sources.

    source_rows: {source: [{external_id, customer_name}]}. mapped: {(source, id)} already linked. known: {tokens: (client id,
    client name)} for existing client names and aliases. client_sources: {client id: {sources it is linked in}}.
    available: the sources that could be read. Every proposal is an inference from names; a person confirms it, and
    confirming links by ID. A name with no usable words is never grouped with anything."""
    groups = {}
    for source in SOURCES:
        for row in source_rows.get(source, []):
            external_id = str(row['external_id'])
            if (source, external_id) in mapped:
                continue
            tokens = tokenise(row['customer_name'])
            key = tokens or (f'#{source}:{external_id}',)
            groups.setdefault(key, {'tokens': tokens, 'members': []})['members'].append(
                {'source': source, 'external_id': external_id, 'customer_name': row['customer_name']})

    proposals = []
    for key, group in groups.items():
        members, tokens = group['members'], group['tokens']
        existing = known.get(tokens) if tokens else None
        found_in = {m['source'] for m in members} | (client_sources.get(existing[0], set()) if existing else set())
        missing = [s for s in available if s not in found_in]
        names = sorted({m['customer_name'] for m in members if m['customer_name']}, key=str.casefold)
        repeated = sorted({m['source'] for m in members if sum(x['source'] == m['source'] for x in members) > 1}, key=SOURCES.index)
        if existing:
            message = f'The name matches the existing client "{existing[1]}" (inference). Confirm to link it by ID.'
        elif len(members) > 1:
            message = f'Grouped by matching name only (inference): {", ".join(names)}. Check these are the same company.'
        else:
            message = None
        proposals.append({
            'key': ' '.join(tokens) or key[0], 'name': existing[1] if existing else _display_name(members[0]),
            'members': members, 'existing_client_id': existing[0] if existing else None,
            'existing_client_name': existing[1] if existing else None,
            'inferred': bool(existing) or len(members) > 1, 'message': message,
            'warnings': [f'More than one {SOURCE_LABELS[s]} customer has this name; they may be different companies.' for s in repeated],
            'found_in': [s for s in SOURCES if s in found_in], 'missing_from': missing,
            'discrepancy': f'Found in {_label_list([s for s in SOURCES if s in found_in])}. Not found in {_label_list(missing)}.' if missing else None,
        })
    return sorted(proposals, key=lambda p: p['name'].casefold())


def client_discrepancies(clients, source_rows, available):
    """For each master client: which sources it is missing from, and which of its links point at a customer that no longer
    exists. Inactive clients are not expected to be everywhere, so a missing source is not reported for them."""
    ids = {s: {str(r['external_id']) for r in source_rows.get(s, [])} for s in available}
    rows = []
    for client in clients:
        linked = {link['source'] for link in client['sources']}
        missing = [s for s in available if s not in linked] if client['is_active'] else []
        stale = [link for link in client['sources'] if link['source'] in ids and link['external_id'] not in ids[link['source']]]
        messages = [f'No {SOURCE_LABELS[s]} customer is linked to this client.' for s in missing]
        messages += [f"{SOURCE_LABELS[l['source']]} {l['external_id']} is linked but no longer exists in {SOURCE_LABELS[l['source']]}." for l in stale]
        rows.append({'id': client['id'], 'name': client['name'], 'is_active': client['is_active'],
                     'linked_in': [s for s in SOURCES if s in linked], 'missing_from': missing, 'stale': stale, 'discrepancies': messages})
    return rows


def load_resolver():
    from .models import Client, ClientAlias, ClientSourceMap
    mapping = {(s, e): c for s, e, c in ClientSourceMap.objects.values_list('source', 'external_id', 'client_id')}
    names = {normalise(n): c for n, c in Client.objects.values_list('name', 'id')}
    names.update({a: c for a, c in ClientAlias.objects.values_list('alias_norm', 'client_id')})
    return Resolver(mapping, names)


def source_customers():
    """Returns ({source: rows}, [sources that could not be read]). A missing schema is reported, not fatal."""
    from .data import common
    rows, unavailable = {}, []
    for source, sql in SOURCE_CUSTOMER_SQL.items():
        try:
            rows[source] = common.query(sql)
        except Exception:
            unavailable.append(source)
    return rows, unavailable


def parse_ids(value, limit=50):
    """The whole numbers in "5" or "1,2,3" (repeats dropped, order kept); nothing for None or "". ValueError for anything else, or for
    more than `limit` of them. This is how a request names several clients or customers at once."""
    if value is None or value == '':
        return []
    text = str(value)
    if not re.fullmatch(r'\d{1,12}(,\d{1,12})*', text):
        raise ValueError('a number, or numbers separated by commas')
    ids = list(dict.fromkeys(int(part) for part in text.split(',')))
    if len(ids) > limit:
        raise ValueError(f'at most {limit} values')
    return ids


def org_units_for(client_id):
    """The N-central org units (integers) linked to a client."""
    from .models import ClientSourceMap
    units = []
    for external_id in ClientSourceMap.objects.filter(client_id=client_id, source='ncentral').values_list('external_id', flat=True):
        try:
            units.append(int(external_id))
        except ValueError:
            continue
    return units


def ncentral_scopes():
    """{client_id: [org unit ids]} for every client linked to N-central. Used to write per-client score snapshots."""
    from .models import ClientSourceMap
    scopes = {}
    for client_id, external_id in ClientSourceMap.objects.filter(source='ncentral').values_list('client_id', 'external_id'):
        try:
            scopes.setdefault(client_id, []).append(int(external_id))
        except ValueError:
            continue
    return scopes


def device_customers():
    """The N-central customer each device belongs to, with device counts. This is where the Patches client list comes from,
    so it works before any master client has been set up."""
    from .data import common
    return common.query('SELECT org_unit_id, customer_name, COUNT(*) AS devices FROM ncentral.device '
                        'WHERE org_unit_id IS NOT NULL GROUP BY org_unit_id, customer_name')


def ncentral_org_map():
    """{org unit id: (client id, client name)} for org units linked to a master client."""
    from .models import Client, ClientSourceMap
    links = dict(ClientSourceMap.objects.filter(source='ncentral').values_list('external_id', 'client_id'))
    names = dict(Client.objects.filter(pk__in=set(links.values())).values_list('id', 'name'))
    return {org: (client_id, names[client_id]) for org, client_id in links.items() if client_id in names}


def build_client_options(rows, org_map):
    """Choices for the Patches client filter, built from where devices actually live. Org units linked to a master
    client are merged under that client's name; the rest appear under their N-central customer name."""
    orgs = {}
    for row in rows:
        devices = int(row['devices'])
        org = orgs.setdefault(str(row['org_unit_id']), {'devices': 0, 'name': None, 'top': -1})
        org['devices'] += devices
        if row['customer_name'] and devices > org['top']:  # a renamed customer: keep the name most devices carry
            org['name'], org['top'] = row['customer_name'], devices
    options = {}
    for org_id, org in orgs.items():
        if org_id in org_map:
            client_id, name = org_map[org_id]
            option = options.setdefault(f'client:{client_id}', {'key': f'client:{client_id}', 'label': name, 'client': client_id,
                                                                 'org': None, 'linked': True, 'devices': 0})
            option['devices'] += org['devices']
        else:
            options[f'org:{org_id}'] = {'key': f'org:{org_id}', 'label': org['name'] or f'Org unit {org_id}', 'client': None,
                                        'org': int(org_id), 'linked': False, 'devices': org['devices']}
    return sorted(options.values(), key=lambda o: o['label'].casefold())
