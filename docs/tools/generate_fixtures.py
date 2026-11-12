#!/usr/bin/env python3
"""Regenerates frontend/src/report/fixtures/api-responses.json from the real backend code.

The report code in the browser must read exactly what the API returns. These fixtures are produced by calling the backend's own data
functions (with the test database standing in for PostgreSQL), so a change to an API response shows up as a failing frontend test
instead of a broken report.

    python docs/tools/generate_fixtures.py          write it
    python docs/tools/generate_fixtures.py --check  exit 1 if it is out of date

It needs the backend's test fixtures, so it runs inside the Django project (Django must be installed)."""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'frontend' / 'src' / 'report' / 'fixtures' / 'api-responses.json'


def build():
    sys.path.insert(0, str(ROOT / 'backend'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from api import data
    from api.test_support import FakeReports, RESOURCE_COLUMNS, TECHNICIAN_COLUMNS
    out = {}
    with FakeReports(technician_columns=TECHNICIAN_COLUMNS + RESOURCE_COLUMNS, resources=[{'resource_id': 7, 'first_name': 'Thandi', 'last_name': 'Nkosi'}]):
        out['tickets'] = data.get_section('tickets', date(2026, 10, 1), date(2026, 10, 31))
        out['patches'] = data.get_section('patches')
        out['patch-entries'] = data.get_section('patch-entries', params={'type': 'windows-security', 'category': 'g1'})
        out['server-health'] = data.get_section('server-health')
        out['backups'] = data.get_section('backups')
    comment = 'Generated from the real backend code (api/data/*) with the test database fixtures: the shapes the report code must read. Regenerate with docs/tools/generate_fixtures.py.'
    return json.dumps({'comment': comment, **out}, indent=1, default=str)


def comparable(text):
    """Without the values that change with the clock (how old the data is)."""
    data = json.loads(text)
    def scrub(value):
        if isinstance(value, dict):
            return {k: ('<time>' if k in ('data_age_hours', 'captured_at', 'last_collected_at', 'last_success', 'at', 'attempted_at', 'last_installed_at') else scrub(v)) for k, v in value.items()}
        return [scrub(v) for v in value] if isinstance(value, list) else value
    return scrub(data)


if __name__ == '__main__':
    fresh = build()
    if '--check' in sys.argv:
        same = TARGET.is_file() and comparable(TARGET.read_text(encoding='utf-8')) == comparable(fresh)
        print('up to date' if same else 'STALE: run python docs/tools/generate_fixtures.py')
        sys.exit(0 if same else 1)
    TARGET.write_text(fresh, encoding='utf-8')
    print(f'wrote {TARGET.relative_to(ROOT)}')
