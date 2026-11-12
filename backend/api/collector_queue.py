"""Persistent single-worker collector queue, without Django migrations.

SQLite here holds operational orchestration state only, never collected source data.
Use a persistent writable local folder accessible to both Django and the scheduler.
"""
import json
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collection_alerts import ALERT_DDL, HEARTBEAT_DDL
from .collection_health_core import JOB_NAMES

UTC = timezone.utc


def utcnow():
    return datetime.now(UTC)


def iso(value):
    return value.isoformat(timespec='seconds')


def parse(value):
    return datetime.fromisoformat(value) if value else None


def db_path(base):
    return Path(base) / 'collector_runtime.sqlite3'


@contextmanager
def connect(base):
    path = db_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout = 15000')
    con.execute('''CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requested_by TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        status TEXT NOT NULL,
        dry_run INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        current_job TEXT,
        error TEXT
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS batch_jobs (
        batch_id INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        job TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'QUEUED',
        started_at TEXT,
        finished_at TEXT,
        duration_seconds REAL,
        PRIMARY KEY (batch_id, ordinal)
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS schedules (
        job TEXT PRIMARY KEY,
        next_run_at TEXT,
        last_scheduled_at TEXT
    )''')
    con.execute(ALERT_DDL)
    con.execute(HEARTBEAT_DDL)
    try:
        yield con
        if con.in_transaction:
            con.commit()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()


def configuration(base):
    config = json.loads((Path(base) / 'collector_jobs.json').read_text(encoding='utf-8'))
    return config.get('jobs', {})


def enqueue(base, jobs, requested_by='dashboard', dry_run=False):
    config = configuration(base)
    if not jobs or len(set(jobs)) != len(jobs):
        raise ValueError('Select at least one distinct collector')
    if any(name not in JOB_NAMES or not isinstance(config.get(name), dict) or
           not config[name].get('enabled', False) for name in jobs):
        raise ValueError('One or more collectors are unknown or disabled')
    with connect(base) as con:
        con.execute('BEGIN IMMEDIATE')
        # Includes queued batches to prevent a second browser request from stacking work.
        active = con.execute("SELECT id FROM batches WHERE status IN ('QUEUED','RUNNING') LIMIT 1").fetchone()
        if active:
            con.rollback()
            raise RuntimeError('Another collection batch is already queued or running')
        cursor = con.execute('INSERT INTO batches (requested_by,requested_at,status,dry_run,total) VALUES (?,?,?,?,?)',
                             (requested_by, iso(utcnow()), 'QUEUED', int(dry_run), len(jobs)))
        batch_id = cursor.lastrowid
        con.executemany('INSERT INTO batch_jobs (batch_id,ordinal,job) VALUES (?,?,?)',
                        [(batch_id, i, name) for i, name in enumerate(jobs)])
        con.commit()
        return batch_id


def claim(base):
    with connect(base) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT * FROM batches WHERE status='QUEUED' ORDER BY id LIMIT 1").fetchone()
        if row:
            con.execute("UPDATE batches SET status='RUNNING',started_at=? WHERE id=?", (iso(utcnow()), row['id']))
        con.commit()
        return dict(row) if row else None


def mark_started(base, batch_id, job):
    with connect(base) as con:
        con.execute('UPDATE batches SET current_job=? WHERE id=?', (job, batch_id))
        con.execute("UPDATE batch_jobs SET status='RUNNING',started_at=? WHERE batch_id=? AND job=?",
                    (iso(utcnow()), batch_id, job))


def mark_done(base, batch_id, job, status, duration):
    with connect(base) as con:
        con.execute("UPDATE batch_jobs SET status=?,finished_at=?,duration_seconds=? WHERE batch_id=? AND job=?",
                    (status, iso(utcnow()), duration, batch_id, job))
        con.execute('UPDATE batches SET completed=completed+1,current_job=NULL WHERE id=?', (batch_id,))


def finish(base, batch_id):
    with connect(base) as con:
        statuses = [r['status'] for r in con.execute('SELECT status FROM batch_jobs WHERE batch_id=?', (batch_id,))]
        status = 'FAILED' if statuses and all(s == 'FAILED' for s in statuses) else (
            'PARTIAL' if any(s in ('FAILED', 'PARTIAL') for s in statuses) else 'SUCCESS')
        con.execute('UPDATE batches SET status=?,finished_at=?,current_job=NULL WHERE id=?',
                    (status, iso(utcnow()), batch_id))
        return status


