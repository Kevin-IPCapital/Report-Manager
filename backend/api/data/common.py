"""Helpers shared by the report data modules. Every database read goes through `query`, so tests (and any
future caching or client scoping) have exactly one place to hook in."""
from datetime import datetime, timezone

from django.conf import settings
from django.db import connections

UTC = timezone.utc


def col(key, label, **extra):
    """Column definition. Optional: type ('status' | 'datetime' | 'bool' | 'flag' | 'priority' | 'duration' |
    'percent' | 'link' | 'text'), wrap=True for long text, invert=True so a true bool is shown as bad."""
    if key == 'status':
        extra.setdefault('type', 'status')
    return {'key': key, 'label': label, **extra}


def report_alias():
    return 'reports' if 'reports' in settings.DATABASES else 'default'


def query(sql, params=()):
    """Run a read-only query and return a list of dicts."""
    with connections[report_alias()].cursor() as cursor:
        cursor.execute(sql, params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def aware(dt):
    """Timestamps stored without a time zone are treated as UTC."""
    return dt.replace(tzinfo=UTC) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def iso(dt):
    return aware(dt).isoformat().replace('+00:00', 'Z') if dt else None


def hours(start, end):
    return round((end - start).total_seconds() / 3600, 4) if start and end else None
