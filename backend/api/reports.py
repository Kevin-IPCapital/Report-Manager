"""Saved report definitions: what a report is made of, and the rules a stored or imported one must follow.

A report is a list of BLOCKS (see frontend/src/report/config.js, which holds the same rules for the browser; both are tested against
frontend/src/report/template-cases.json, so they accept and reject exactly the same templates). Version 1 templates (one flat set of
sections and filters) are migrated. Nothing here trusts what a client sent: the result is a freshly built dict."""
import math
import re

VERSION = 2
MAX_BLOCKS = 20
MAX_WEIGHTS = 500
DURATION_UNITS = {'days': 3660, 'weeks': 520, 'months': 120}   # the longest amount for each unit
MAX_FILTER_VALUES = 20   # one filter can hold several values (a row matches if it has any of them), except the client scope
DEFAULT_TITLE = 'IP Capital Management Report'
BLOCK_TYPES = ('tickets', 'patches', 'patch-details', 'server-health', 'backups')
BLOCK_LABELS = {'tickets': 'Client Tickets', 'patches': 'Patch Compliance', 'patch-details': 'Patch Details', 'server-health': 'Endpoint Health', 'backups': 'Backup Health'}
BLOCK_FILTERS = {
    'tickets': ('customer', 'priority', 'ticketStatus', 'ticketType', 'issueType', 'subIssueType'),
    'patches': ('patchType', 'patchScope'),
    'patch-details': ('patchType', 'patchScope'),
    'server-health': ('customer', 'agentStatus'),
    'backups': ('customer', 'backupHealth'),
}
V1_SECTIONS = ('tickets', 'patches', 'server-health', 'backups', 'patch-details')   # the order version 1 reports used
WEIGHT_KEY = re.compile(r'[a-z0-9-]+(:.{0,150})?')
BLOCK_ID = re.compile(r'[A-Za-z0-9_-]{1,40}')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
MAX_CONFIG_BYTES = 100_000


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _migrate_v1(raw):
    sections = raw.get('sections') if isinstance(raw.get('sections'), dict) else {}
    old = raw.get('filters') if isinstance(raw.get('filters'), dict) else {}
    blocks = []
    for key in V1_SECTIONS:
        if sections.get(key) is True:
            filters = {k: old[k] for k in BLOCK_FILTERS[key] if k in old and k != 'patchScope'}
            blocks.append({'id': f'b{len(blocks) + 1}', 'type': key, 'title': '', 'filters': filters})
    return {'version': VERSION, 'title': raw.get('title'), 'annex': False, 'maxRows': raw.get('maxRows'),
            'period': {'from': old.get('from', ''), 'to': old.get('to', '')}, 'weights': {}, 'blocks': blocks}


def _clean_filter(key, value):
    """One filter: text as given, or a list of up to 20 values (trimmed, blanks dropped, repeats ignoring case dropped). One value
    becomes plain text and none becomes blank, so a single choice always looks the same whichever way it was entered."""
    if isinstance(value, str):
        if len(value) > 200:
            raise ValueError(f'Invalid {key} filter.')
        return value
    if not isinstance(value, list):
        raise ValueError(f'Invalid {key} filter.')
    if key == 'patchScope':
        raise ValueError('The client filter takes one value.')
    if len(value) > MAX_FILTER_VALUES:
        raise ValueError(f'A filter can have at most {MAX_FILTER_VALUES} values.')
    seen, kept = set(), []
    for item in value:
        if not isinstance(item, str) or len(item) > 200:
            raise ValueError(f'Invalid {key} filter.')
        text = item.strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            kept.append(text)
    return '' if not kept else kept[0] if len(kept) == 1 else kept


