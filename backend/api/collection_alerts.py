"""Collector alerts: what is wrong right now, and which of it still needs telling to someone.

`evaluate` is pure. `plan_alerts` decides what to send so that each problem is announced once, repeated as a reminder
(default daily) while it lasts, and announced again when it clears. State is kept in the collector runtime database,
and is only recorded after the email has actually been sent, so a failed send is retried on the next run."""
from datetime import datetime, timedelta

from .collection_health_core import JOB_LABELS

ALERT_DDL = """CREATE TABLE IF NOT EXISTS alert_state (
    key         TEXT PRIMARY KEY,
    message     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL,
    last_sent   TEXT NOT NULL,
    resolved_at TEXT
)"""
HEARTBEAT_DDL = """CREATE TABLE IF NOT EXISTS heartbeat (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick_at TEXT,
    state        TEXT
)"""


def _ago(minutes):
    minutes = max(0, round(minutes))
    if minutes >= 2 * 1440:
        return f'{minutes // 1440} days'
    if minutes >= 120:
        return f'{minutes // 60} hours'
    return f'{minutes} minute{"" if minutes == 1 else "s"}'


def _parse(value):
    return datetime.fromisoformat(value) if value else None


def _stamp(value):
    parsed = _parse(value)
    return parsed.strftime('%Y-%m-%d %H:%M UTC') if parsed else 'at an unknown time'


def evaluate(jobs, schedules, config, heartbeat, now, heartbeat_max_minutes=5, include_partial=False):
    """The problems that exist at `now`, as [{key, severity, job, message}].

    jobs: build_health(...) jobs, annotated with stored data age. schedules: {job: {schedule_enabled}}.
    config: {job: {timeout_seconds, ...}}. heartbeat: {last_tick_at, ...}. Disabled jobs are never reported.
    A partial result (some devices unavailable) is only reported when include_partial is set."""
    alerts = []

    def add(key, severity, job, message):
        alerts.append({'key': key, 'severity': severity, 'job': job, 'message': message})

    if any(s.get('schedule_enabled') for s in schedules.values()):
        last_tick = _parse((heartbeat or {}).get('last_tick_at'))
        if last_tick is None:
            add('worker:never', 'error', None, 'The collection worker has never run, so scheduled collections are not happening.')
        else:
            minutes = (now - last_tick).total_seconds() / 60
            if minutes > heartbeat_max_minutes:
                add('worker:stale', 'error', None, f'The collection worker last ran {_ago(minutes)} ago. It should run every minute; '
                                                   'check the Windows scheduled task.')

    for job in jobs:
        if not job.get('enabled'):
            continue
        name, label, status = job['job'], JOB_LABELS.get(job['job'], job['job']), job.get('status')
        when = _stamp(job.get('last_run_at'))
        if status == 'FAILED':
            add(f'{name}:failed', 'error', name, f'{label} failed on its last run ({when}).')
        elif status == 'PARTIAL' and include_partial:
            add(f'{name}:partial', 'warning', name, f'{label} completed only partly ({when}); some devices were unavailable (N-central cannot find them, so they count as disconnected).')
        elif status == 'STARTED':
            started = _parse(job.get('last_run_at'))
            limit = ((config or {}).get(name) or {}).get('timeout_seconds', 1800) * 1.5
            if started and (now - started).total_seconds() > limit:
                add(f'{name}:stuck', 'error', name, f'{label} started {_ago((now - started).total_seconds() / 60)} ago and never finished.')

        interval = job.get('interval_minutes')
        if (schedules.get(name) or {}).get('schedule_enabled') and interval:
            from_good_run = job.get('freshness_basis') == 'last good run'   # else the newest stored timestamp
            hours = job.get('good_run_age_hours') if from_good_run else job.get('data_age_hours')
            if hours is None:
                add(f'{name}:nodata', 'warning', name, f'{label} is scheduled but it has no successful run and no stored data was found.')
            else:
                age = hours * 60
                if age > interval * 1.5 + 10:   # a grace period, so a run that is merely due now is not reported
                    what = f'last succeeded {_ago(age)} ago' if from_good_run else f'data is {_ago(age)} old'
                    add(f'{name}:overdue', 'error', name, f'{label} {what}; it should refresh every {_ago(interval)}.')
    return alerts


def active_rows(con):
    return {r['key']: dict(r) for r in con.execute('SELECT key, message, last_sent FROM alert_state WHERE active = 1')}


def plan_alerts(stored, alerts, now, repeat_hours=24):
    """What to announce: new problems, reminders for old ones, and problems that have cleared. Writes nothing."""
    current = {a['key']: a for a in alerts}
    reminder_after = timedelta(hours=repeat_hours)
    return {
        'new': [a for k, a in current.items() if k not in stored],
        'repeat': [a for k, a in current.items() if k in stored and now - _parse(stored[k]['last_sent']) >= reminder_after],
        'resolved': [{'key': k, 'message': row['message']} for k, row in stored.items() if k not in current],
        'current': alerts,
    }


def record(con, plan, now):
    """Remember what was announced. Call this only after the email has been sent."""
    stamp = now.isoformat(timespec='seconds')
    for a in plan['new'] + plan['repeat']:
        con.execute(
            'INSERT INTO alert_state (key, message, severity, active, first_seen, last_sent) VALUES (?, ?, ?, 1, ?, ?) '
            'ON CONFLICT(key) DO UPDATE SET message = excluded.message, severity = excluded.severity, last_sent = excluded.last_sent, '
            'first_seen = CASE WHEN alert_state.active = 1 THEN alert_state.first_seen ELSE excluded.first_seen END, '
            'active = 1, resolved_at = NULL',
            (a['key'], a['message'], a['severity'], stamp, stamp))
    for r in plan['resolved']:
        con.execute('UPDATE alert_state SET active = 0, resolved_at = ? WHERE key = ?', (stamp, r['key']))


def format_email(plan):
    """(subject, body), or None when there is nothing to announce."""
    if not (plan['new'] or plan['repeat'] or plan['resolved']):
        return None
    lines = []
    for heading, items in (('New problems', plan['new']), ('Still not fixed (reminder)', plan['repeat'])):
        if items:
            lines += [heading + ':', *[f"  - {a['message']}" for a in items], '']
    if plan['resolved']:
        lines += ['Resolved:', *[f"  - {r['message']}" for r in plan['resolved']], '']
    open_count = len(plan['current'])
    lines.append(f'{open_count} problem(s) open now.' if open_count else 'No problems are open now.')
    raised = len(plan['new']) + len(plan['repeat'])
    subject = (f'[Report Manager] {raised} collection alert(s)' if raised else '[Report Manager] Collection alerts resolved')
    return subject, '\n'.join(lines)


def current_alerts(base, jobs, now=None):
    """The alerts for the dashboard: same rules, using the saved schedule, heartbeat and settings."""
    from django.conf import settings

    from .collector_queue import configuration, heartbeat_status, schedule_status, utcnow
    try:
        config = configuration(base)
    except (OSError, ValueError):
        config = {}
    now = now or utcnow()
    return evaluate(jobs, schedule_status(base), config, heartbeat_status(base, now), now,
                    settings.COLLECTOR_HEARTBEAT_MAX_MINUTES, settings.COLLECTOR_ALERT_ON_PARTIAL)
