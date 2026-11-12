"""Database-free, read-only collector status log parser."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

JOB_NAMES = (
    'ncentral_inventory', 'ncentral_patches', 'ncentral_monitoring',
    'autotask_tickets', 'sentinelone_monthly', 'cove_backup',
)  # append only: tests and the UI rely on the existing order
JOB_LABELS = {
    'ncentral_inventory': 'N-central inventory', 'ncentral_patches': 'N-central patches', 'ncentral_monitoring': 'N-central monitoring',
    'autotask_tickets': 'Autotask tickets', 'sentinelone_monthly': 'SentinelOne monthly', 'cove_backup': 'Cove backup',
}
LOG_LINE = re.compile(
    r'^(?P<time>\S+) (?P<status>START|SUCCESS|PARTIAL|FAILED) '
    r'job=(?P<job>[a-z_]+)\s+(?P<rest>.*)$'
)
GOOD_STATUSES = ('SUCCESS', 'PARTIAL')   # PARTIAL collected every device N-central could find, so it refreshed the data
UNAVAILABLE = re.compile(r'\bunavailable=(\d+)\b')   # how many devices N-central could not find, on a partial run
DURATION = re.compile(r'\bduration_seconds=(\d+(?:\.\d+)?)\b')
EXIT = re.compile(r'\bexit_code=(-?\d+|None)\b')
DRY_RUN = re.compile(r'\bdry_run=(True|False)\b')
MAX_LOG_BYTES = 1024 * 1024


def _parse_timestamp(value):
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(timezone.utc)
    except ValueError:
        return None


def _records(path, job):
    """Every START and terminal entry for the job in the bounded tail of its log, oldest first; None if the log cannot be read."""
    try:
        with path.open('rb') as f:
            f.seek(0, 2)
            end = f.tell()
            offset = max(0, end - MAX_LOG_BYTES)
            f.seek(offset)
            contents = f.read(MAX_LOG_BYTES)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if offset:
        contents = contents.partition(b'\n')[2]
    records = []
    for line in contents.decode('utf-8', 'replace').splitlines():
        match = LOG_LINE.match(line)
        if not match or match['job'] != job:
            continue
        timestamp = _parse_timestamp(match['time'])
        if timestamp is None:
            continue
        fields = match['rest']
        dry = DRY_RUN.search(fields)
        record = {'status': match['status'], 'timestamp': timestamp, 'dry_run': dry.group(1) == 'True' if dry else None}
        if match['status'] != 'START':
            duration = DURATION.search(fields)
            exit_match = EXIT.search(fields)
            record['duration_seconds'] = float(duration.group(1)) if duration else None
            record['exit_code'] = int(exit_match.group(1)) if exit_match and exit_match.group(1) != 'None' else None
            unavailable = UNAVAILABLE.search(fields)
            record['unavailable'] = int(unavailable.group(1)) if unavailable else None
        records.append(record)
    return records


def summarise_log(path, job):
    """{'latest': the latest run, 'good': the latest good run} (either may be None); None if the log cannot be read.

    The latest run is whatever happened last, including a failure. A newer unmatched START overrides an older terminal status: it
    might be running or have crashed before final logging, so success is not claimed then.

    The GOOD run is the latest one that succeeded or completed partly and was not a dry run: the run that actually refreshed the
    data. Data freshness is judged from it, never from a failure. `failures_since` counts the failed runs after it (or all those in
    the log when there has been no good run), so "it has failed four times since it last worked" can be said."""
    records = _records(path, job)
    if records is None:
        return None
    last_start = last_terminal = good = None
    failures = 0
    for record in records:
        if record['status'] == 'START':
            last_start = record
            continue
        last_terminal = record
        if record['status'] in GOOD_STATUSES and record['dry_run'] is False:
            good, failures = record, 0
        elif record['status'] == 'FAILED' and record['dry_run'] is not True:
            failures += 1
    latest = last_terminal
    if last_start and (not last_terminal or last_start['timestamp'] > last_terminal['timestamp']):
        latest = {**last_start, 'status': 'STARTED', 'duration_seconds': None, 'exit_code': None}
    return {'latest': latest, 'good': {**good, 'failures_since': failures} if good else None, 'failures_in_log': failures}


def read_job_log(path, job):
    """The latest observed run (see summarise_log), or None."""
    summary = summarise_log(path, job)
    return summary['latest'] if summary else None


# What each page's data comes from, for the "data as of" line: key (as the page asks for it), what to call it, and the job that fills it.
PAGE_SOURCES = (
    ('patches', 'Patch data', 'ncentral_patches'),
    ('servers', 'Server data', 'sentinelone_monthly'),
    ('backups', 'Backup data', 'cove_backup'),
    ('tickets', 'Ticket data', 'autotask_tickets'),
)


def public_freshness(jobs):
    """Pure: how old each page's data is, in the small form anyone signed in may see. It carries only a time, a state and how many runs have
    failed since: nothing from the logs, no exit codes, no errors, no schedule. `jobs` are build_health's jobs (with the stored-data age
    added when it is known). The time is the last GOOD run (a failure leaves the data exactly as old as it was), or, with no good run in the
    log, the newest row in the source's own schema. state: `current`, `stale`, `unknown` (a time, but no schedule to judge it by) or `never`."""
    by_name = {job['job']: job for job in jobs}
    sources = []
    for key, label, name in PAGE_SOURCES:
        job = by_name.get(name, {})
        good = job.get('last_good_at')
        as_of = good or job.get('data_as_of')
        age = job.get('good_run_age_hours') if good else job.get('data_age_hours')
        if not as_of:
            state = 'never'
        else:
            state = {'CURRENT': 'current', 'STALE': 'stale'}.get(job.get('freshness'), 'unknown')
        sources.append({'key': key, 'label': label, 'as_of': as_of, 'age_hours': age, 'state': state,
                        'failures_since': int(job.get('failures_since_good') or 0), 'basis': 'last good run' if good else 'stored data' if as_of else 'none',
                        'enabled': bool(job.get('enabled', False))})
    return sources