def fail_interrupted(base):
    """A previous worker ended without completing its active batch."""
    with connect(base) as con:
        con.execute("UPDATE batch_jobs SET status='FAILED',finished_at=? WHERE status='RUNNING'", (iso(utcnow()),))
        con.execute("UPDATE batches SET status='FAILED',finished_at=?,error='Worker interrupted' WHERE status='RUNNING'",
                    (iso(utcnow()),))


def due_jobs(base, now=None):
    """Durable next-run values; a schedule is enabled only with schedule_enabled=true."""
    now = now or utcnow()
    selected = []
    try:
        jobs = configuration(base)
    except (OSError, ValueError):
        return selected
    with connect(base) as con:
        con.execute('BEGIN IMMEDIATE')
        for name in JOB_NAMES:
            cfg = jobs.get(name, {})
            if not isinstance(cfg, dict) or not cfg.get('enabled') or not cfg.get('schedule_enabled', False):
                continue
            minutes = cfg.get('interval_minutes')
            if type(minutes) is not int or not 1 <= minutes <= 10080:
                continue
            row = con.execute('SELECT next_run_at FROM schedules WHERE job=?', (name,)).fetchone()
            next_time = parse(row['next_run_at']) if row and row['next_run_at'] else None
            # First run is due immediately once schedule_enabled is explicitly set.
            if next_time is None or next_time <= now:
                selected.append(name)
                # Advance from now: no catch-up bursts if a machine was switched off.
                con.execute('INSERT INTO schedules (job,next_run_at,last_scheduled_at) VALUES (?,?,?) '
                            'ON CONFLICT(job) DO UPDATE SET next_run_at=excluded.next_run_at,last_scheduled_at=excluded.last_scheduled_at',
                            (name, iso(now + timedelta(minutes=minutes)), iso(now)))
        con.commit()
    return selected


def schedule_status(base):
    try:
        jobs = configuration(base)
    except (OSError, ValueError):
        jobs = {}
    with connect(base) as con:
        rows = {r['job']: r['next_run_at'] for r in con.execute('SELECT job,next_run_at FROM schedules')}
    result = {}
    for name in JOB_NAMES:
        cfg = jobs.get(name, {})
        cfg = cfg if isinstance(cfg, dict) else {}
        enabled = bool(cfg.get('enabled') and cfg.get('schedule_enabled'))
        result[name] = {'schedule_enabled': enabled,
                        'next_execution_at': rows.get(name) if enabled else None}
    return result


def touch_heartbeat(base, state):
    """Record that the worker ran just now. Every tick does this, including ticks that find the worker already busy,
    so a long batch does not look like a dead worker."""
    with connect(base) as con:
        con.execute('INSERT INTO heartbeat (id, last_tick_at, state) VALUES (1, ?, ?) '
                    'ON CONFLICT(id) DO UPDATE SET last_tick_at = excluded.last_tick_at, state = excluded.state',
                    (iso(utcnow()), state))


def heartbeat_status(base, now=None):
    with connect(base) as con:
        row = con.execute('SELECT last_tick_at, state FROM heartbeat WHERE id = 1').fetchone()
    if not row or not row['last_tick_at']:
        return {'last_tick_at': None, 'state': None, 'age_seconds': None}
    age = ((now or utcnow()) - parse(row['last_tick_at'])).total_seconds()
    return {'last_tick_at': row['last_tick_at'], 'state': row['state'], 'age_seconds': max(0.0, age)}


def batch_status(base):
    with connect(base) as con:
        active = con.execute("SELECT * FROM batches WHERE status IN ('QUEUED','RUNNING') ORDER BY id LIMIT 1").fetchone()
        recent = active or con.execute('SELECT * FROM batches ORDER BY id DESC LIMIT 1').fetchone()
        if not recent:
            return {'active': False, 'batch': None}
        jobs = [dict(r) for r in con.execute('SELECT job,status,duration_seconds FROM batch_jobs WHERE batch_id=? ORDER BY ordinal', (recent['id'],))]
        batch = {k: recent[k] for k in ('id','requested_at','started_at','finished_at','status','total','completed','current_job','dry_run')}
        batch['jobs'] = jobs
        return {'active': bool(active), 'batch': batch}
