"""Patch scores. A type's score is the weighted average of its CATEGORIES' compliance (so a category with many patches
does not swamp the rest); the overall score is the weighted average of the type scores. Weights default to 1 and 0
leaves an item out. Pure functions, plus `weights_for` which reads the saved weights."""
import re

WEIGHT_KEY = re.compile(r'^[a-z0-9-]+(:.{0,150})?$')
MAX_WEIGHTS = 500


def category_key(type_key, category_id):
    return f"{type_key}:{'' if category_id is None else category_id}"


def weighted_average(items, weight_of, value_of):
    """Weighted average that skips missing values and zero weights."""
    total = weight = 0.0
    for item in items:
        value, w = value_of(item), weight_of(item)
        if value is not None and w > 0:
            total += value * w
            weight += w
    return total / weight if weight else None


def _round(value):
    return None if value is None else round(value, 2)


def compute_scores(types, weights):
    """types: [{'key', 'label', 'categories': [{'category_id', 'compliance', 'previous'}]}].
    Returns (rows for the score table, {'current', 'previous'}), each score rounded to 2 places."""
    items = []
    for t in types:
        def weight(c, key=t['key']):
            return weights.get(category_key(key, c['category_id']), 1)
        items.append({'key': t['key'], 'label': t['label'], 'indent': True,
                      'current': weighted_average(t['categories'], weight, lambda c: c['compliance']),
                      'previous': weighted_average(t['categories'], weight, lambda c: c['previous'])})
    type_weight = lambda i: weights.get(i['key'], 1)  # noqa: E731
    overall = {'current': _round(weighted_average(items, type_weight, lambda i: i['current'])),
               'previous': _round(weighted_average(items, type_weight, lambda i: i['previous']))}
    rows = [{'group': True, 'label': 'Security Monitoring'},
            *[{**i, 'current': _round(i['current']), 'previous': _round(i['previous'])} for i in items]]
    return rows, overall


def _share(weight, total):
    return round(weight / total * 100, 1) if total else None


def explain(types, weights):
    """The working behind compute_scores, for showing a person how their weights were used. Same inputs, same rules (it calls the same
    weighted_average), so the figures it gives are the figures the scores are made from.

    For each type: its categories, each with its compliance, its weight, whether it counts and, if it does, its share of the type (its weight
    divided by the weights that count); then the type's score, its own weight and its share of the overall score. A category counts when it has
    a compliance (it lists patches) and its weight is above 0; a type counts when it has a score and its weight is above 0. `weighted_total`
    and `weight_total` are the numerator and denominator of each average. The overall score is the weighted average of the type scores,
    unrounded, exactly as compute_scores does it; scores are shown to 2 places."""
    out = []
    for t in types:
        categories, total, weight_total = [], 0.0, 0.0
        for c in t['categories']:
            w, value = weights.get(category_key(t['key'], c['category_id']), 1), c['compliance']
            counts = value is not None and w > 0
            if counts:
                total += value * w
                weight_total += w
            categories.append({'category_id': c['category_id'], 'category': c['category'], 'compliance': value, 'weight': w, 'counts': counts,
                               'reason': None if counts else ('its weight is 0' if w <= 0 else 'it lists no patches'), 'share': None})
        for c in categories:
            c['share'] = _share(c['weight'], weight_total) if c['counts'] else None
        score = weighted_average(t['categories'], lambda c, key=t['key']: weights.get(category_key(key, c['category_id']), 1), lambda c: c['compliance'])
        out.append({'key': t['key'], 'label': t['label'], 'categories': categories, 'weighted_total': round(total, 4), 'weight_total': weight_total,
                    'score': _round(score), '_exact': score, 'weight': weights.get(t['key'], 1)})
    total = weight_total = 0.0
    for t in out:
        t['counts'] = t['_exact'] is not None and t['weight'] > 0
        t['reason'] = None if t['counts'] else ('its weight is 0' if t['weight'] <= 0 else 'none of its categories counts')
        if t['counts']:
            total += t['_exact'] * t['weight']
            weight_total += t['weight']
    for t in out:
        t['share'] = _share(t['weight'], weight_total) if t['counts'] else None
        del t['_exact']
    return {'types': out, 'overall': {'score': _round(total / weight_total) if weight_total else None, 'weighted_total': round(total, 4), 'weight_total': weight_total}}


def effective_weights(shared, user):
    return {**shared, **user}


def report_weights(override):
    """The weights for a report that carries its own: the shared defaults with the report's weights on top. The viewer's personal
    weights are left out on purpose, so the same report gives the same scores to everyone who runs it."""
    return effective_weights(shared_weights(), override)


def clean_weights(raw):
    """Validate a {key: number} mapping from a request. Returns (weights, error)."""
    if not isinstance(raw, dict) or len(raw) > MAX_WEIGHTS:
        return None, 'Weights must be an object with at most 500 entries'
    cleaned = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not WEIGHT_KEY.fullmatch(key):
            return None, f'Invalid weight key: {str(key)[:40]}'
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1000:
            return None, f'Weight for {key} must be a number from 0 to 1000'
        cleaned[key] = float(value)
    return cleaned, None


def shared_weights():
    from .models import ScoreWeight
    return dict(ScoreWeight.objects.filter(user__isnull=True).values_list('key', 'weight'))


def user_weights(user):
    from .models import ScoreWeight
    return dict(ScoreWeight.objects.filter(user=user).values_list('key', 'weight'))


def weights_for(user):
    """The weights that apply to this user: shared defaults, overridden by the user's own."""
    return effective_weights(shared_weights(), user_weights(user) if user is not None else {})
