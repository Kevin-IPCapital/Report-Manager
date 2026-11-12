from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ('Give an existing user staff access (needed for the Data Collection page and collector endpoints), '
            'or take it away with --remove.')

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--remove', action='store_true', help='Remove staff access instead of granting it')

    def handle(self, *args, username, remove=False, **options):
        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError(f'No user named {username!r}. Sign in once (or register) to create it first.')
        user.is_staff = not remove
        user.save(update_fields=['is_staff'])
        self.stdout.write(self.style.SUCCESS(f"{username} is {'no longer' if remove else 'now'} staff."))
