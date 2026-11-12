"""Saved patch scores, so trends are real history. The worker writes one snapshot per patch run (for all devices,
and for each client linked to N-central) right after a patch collection. A snapshot keeps the counts and the SHARED
weights used, so history can be audited or re-weighted later. Users' own weights never change saved history."""
from . import clients, scoring
from .data import common as c, patches


def snapshot_payload(groups, weights):
    """Pure: the stored form of one set of groups, or None when there is nothing to score (nothing is invented)."""
    if not groups:
        return None
    types = patches.build_types(groups, [])
    rows, overall = scoring.compute_scores(types, weights)
    scores = {'overall': overall['current'], **{r['key']: r['current'] for r in rows if not r.get('group')}}
    components = [{'type': t['key'], 'category_id': cat['category_id'], 'category': cat['category'], 'installed': cat['installed'],
                   'missing': cat['missing'], 'compliance': cat['compliance']} for t in types for cat in t['categories']]
    return {'components': components, 'scores': scores}


def finished_runs():
    return c.query('SELECT run_id, finished_at FROM ncentral.patch_run WHERE finished_at IS NOT NULL ORDER BY run_id')


def capture_run(run, source='live'):
    """Write the snapshots for one run (a {run_id, finished_at} row). Returns how many were written."""
    from .models import PatchSnapshot
    weights = scoring.shared_weights()
    written = 0
    for client_id, units in [(None, None), *clients.ncentral_scopes().items()]:
        payload = snapshot_payload(patches.groups(as_of=run['run_id'], org_units=units), weights)
        if payload is None:
            continue
        PatchSnapshot.objects.update_or_create(
            run_id=run['run_id'], client_id=client_id,
            defaults={'captured_at': c.aware(run['finished_at']), 'source': source, 'weights': weights, **payload})
        written += 1
    return written


def capture_latest():
    runs = finished_runs()
    return capture_run(runs[-1]) if runs else 0


def backfill(limit=None):
    """Snapshots for finished runs that have none. Marked 'backfill': they use today's definitions and weights."""
    from .models import PatchSnapshot
    done = set(PatchSnapshot.objects.filter(client__isnull=True).values_list('run_id', flat=True))
    pending = [r for r in finished_runs() if r['run_id'] not in done]
    if limit:
        pending = pending[-limit:]
    return sum(capture_run(r, source='backfill') for r in pending)


def combine(snapshots):
    """Pure: the scores of several clients taken together, one result per run. `snapshots` are dicts with run_id, captured_at, weights and
    components (as saved). For each run the saved counts of every chosen client are added together and then scored with the weights saved
    for that run, so the result is the combined position at that time, not an average of the clients' scores. A client with no snapshot
    for a run simply is not in it. Returns [{captured_at, scores}] in run order."""
    runs = {}
    for snap in snapshots:
        runs.setdefault(snap['run_id'], []).append(snap)
    combined = []
    for run_id in sorted(runs):
        group = runs[run_id]
        totals = {}
        for snap in group:
            for comp in snap['components']:
                slot = totals.setdefault((comp['type'], comp['category_id'], comp['category']), {'installed': 0, 'missing': 0})
                slot['installed'] += comp['installed']
                slot['missing'] += comp['missing']
        types = []
        for key, label in patches._PATCH_TYPES:
            categories = []
            for (kind, category_id, _name), n in totals.items():
                if kind != key:
                    continue
                total = n['installed'] + n['missing']
                categories.append({'category_id': category_id, 'compliance': round(n['installed'] / total * 100, 2) if total else None, 'previous': None})
            types.append({'key': key, 'label': label, 'categories': categories})
        rows, overall = scoring.compute_scores(types, group[0].get('weights') or {})
        combined.append({'captured_at': max(snap['captured_at'] for snap in group), 'scores': {'overall': overall['current'], **{r['key']: r['current'] for r in rows if not r.get('group')}}})
    return combined


def trend(params, allowed=None):
    """{heading, type, x, y} from saved snapshots: one point per day (the latest that day). Empty, never invented. Several clients give
    their combined history (see combine)."""
    client_ids = clients.parse_ids(params.get('client'))
    client_id = client_ids[0] if len(client_ids) == 1 else None
    scope = params.get('scope') or 'overall'
    if params.get('org') and not client_ids:  # snapshots are kept per master client
        return {'heading': 'Score history is saved per client: link this customer to a client to see its trend',
                'type': 'line', 'x': [], 'y': []}
    if allowed is not None and not client_ids:
        # the saved history for "everyone" covers everybody, including clients this person may not see: someone limited to some clients
        # gets the combined history of their own clients instead
        client_ids = sorted(allowed)
        if not client_ids:
            return {'heading': 'No clients to show a score trend for', 'type': 'line', 'x': [], 'y': []}
        client_id = client_ids[0] if len(client_ids) == 1 else None
    label = 'Overall' if scope == 'overall' else dict(patches._PATCH_TYPES).get(scope, scope)
    from .models import PatchSnapshot   # only now: the early answers above need no database
    points = {}
    if len(client_ids) > 1:
        saved = [{'run_id': s.run_id, 'captured_at': s.captured_at, 'weights': s.weights, 'components': s.components}
                 for s in PatchSnapshot.objects.filter(client_id__in=client_ids).order_by('captured_at')]
        for result in combine(saved):
            value = result['scores'].get(scope)
            if value is not None:
                points[result['captured_at'].date().isoformat()] = value
        suffix = f' (combined: {len(client_ids)} clients' + ('; customers not linked to a client have no history)' if params.get('org') else ')')
    else:
        for snapshot in PatchSnapshot.objects.filter(client_id=client_id).order_by('captured_at'):
            value = snapshot.scores.get(scope)
            if value is not None:
                points[snapshot.captured_at.date().isoformat()] = value
        suffix = ''
    if not points:
        return {'heading': f'{label} score trend: no history yet', 'type': 'line', 'x': [], 'y': []}
    days = sorted(points)
    return {'heading': f'{label} Score Trend{suffix}', 'type': 'line', 'x': days, 'y': [points[d] for d in days]}
