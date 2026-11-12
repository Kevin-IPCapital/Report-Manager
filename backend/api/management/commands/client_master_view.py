"""Create (or drop) the `client_master` view: one row per client with every reference to it in the other schemas, so a query can join to
any of them in one step.

    python manage.py client_master_view --sql     print the SQL and change nothing
    python manage.py client_master_view           create or replace the view
    python manage.py client_master_view --drop    remove it

The view is built from the client link table, so there is still one place to maintain links (the Clients page). It uses PostgreSQL
arrays, because one client can have several Autotask companies, N-central org units and so on. Example:

    SELECT m.client_name, count(*) FROM ncentral.device d
    JOIN report_manager.client_master m ON d.org_unit_id = ANY (m.ncentral_org_unit_ids) GROUP BY 1;"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

VIEW = 'client_master'

# The numeric IDs (Autotask company, N-central org unit, Cove customer) are stored as text, so each is turned into a number only when it
# looks like one: a link with an ID that is not a number is left out of its array instead of making the whole view fail.
SQL = f"""CREATE OR REPLACE VIEW {VIEW} AS
WITH links AS (
    SELECT client_id, source, external_id,
           CASE WHEN external_id ~ '^[0-9]{{1,18}}$' THEN external_id::bigint END AS numeric_id
    FROM api_clientsourcemap
)
SELECT c.id AS client_id,
       c.name AS client_name,
       c.is_active,
       COALESCE(array_agg(l.numeric_id ORDER BY l.numeric_id) FILTER (WHERE l.source = 'autotask' AND l.numeric_id IS NOT NULL), '{{}}') AS autotask_company_ids,
       COALESCE(array_agg(l.numeric_id ORDER BY l.numeric_id) FILTER (WHERE l.source = 'ncentral' AND l.numeric_id IS NOT NULL), '{{}}') AS ncentral_org_unit_ids,
       COALESCE(array_agg(l.external_id ORDER BY l.external_id) FILTER (WHERE l.source = 'sentinelone'), '{{}}') AS sentinelone_site_ids,
       COALESCE(array_agg(l.numeric_id ORDER BY l.numeric_id) FILTER (WHERE l.source = 'cove' AND l.numeric_id IS NOT NULL), '{{}}') AS cove_customer_ids
FROM api_client c
LEFT JOIN links l ON l.client_id = c.id
GROUP BY c.id, c.name, c.is_active"""

EXAMPLES = f"""Examples (replace report_manager with the schema the view was created in):
  SELECT m.client_name, count(*) FROM autotask.ticket t JOIN report_manager.{VIEW} m ON t.company_id = ANY (m.autotask_company_ids) GROUP BY 1;
  SELECT m.client_name, count(*) FROM ncentral.device d JOIN report_manager.{VIEW} m ON d.org_unit_id = ANY (m.ncentral_org_unit_ids) GROUP BY 1;
  SELECT m.client_name, count(*) FROM sentinelone.agent a JOIN report_manager.{VIEW} m ON a.site_id = ANY (m.sentinelone_site_ids) GROUP BY 1;
  SELECT m.client_name, cc.customer_name FROM cove.customers cc JOIN report_manager.{VIEW} m ON cc.id = ANY (m.cove_customer_ids);"""


def view_sql():
    return SQL


class Command(BaseCommand):
    help = 'Create, replace or drop the client_master view (PostgreSQL only).'

    def add_arguments(self, parser):
        parser.add_argument('--sql', action='store_true', help='Print the SQL and do nothing else')
        parser.add_argument('--drop', action='store_true', help=f'Remove the {VIEW} view')

    def handle(self, *args, sql=False, drop=False, **options):
        if sql:
            self.stdout.write(SQL + ';')
            return
        if connection.vendor != 'postgresql':
            raise CommandError(f'The {VIEW} view uses PostgreSQL arrays; this database is {connection.vendor}. Use --sql to see it.')
        with connection.cursor() as cursor:
            if drop:
                cursor.execute(f'DROP VIEW IF EXISTS {VIEW}')
                self.stdout.write(f'Dropped the {VIEW} view (if it existed).')
                return
            cursor.execute(SQL)
            cursor.execute('SELECT current_schema()')
            schema = cursor.fetchone()[0]
        self.stdout.write(f'Created {schema}.{VIEW}. It is built from the client links, so it is always current.')
        self.stdout.write(EXAMPLES.replace('report_manager', schema))
