"""Run once per scheduler tick (e.g. Windows Task Scheduler every minute).

Never run collectors inside the Django web process. The global lock prevents
simultaneous workers; individual run_collection jobs retain their own locks.

Every tick records a heartbeat, even when another worker is still busy, so the dashboard and the alert check can
tell a working scheduler from a dead one. A tick that finds the worker busy exits normally (not as an error), so the
Windows task history is not full of failures while a long batch runs."""
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from api.collector_queue import (batch_status, claim, connect, due_jobs, enqueue,
                                 fail_interrupted, finish, mark_done, mark_started, touch_heartbeat)
from api.collection_health_core import read_job_log
from api.management.commands.run_collection import JobLock


class Command(BaseCommand):
    help = 'Process queued/manual and scheduled collection jobs sequentially.'

    def beat(self, base, state):
        try:
            touch_heartbeat(base, state)
        except Exception as exc:  # a heartbeat problem must never stop the collections themselves
            self.stderr.write(f'Heartbeat not recorded: {type(exc).__name__}: {exc}')

    def run_job(self, base, argv, timeout=7500, beat_every=30):
        """Run one collector as a child process and keep the heartbeat fresh while it runs, so a long collection does not
        look like a dead worker. Output is read throughout (a full pipe would otherwise stall the child)."""
        proc = subprocess.Popen(argv, cwd=base, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
        deadline = time.monotonic() + timeout
        while True:
            try:
                out, err = proc.communicate(timeout=beat_every)
                return subprocess.CompletedProcess(argv, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                self.beat(base, 'RUNNING')
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    raise subprocess.TimeoutExpired(argv, timeout)

    def handle(self, *args, **kwargs):
        base = Path(settings.BASE_DIR)
        lock = JobLock(base / 'logs' / 'collectors' / 'worker.lock')
        try:
            lock.__enter__()
        except RuntimeError:  # JobLock raises this only when another worker holds the lock
            self.beat(base, 'BUSY')
            self.stdout.write('Worker already running; nothing to do this tick.')
            return
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        try:
            self.run_tick(base)
        except (RuntimeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            lock.__exit__(None, None, None)

    def run_tick(self, base):
        self.beat(base, 'TICK')
        # Recover runs interrupted by a prior worker crash.
        fail_interrupted(base)
        state = batch_status(base)
        if not state['active']:
            names = due_jobs(base)
            if names:
                enqueue(base, names, requested_by='scheduler')
        batch = claim(base)
        if not batch:
            self.beat(base, 'IDLE')
            self.stdout.write('No collector work due.')
            return
        with connect(base) as con:
            ordered = [r['job'] for r in con.execute(
                'SELECT job FROM batch_jobs WHERE batch_id=? ORDER BY ordinal', (batch['id'],))]
        for name in ordered:
            self.beat(base, 'RUNNING')
            mark_started(base, batch['id'], name)
            started = time.monotonic()
            argv = [sys.executable, str(base / 'manage.py'), 'run_collection', name]
            if batch['dry_run']:
                argv.append('--dry-run')
            # run_collection handles per-job timeout, log output and partial classification.
            try:
                proc = self.run_job(base, argv)
                last = read_job_log(base / 'logs' / 'collectors' / f'{name}.log', name)
                # Do not classify this run from a previous terminal log entry.
                fresh = bool(last and last.get('timestamp') and
                             last['timestamp'].timestamp() >= (time.time() - (time.monotonic() - started) - 3))
                if not fresh:
                    last = None
                if last and last['status'] in ('SUCCESS', 'PARTIAL', 'FAILED'):
                    status = last['status']
                else:
                    status = 'SUCCESS' if proc.returncode == 0 else 'FAILED'
                if proc.returncode and status != 'PARTIAL':
                    status = 'FAILED'
                self.stdout.write(f'{name}: {status} ({time.monotonic()-started:.1f}s)')
            except (subprocess.SubprocessError, OSError) as exc:
                status = 'FAILED'
                self.stderr.write(f'{name}: {type(exc).__name__}: {exc}')
            mark_done(base, batch['id'], name, status, round(time.monotonic()-started, 2))
            self.beat(base, 'RUNNING')
            if name == 'ncentral_patches' and status in ('SUCCESS', 'PARTIAL') and not batch['dry_run']:
                # Save the scores for trends. A failure here is reported but never changes the batch outcome.
                try:
                    from api import snapshots
                    self.stdout.write(f'Patch score snapshots written: {snapshots.capture_latest()}')
                except Exception as exc:
                    self.stderr.write(f'Patch score snapshot failed: {type(exc).__name__}: {exc}')
        self.stdout.write(f'Batch {batch["id"]}: {finish(base, batch["id"])}')
        self.beat(base, 'IDLE')
