"""Give someone access to a client, take it away, or list who can see what. Only matters while DJANGO_CLIENT_ACCESS_ENFORCED=1; staff
always see every client.

    python manage.py client_access list
    python manage.py client_access grant sam "Acme"
    python manage.py client_access revoke sam "Acme"
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.models import Client, ClientAccess


class Command(BaseCommand):
    help = 'List, grant or revoke a person\'s access to a client.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['list', 'grant', 'revoke'])
        parser.add_argument('username', nargs='?')
        parser.add_argument('client', nargs='?', help='The client\'s name (any capitals)')

    def handle(self, *args, action, username=None, client=None, **options):
        if action == 'list':
            people = {}
            for grant in ClientAccess.objects.select_related('user', 'client').order_by('user__username', 'client__name'):
                people.setdefault(grant.user.get_username(), []).append(grant.client.name)
            for name in sorted(people):
                self.stdout.write(f"{name}: {', '.join(people[name])}")
            if not people:
                self.stdout.write('Nobody has been given a client yet.')
            return
        if not username or not client:
            raise CommandError(f'{action} needs a username and a client name.')
        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError(f'There is no user "{username}".')
        found = Client.objects.filter(name__iexact=client).first()
        if not found:
            raise CommandError(f'There is no client "{client}".')
        if action == 'grant':
            _, created = ClientAccess.objects.get_or_create(user=user, client=found)
            self.stdout.write(f'{username} can now see {found.name}.' if created else f'{username} could already see {found.name}.')
        else:
            deleted, _ = ClientAccess.objects.filter(user=user, client=found).delete()
            self.stdout.write(f'{username} can no longer see {found.name}.' if deleted else f'{username} could not see {found.name} anyway.')
