"""The client_master view: the SQL is checked here against the real tables and columns; the view itself is exercised on PostgreSQL by
api.tests.ClientMasterViewOnPostgres (which is skipped on any other database)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from api.management.commands.client_master_view import EXAMPLES, SQL, VIEW, view_sql

MODELS = (Path(settings.BASE_DIR) / 'api' / 'models.py').read_text(encoding='utf-8')


def model_fields(name):
    block = re.search(rf'^class {name}\(models\.Model\):(.*?)(?=^class |\Z)', MODELS, re.S | re.M).group(1)
    return set(re.findall(r'^    (\w+) = models\.', block, re.M))


class ViewSql(SimpleTestCase):
    def test_it_only_uses_tables_and_columns_that_exist(self):
        self.assertTrue({'client', 'source', 'external_id'} <= model_fields('ClientSourceMap'))   # client becomes client_id
        self.assertTrue({'name', 'is_active'} <= model_fields('Client'))
        self.assertIn('FROM api_clientsourcemap', SQL)
        self.assertIn('FROM api_client c', SQL)
        self.assertEqual(view_sql(), SQL)

    def test_every_source_has_a_column_and_every_array_is_never_null(self):
        for source, column in (('autotask', 'autotask_company_ids'), ('ncentral', 'ncentral_org_unit_ids'), ('sentinelone', 'sentinelone_site_ids'), ('cove', 'cove_customer_ids')):
            self.assertIn(f"l.source = '{source}'", SQL)
            self.assertIn(f'AS {column}', SQL)
        self.assertEqual(SQL.count('COALESCE(array_agg('), 4)
        self.assertEqual(SQL.count("'{}'"), 4)          # an empty array, never NULL, so ANY () is simply false

    def test_a_non_numeric_id_cannot_make_the_view_fail(self):
        # the cast to a number is inside a CASE that checks the text first, and the numeric arrays also leave out anything that did not convert
        self.assertIn("CASE WHEN external_id ~ '^[0-9]{1,18}$' THEN external_id::bigint END", SQL)
        self.assertEqual(SQL.count('l.numeric_id IS NOT NULL'), 3)
        self.assertNotIn('external_id::bigint', SQL.replace("CASE WHEN external_id ~ '^[0-9]{1,18}$' THEN external_id::bigint END", ''))   # no other cast

    def test_one_row_per_client_including_clients_with_no_links(self):
        self.assertIn('LEFT JOIN links l ON l.client_id = c.id', SQL)
        self.assertIn('GROUP BY c.id, c.name, c.is_active', SQL)

    def test_no_schema_is_hard_coded_so_it_lands_where_the_tables_are(self):
        self.assertNotRegex(SQL, r'report_manager\.')
        self.assertTrue(SQL.startswith(f'CREATE OR REPLACE VIEW {VIEW} AS'))

    def test_the_sql_is_well_formed_at_a_glance(self):
        self.assertEqual(SQL.count('('), SQL.count(')'))
        self.assertNotIn(';', SQL)
        self.assertEqual(len(re.findall(r'FILTER \(WHERE', SQL)), 4)

    def test_the_examples_join_on_columns_that_exist(self):
        for column in ('autotask_company_ids', 'ncentral_org_unit_ids', 'sentinelone_site_ids', 'cove_customer_ids'):
            self.assertIn(column, EXAMPLES)
            self.assertIn(f'ANY (m.{column})', EXAMPLES)
