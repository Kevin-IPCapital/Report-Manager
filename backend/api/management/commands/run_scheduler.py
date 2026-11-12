"""One long-running process that does what the two Windows scheduled tasks do: start the collection worker every minute
and check for problems every five minutes. Use it when scheduled tasks are not an option, or to run everything from
one console.

It is deliberately NOT part of the web server: a web server may run several processes (each would start its own timer),
restarts for code changes, and recycles workers. This is its own process. It still needs something to keep it running
(a console window, a startup task, or a service wrapper)."""
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from api.management.commands.run_collection import JobLock


class Scheduler:
    """The timing rules, separated from the clock and from the processes so they can be tested.
    `launch_worker()` starts a worker and returns something with poll(); `run_alerts()` runs the alert check."""

    def __init__(self, launch_worker, run_alerts, worker_every=60, alerts_every=300, clock=time.monotonic):
        self.launch_worker, self.run_alerts, self.clock = launch_worker, run_alerts, clock
        self.worker_every, self.alerts_every = worker_every, alerts_every
        self.worker = None
        self.next_worker = self.next_alerts = clock()

    def step(self):
        """Do whatever is due now. Returns messages to show."""
        now, messages = self.clock(), []
        if now >= self.next_worker:
            self.next_worker = now + self.worker_every
            # A worker still running is a long batch: do not start a second one (it would only report "busy").
            # That worker keeps its own heartbeat fresh while it runs.
            if self.worker is None or self.worker.poll() is not None:
                self.worker = self.launch_worker()
                messages.append('Worker started.')
        if now >= self.next_alerts:
            self.next_alerts = now + self.alerts_every
            try:
                self.run_alerts()
            except Exception as exc:  # a mail problem must not stop the scheduler
                messages.append(f'Alert check failed: {type(exc).__name__}: {exc}')
        return messages


class Command(BaseCommand):
    help = 'Run the collection worker every minute and the alert check every 5 minutes, in this one process (Ctrl+C stops it).'

    def add_arguments(self, parser):
        parser.add_argument('--worker-every', type=int, default=60, help='Seconds between worker ticks (default 60)')
        parser.add_argument('--alerts-every', type=int, default=300, help='Seconds between alert checks (default 300)')
        parser.add_argument('--once', action='store_true', help='Run one worker tick and one alert check, then exit')

    def handle(self, *args, worker_every=60, alerts_every=300, once=False, **options):
        base = Path(settings.BASE_DIR)
        lock = JobLock(base / 'logs' / 'collectors' / 'scheduler.lock')
        try:
            lock.__enter__()
        except RuntimeError:
            raise CommandError('The scheduler is already running.') from None
        scheduler = Scheduler(
            launch_worker=lambda: subprocess.Popen([sys.executable, str(base / 'manage.py'), 'collection_worker'], cwd=base),
            run_alerts=lambda: call_command('collection_alerts'),
            worker_every=worker_every, alerts_every=alerts_every)
        try:
            self.stdout.write(f'Scheduler running: worker every {worker_every}s, alert check every {alerts_every}s. Ctrl+C stops it.')
            if once:
                scheduler.step()
                if scheduler.worker:
                    scheduler.worker.wait()
                return
            while True:
                for message in scheduler.step():
                    self.stdout.write(message)
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('Stopping.')
            if scheduler.worker and scheduler.worker.poll() is None:
                scheduler.worker.terminate()  # an interrupted batch is marked failed by the next worker
        finally:
            lock.__exit__(None, None, None)
