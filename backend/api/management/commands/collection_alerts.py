"""Check the collectors and email anything that is wrong. Run every few minutes from a scheduled task that is separate
from the worker, so a dead worker is itself reported. Each problem is emailed once, again as a daily reminder, and once
more when it clears."""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.collection_alerts import active_rows, current_alerts, format_email, plan_alerts, record
from api.collection_freshness import annotate_stored_data_age, source_capture_times
from api.collection_health_core import build_health
from api.collector_queue import connect


class Command(BaseCommand):
    help = 'Email collector problems (set COLLECTOR_ALERT_EMAILS). --dry-run only prints; --test-email checks the mail settings.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print what would be sent; send and record nothing')
        parser.add_argument('--test-email', action='store_true', help='Send a test message to the recipients and stop')

    def handle(self, *args, dry_run=False, test_email=False, **options):
        recipients = settings.COLLECTOR_ALERT_EMAILS
        if test_email:
            if not recipients:
                raise CommandError('COLLECTOR_ALERT_EMAILS is empty.')
            send_mail('[Report Manager] Test alert', 'If you can read this, collector alert emails are working.', None, recipients)
            self.stdout.write(self.style.SUCCESS(f'Test email sent to {", ".join(recipients)}.'))
            return

        base, now = settings.BASE_DIR, timezone.now()
        health = build_health(base)
        annotate_stored_data_age(health['jobs'], source_capture_times(), now)
        alerts = current_alerts(base, health['jobs'], now)
        with connect(base) as con:
            plan = plan_alerts(active_rows(con), alerts, now, settings.COLLECTOR_ALERT_REPEAT_HOURS)
            message = format_email(plan)
            self.stdout.write(f'{len(alerts)} problem(s) open; {len(plan["new"])} new, {len(plan["repeat"])} reminder(s), '
                              f'{len(plan["resolved"])} resolved.')
            if message is None:
                return
            subject, body = message
            if dry_run:
                self.stdout.write(f'\n[dry run] {subject}\n\n{body}')
                return
            if not recipients:
                self.stdout.write(self.style.WARNING('No recipients: set COLLECTOR_ALERT_EMAILS. Nothing sent or recorded.'))
                return
            send_mail(subject, body, None, recipients)   # raises on failure, so nothing is recorded and the next run retries
            record(con, plan, now)
            self.stdout.write(self.style.SUCCESS(f'Alert email sent to {", ".join(recipients)}.'))