def build_health(base_dir, config_path=None, now=None):
    """All configured jobs; no upstream calls, no DB or filesystem writes."""
    base_dir = Path(base_dir)
    config_path = Path(config_path) if config_path else base_dir / 'collector_jobs.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        jobs = config.get('jobs', {})
        if not isinstance(jobs, dict):
            raise ValueError('Invalid job configuration')
        configured = True
    except (OSError, ValueError, TypeError):
        jobs = {}
        configured = False
    now = now or datetime.now(timezone.utc)
    results = []
    for name in JOB_NAMES:
        job = jobs.get(name)
        job = job if isinstance(job, dict) else {}
        summary = summarise_log(base_dir / 'logs' / 'collectors' / (name + '.log'), name)
        last = summary['latest'] if summary else None
        good = summary['good'] if summary else None
        enabled = bool(job.get('enabled', False))
        interval = job.get('interval_minutes')
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            interval = None
        result = {
            'job': name,
            'enabled': enabled,
            'status': last['status'] if last else 'NEVER_RUN',
            'last_run_at': last['timestamp'].isoformat() if last else None,
            'last_duration_seconds': last['duration_seconds'] if last else None,
            'last_exit_code': last['exit_code'] if last else None,
            'last_dry_run': last['dry_run'] if last else None,
            'disconnected_devices': last.get('unavailable') if last and last['status'] == 'PARTIAL' else None,   # devices N-central cannot find: they count as disconnected (NC-01)
            'interval_minutes': interval,
            # The last run that succeeded or completed partly: the one that actually refreshed the data (never a failure or a dry run).
            'last_good_at': good['timestamp'].isoformat() if good else None,
            'last_good_status': good['status'] if good else None,
            'last_good_duration_seconds': good['duration_seconds'] if good else None,
            'failures_since_good': (good['failures_since'] if good else summary['failures_in_log'] if summary else 0),
            'good_run_age_hours': None,
            'stale_since': None,
            'freshness': 'UNKNOWN',
            'freshness_basis': 'none',
        }
        # Freshness is judged from the last good run, whatever the latest run did: a failure does not make old data look current,
        # and does not hide that it has gone stale. Without a schedule interval (or without a good run) it stays unknown here.
        if good:
            age = (now - good['timestamp']).total_seconds()
            result['good_run_age_hours'] = max(0.0, age / 3600)
            if interval:
                result['freshness'] = 'CURRENT' if 0 <= age <= interval * 60 else 'STALE'
                result['freshness_basis'] = 'last good run'
                if result['freshness'] == 'STALE':
                    result['stale_since'] = (good['timestamp'] + timedelta(minutes=interval)).isoformat()
        results.append(result)
    return {'configured': configured, 'checked_at': now.isoformat(), 'jobs': results}


