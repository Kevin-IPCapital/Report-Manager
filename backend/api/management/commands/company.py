"""Set up companies from the command line. Companies are walls between organisations (once DJANGO_CLIENT_ACCESS_ENFORCED=1); the platform
owner sits above them.

    python manage.py company list
    python manage.py company create "Company Name"
    python manage.py company add-user <username> "Company Name"     (use - for the company to take someone out of theirs)
    python manage.py company claim "Client Name" "Company Name"     (use - for the company to take a client back: unclaimed)
    python manage.py company owner <username>                       make this person the platform owner (--remove to undo)

The platform owner can see everything and is the only one who can create companies. A company's administrators (staff in that company) see
their own company's clients, run its data collection, and claim clients nobody has claimed yet."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import Client, ClientAccess, Company, CompanyMember


class Command(BaseCommand):
    help = 'List or set up companies, who belongs to them, and who the platform owner is.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['list', 'create', 'add-user', 'claim', 'owner'])
        parser.add_argument('first', nargs='?')
        parser.add_argument('second', nargs='?')
        parser.add_argument('--remove', action='store_true', help='With owner: take the platform owner role away')

    def company(self, name):
        found = Company.objects.filter(name__iexact=name).first()
        if not found:
            raise CommandError(f'There is no company "{name}". Companies: {", ".join(Company.objects.order_by("name").values_list("name", flat=True)) or "none yet"}.')
        return found

    def handle(self, *args, action, first=None, second=None, remove=False, **options):
        User = get_user_model()
        if action == 'list':
            for c in Company.objects.order_by('name'):
                self.stdout.write(f'{c.name}: {c.members.count()} people, {c.clients.count()} clients')
            self.stdout.write(f'Unclaimed clients: {Client.objects.filter(company__isnull=True).count()}')
            self.stdout.write('Platform owner(s): ' + (', '.join(User.objects.filter(is_superuser=True).values_list('username', flat=True)) or 'nobody yet'))
            return
        if not first:
            raise CommandError(f'{action} needs more arguments (see --help).')
        if action == 'create':
            if Company.objects.filter(name__iexact=first).exists():
                raise CommandError(f'There is already a company called "{first}".')
            Company.objects.create(name=first.strip())
            self.stdout.write(f'Created the company {first.strip()}.')
            self.stdout.write('Note: the shared score weights and the shared report templates are not yet separate for each company, so do not '
                              'put a second organisation on this system until that is done (ACC-15 in docs/06_STATUS_AND_BACKLOG.md).')
        elif action == 'add-user':
            user = User.objects.filter(username=first).first()
            if not user:
                raise CommandError(f'There is no user "{first}".')
            if not second:
                raise CommandError('add-user needs a username and a company (or - to take them out).')
            with transaction.atomic():
                ClientAccess.objects.filter(user=user).delete()   # the clients they were given belonged to their old company
                if second == '-':
                    CompanyMember.objects.filter(user=user).delete()
                    self.stdout.write(f'{first} is no longer part of any company.')
                else:
                    company = self.company(second)
                    CompanyMember.objects.update_or_create(user=user, defaults={'company': company})
                    self.stdout.write(f'{first} is now part of {company.name}.')
        elif action == 'claim':
            client = Client.objects.filter(name__iexact=first).first()
            if not client:
                raise CommandError(f'There is no client "{first}".')
            if not second:
                raise CommandError('claim needs a client and a company (or - to take it back).')
            company = None if second == '-' else self.company(second)
            Client.objects.filter(pk=client.pk).update(company=company)
            self.stdout.write(f'{client.name} now belongs to {company.name}.' if company else f'{client.name} is unclaimed again: only administrators can see it.')
        elif action == 'owner':
            user = User.objects.filter(username=first).first()
            if not user:
                raise CommandError(f'There is no user "{first}".')
            user.is_superuser = not remove
            if not remove:
                user.is_staff = True
            user.save(update_fields=['is_superuser', 'is_staff'])
            self.stdout.write(f"{first} is {'no longer' if remove else 'now'} the platform owner.")
