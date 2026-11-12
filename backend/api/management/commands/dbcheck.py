from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = 'Test every configured database connection (reads DATABASE_URL / REPORTS_DATABASE_URL from .env).'

    def handle(self, *args, **options):
        failed = False
        for alias in connections:
            conn = connections[alias]
            where = f"{conn.settings_dict['NAME']} on {conn.settings_dict.get('HOST') or 'local file'}"
            try:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    if conn.vendor == 'postgresql':
                        cursor.execute("SELECT current_user, current_database(), current_schema(), "
                                       "has_schema_privilege(current_user, current_schema(), 'CREATE')")
                        user, database, schema, can_create = cursor.fetchone()
                        where += f'; user={user}, database={database}, schema={schema}, can_create_tables={can_create}'
                self.stdout.write(self.style.SUCCESS(f'{alias}: OK ({conn.vendor}, {where})'))
                if conn.vendor == 'postgresql' and not can_create:
                    self.stderr.write(self.style.WARNING(
                        f'{alias}: user "{user}" cannot create tables in schema "{schema}", so `migrate` will fail. '
                        f'Grant CREATE on that schema (see README) or point search_path at a schema it owns.'))
            except Exception as exc:  # report every failure, never print the password
                failed = True
                self.stderr.write(self.style.ERROR(f'{alias}: FAILED ({conn.vendor}, {where}): {exc}'))
        if failed:
            raise SystemExit(1)
