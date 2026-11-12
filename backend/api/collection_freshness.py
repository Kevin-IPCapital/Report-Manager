"""Read-only source-table timestamps for collection freshness.

A source's latest timestamp is not proof of complete ingestion. The dashboard
shows it alongside the collector's latest execution outcome.
"""
from datetime import datetime, timezone

from django.conf import settings
from django.db import connections
from django.utils import timezone as django_timezone

# SQL is static and never includes input received from the browser.
SOURCE_QUERIES = {
    'ncentral_inventory': 'SELECT MAX(last_seen) FROM ncentral.device',
    'ncentral_patches': 'SELECT MAX(finished_at) FROM ncentral.patch_run WHERE finished_at IS NOT NULL',
    'ncentral_monitoring': 'SELECT MAX(finished_at) FROM ncentral.poll_run WHERE finished_at IS NOT NULL',
    'autotask_tickets': 'SELECT MAX(captured_at) FROM autotask.snapshot',
    'sentinelone_monthly': 'SELECT MAX(captured_at) FROM sentinelone.snapshot',
    'cove_backup': 'SELECT MAX(captured_at) FROM cove.snapshot',
}


def source_capture_times():
    """Return a per-job timestamp or None if the source has no usable data.

    Missing schemas, permissions and DB outages must not make the entire
    operational health endpoint fail. They are reported as unknown instead.
    """
    alias = 'reports' if 'reports' in settings.DATABASES else 'default'
    times = {}
    for name, sql in SOURCE_QUERIES.items():
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute(sql)
                value = cursor.fetchone()[0]
            if value is not None and django_timezone.is_naive(value):
                # The N-central tables contain timestamp WITHOUT time zone.
                # Respect the configured Django time zone, rather than assuming UTC.
                value = django_timezone.make_aware(value, django_timezone.get_default_timezone())
            times[name] = value.astimezone(timezone.utc) if value else None
        except Exception:
            times[name] = None
    return times


def annotate_stored_data_age(jobs, timestamps, now):
    """Add the age of the newest stored timestamp. It also gives freshness, but only for a job with no good run in its log."""
    for job in jobs:
        captured = timestamps.get(job['job'])
        job['data_as_of'] = captured.isoformat() if captured else None
        job['data_age_hours'] = max(0.0, (now - captured).total_seconds() / 3600) if captured else None
        job['data_age_source'] = 'database' if captured else 'unavailable'
        if job.get('freshness_basis') == 'last good run':
            continue   # judged from the last good run: a timestamp in the database can come from a run that failed part-way
        interval = job.get('interval_minutes')
        if captured and interval:   # no good run in the log: the newest stored timestamp is the best evidence there is
            job['freshness'] = 'CURRENT' if (now - captured).total_seconds() <= interval * 60 else 'STALE'
            job['freshness_basis'] = 'stored data'
    return jobs
