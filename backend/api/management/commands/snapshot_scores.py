from django.core.management.base import BaseCommand

from api import snapshots


class Command(BaseCommand):
    help = ('Save patch score snapshots for trends. By default the latest finished N-central patch run; '
            'the worker does this automatically after every patch collection.')

    def add_arguments(self, parser):
        parser.add_argument('--backfill', action='store_true', help='Also build snapshots for older runs that have none')
        parser.add_argument('--limit', type=int, help='With --backfill: only the newest N runs')

    def handle(self, *args, backfill=False, limit=None, **options):
        written = snapshots.backfill(limit) if backfill else snapshots.capture_latest()
        self.stdout.write(self.style.SUCCESS(f'{written} snapshot(s) written.'))
