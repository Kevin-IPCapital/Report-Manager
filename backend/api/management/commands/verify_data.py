"""Check every dashboard section against the real databases, read-only, and say what worked, what is empty and what failed.

    python manage.py verify_data              all sections
    python manage.py verify_data --days 365   look further back for tickets

Nothing is written. It also lists each collector's last run and freshness. The exit code is 1 if any section failed."""
import traceback

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api import data, verify
from api.data import common
from api.collection_health_core import JOB_LABELS, build_health


class Command(BaseCommand):
    help = 'Check every dashboard section against the real data (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='How far back to look for tickets (default 90)')
        parser.add_argument('--verbose', action='store_true', help='Show the full error for a failed section')

    def handle(self, *args, days=90, verbose=False, **options):
        if not 1 <= days <= 3660:
            raise CommandError('--days must be between 1 and 3660.')
        self.stdout.write('Checking each dashboard section against the live data (nothing is written)...\n')
        failures = []

        def fetch(name, *a, **k):
            try:
                return data.get_section(name, *a, user=None, allowed=None, **k)   # an administrator's check: no client limit
            except Exception:
                failures.append((name, traceback.format_exc()))
                raise

        results = verify.run_checks(fetch, days=days, query=common.query)
        for r in results:
            self.stdout.write(f"  {r['name']:<16} {r['status']:<8} {r['detail']}  ({r['seconds']:.1f}s)")
        if verbose:
            for name, trace in failures:
                self.stdout.write(f'\n--- {name} ---\n{trace}')
        self.stdout.write('\nCollectors (from their logs):')
        try:
            for job in build_health(settings.BASE_DIR)['jobs']:
                good = job['last_good_at'] or 'never'
                self.stdout.write(f"  {JOB_LABELS.get(job['job'], job['job']):<22} {'enabled' if job['enabled'] else 'disabled':<9} latest {job['status']:<10} last good run {good}  freshness {job['freshness']}")
        except Exception as exc:   # noqa: BLE001
            self.stdout.write(f'  could not read the collector logs: {exc}')
        counts, text = verify.summary(results)
        self.stdout.write(f'\nResult: {text}.')
        if counts[verify.FAILED]:
            self.stdout.write('A FAILED section is a real problem to fix: copy this output (and run with --verbose) and send it on.')
            raise CommandError(f"{counts[verify.FAILED]} section(s) failed.")
