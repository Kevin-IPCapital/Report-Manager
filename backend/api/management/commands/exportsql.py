from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = ('Write the SQL that `migrate` would run, plus the django_migrations bookkeeping, to a file you can '
            'run in pgAdmin. Reads the database but changes nothing.')

    def add_arguments(self, parser):
        parser.add_argument('--output', default='schema.sql', help='File to write (default: schema.sql)')
        parser.add_argument('--schema', help='Create this schema and put the tables in it '
                                             '(use the same name in the search_path option of DATABASE_URL)')

    def handle(self, *args, output, schema, **options):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            self.stdout.write('Nothing to do: every migration is already applied.')
            return

        collect = getattr(executor.loader, 'collect_sql', None) or executor.collect_sql  # location differs by Django version
        lines = ['BEGIN;']
        if schema:
            lines += [f'CREATE SCHEMA IF NOT EXISTS "{schema}";', f'SET search_path TO "{schema}";']
        if not executor.recorder.has_table():
            with connection.schema_editor(collect_sql=True) as editor:
                editor.create_model(executor.recorder.Migration)
            lines += editor.collected_sql
        lines += collect(plan)
        values = ', '.join(f"('{m.app_label}', '{m.name}', NOW())" for m, _ in plan)
        lines.append(f'INSERT INTO django_migrations (app, name, applied) VALUES {values};')
        lines.append('COMMIT;')

        with open(output, 'w', encoding='utf-8') as f:  # written by Python, so no PowerShell encoding surprises
            f.write('\n'.join(lines) + '\n')
        self.stdout.write(self.style.SUCCESS(f'Wrote {len(plan)} migrations to {output}'))
