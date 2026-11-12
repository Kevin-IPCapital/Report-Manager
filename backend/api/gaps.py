"""A lasting record of the gaps between the source systems and the master client list, so the Client Resolution page can say how long a
client has been missing from a system, and what was resolved recently.

A gap is one of three kinds (see ClientGap). What is current comes from the same calculation the page already makes; this module keeps the
record: a gap seen for the first time is created, one still there is touched, and one that has gone is closed. A source that could not be
read this time cannot say anything, so its gaps are left open rather than wrongly closed. The planning is pure; only refresh_gaps writes."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import access
from .clients import SOURCE_LABELS

CHUNK = 500   # keys per query, so a long list never hits a database's limit on parameters


def row_keys(row):
    """The keys of a client's gaps, in the same order as the messages in row['discrepancies'] (missing sources first, then stale links)."""
    return ([f"missing:{row['id']}:{source}" for source in row['missing_from']]
            + [f"stale:{row['id']}:{link['source']}:{link['external_id']}" for link in row['stale']])


def proposal_key(source, external_id):
    return f'unlinked:{source}:{external_id}'


def gaps_from(proposals, client_rows):
    """Every gap there is now, as dicts: key, kind, source, client_id, external_id, label. A key appears once."""
    gaps = {}
    for proposal in proposals:
        for member in proposal['members']:
            key = proposal_key(member['source'], member['external_id'])
            label = member['customer_name'] or f"{SOURCE_LABELS[member['source']]} {member['external_id']}"
            gaps[key] = {'key': key, 'kind': 'unlinked', 'source': member['source'], 'client_id': None, 'external_id': str(member['external_id']), 'label': label}
    for row in client_rows:
        entries = [('missing', source, '') for source in row['missing_from']] + [('stale', link['source'], str(link['external_id'])) for link in row['stale']]
        for key, (kind, source, external_id) in zip(row_keys(row), entries):
            gaps[key] = {'key': key, 'kind': kind, 'source': source, 'client_id': row['id'], 'external_id': external_id, 'label': row['name']}
    return list(gaps.values())


def plan_gaps(open_gaps, current, readable):
    """What to do: `open_gaps` maps the key of each open gap to {'source'}; `current` is gaps_from(); `readable` the sources that could be read.
    Returns {'create': [gap dicts], 'touch': [keys], 'resolve': [keys]}."""
    seen = {gap['key'] for gap in current}
    return {
        'create': [gap for gap in current if gap['key'] not in open_gaps],
        'touch': [gap['key'] for gap in current if gap['key'] in open_gaps],
        'resolve': [key for key, gap in open_gaps.items() if key not in seen and gap['source'] in readable],
    }


def _chunks(items):
    items = list(items)
    for start in range(0, len(items), CHUNK):
        yield items[start:start + CHUNK]


def refresh_gaps(current, readable, now=None):
    """Bring the record up to date with the gaps there are now. Returns how many were created, touched and resolved."""
    from .models import ClientGap
    now = now or timezone.now()
    with transaction.atomic():
        open_gaps = {g.key: g for g in ClientGap.objects.filter(resolved_at__isnull=True)}
        plan = plan_gaps({key: {'source': g.source} for key, g in open_gaps.items()}, current, set(readable))
        ClientGap.objects.bulk_create([
            ClientGap(key=g['key'], kind=g['kind'], source=g['source'], client_id=g['client_id'], external_id=g['external_id'][:100], label=g['label'][:300],
                      first_seen_at=now, last_seen_at=now) for g in plan['create']], ignore_conflicts=True)   # a simultaneous visit may have just created it
        for keys in _chunks(plan['touch']):
            ClientGap.objects.filter(key__in=keys, resolved_at__isnull=True).update(last_seen_at=now)
        for keys in _chunks(plan['resolve']):
            ClientGap.objects.filter(key__in=keys, resolved_at__isnull=True).update(resolved_at=now)
    return {'created': len(plan['create']), 'touched': len(plan['touch']), 'resolved': len(plan['resolve'])}


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def open_since(allowed=None):
    """{key: when it was first seen} for every open gap the person may see (see access.row_allowed)."""
    from .models import ClientGap
    return {key: _iso(first) for key, first, client_id in ClientGap.objects.filter(resolved_at__isnull=True).values_list('key', 'first_seen_at', 'client_id')
            if access.row_allowed(client_id, allowed)}


def recently_resolved(days=30, limit=20, now=None, allowed=None):
    """The gaps closed in the last `days` that the person may see, newest first."""
    from .models import ClientGap
    since = (now or timezone.now()) - timedelta(days=days)
    rows = [g for g in ClientGap.objects.filter(resolved_at__gte=since).order_by('-resolved_at') if access.row_allowed(g.client_id, allowed)][:limit]
    return [{'kind': g.kind, 'source': g.source, 'source_label': SOURCE_LABELS.get(g.source, g.source), 'label': g.label, 'external_id': g.external_id,
             'first_seen_at': _iso(g.first_seen_at), 'resolved_at': _iso(g.resolved_at)} for g in rows]


def proposal_since(proposal, since):
    """When the earliest customer of a proposed group was first seen unlinked (None if none is on record)."""
    found = [since[proposal_key(m['source'], m['external_id'])] for m in proposal['members'] if proposal_key(m['source'], m['external_id']) in since]
    return min(found) if found else None