def _clean_weights(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_WEIGHTS:
        raise ValueError('Weights must be an object with at most 500 entries.')
    cleaned = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not WEIGHT_KEY.fullmatch(key):
            raise ValueError(f'Invalid weight key: {str(key)[:40]}')
        if not _is_number(value) or not 0 <= value <= 1000:
            raise ValueError(f'Weight for {key} must be a number from 0 to 1000.')
        cleaned[key] = value
    return cleaned


def _clean_period(raw):
    if raw is not None and not isinstance(raw, dict):
        raise ValueError('Invalid period.')
    period = {}
    for key in ('from', 'to'):
        value = (raw or {}).get(key, '')
        value = '' if value is None else value
        if not isinstance(value, str) or (value and not DATE.fullmatch(value)):
            raise ValueError('Dates must use YYYY-MM-DD.')
        period[key] = value
    if period['from'] and period['to'] and period['from'] > period['to']:
        raise ValueError('Start date must not be after end date.')
    last = (raw or {}).get('last')
    if last is not None:
        if not isinstance(last, dict) or set(last) != {'amount', 'unit'}:
            raise ValueError('Invalid duration.')
        if not isinstance(last['unit'], str) or last['unit'] not in DURATION_UNITS:
            raise ValueError('A duration is in days, weeks or months.')
        amount = last['amount']
        if not _is_number(amount) or not float(amount).is_integer() or not 1 <= amount <= DURATION_UNITS[last['unit']]:
            raise ValueError(f"A duration is from 1 to {DURATION_UNITS[last['unit']]} {last['unit']}.")
        if period['from'] or period['to']:
            raise ValueError('Choose either dates or a duration for the default period, not both.')
        period['last'] = {'amount': int(amount), 'unit': last['unit']}   # only present when the report has one
    return period


def validate_template(raw):
    """The normalised template (a new dict), or ValueError with a message that can be shown to the person."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid template.')
    version = raw.get('version')
    if isinstance(version, bool) or version not in (1, VERSION):
        raise ValueError('Unsupported report template version.')
    t = _migrate_v1(raw) if version == 1 else raw
    blocks_in = t.get('blocks')
    if not isinstance(blocks_in, list) or not blocks_in:
        raise ValueError('Add at least one section.')
    if len(blocks_in) > MAX_BLOCKS:
        raise ValueError(f'A report can have at most {MAX_BLOCKS} sections.')
    seen, blocks = set(), []
    for index, b in enumerate(blocks_in):
        if not isinstance(b, dict) or b.get('type') not in BLOCK_TYPES:
            raise ValueError('Unknown section type.')
        kind = b['type']
        given = b.get('filters')
        if given is not None and not isinstance(given, dict):
            raise ValueError('Invalid filters.')
        for key in given or {}:
            if key not in BLOCK_FILTERS[kind]:
                raise ValueError(f'The {key} filter does not apply to {BLOCK_LABELS[kind]}.')
        filters = {}
        for key in BLOCK_FILTERS[kind]:
            value = (given or {}).get(key, '')
            filters[key] = _clean_filter(key, '' if value is None else value)
        title = b.get('title')
        title = '' if title is None else title
        if not isinstance(title, str) or len(title) > 120:
            raise ValueError('A section title can have at most 120 characters.')
        block_id = b.get('id') if isinstance(b.get('id'), str) and BLOCK_ID.fullmatch(b['id']) and b['id'] not in seen else None
        n = index + 1
        while block_id is None:
            if f'b{n}' not in seen:
                block_id = f'b{n}'
            n += 1
        seen.add(block_id)
        blocks.append({'id': block_id, 'type': kind, 'title': title.strip(), 'filters': filters})
    title = t.get('title')
    title = title.strip()[:120] if isinstance(title, str) else ''
    max_rows = t.get('maxRows')
    whole = _is_number(max_rows) and float(max_rows).is_integer()
    return {'version': VERSION, 'title': title or DEFAULT_TITLE, 'annex': t.get('annex') is True,
            'maxRows': max(10, min(500, int(max_rows))) if whole else 200,
            'period': _clean_period(t.get('period')), 'weights': _clean_weights(t.get('weights')), 'blocks': blocks}
