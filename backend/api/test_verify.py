"""The real-data check: run against the fake reporting data it must find every section healthy, and against anything broken it must say so."""
from datetime import date

from django.test import SimpleTestCase

from api import data, verify
from api.test_support import FakeReports


def real(name, *args, **kwargs):
    if name == 'patch-trend':   # read from saved snapshots in the Django database, which these tests do not have; covered by api.tests.SnapshotTests
        return {'heading': 'Overall Score Trend', 'type': 'line', 'x': ['2026-11-01'], 'y': [80.0]}
    return data.get_section(name, *args, user=None, allowed=None, **kwargs)


class Healthy(SimpleTestCase):
    def results(self, **fake):
        with FakeReports(**fake):
            return {r['name']: r for r in verify.run_checks(real, today=date(2026, 11, 10))}

    def test_every_section_is_checked_in_order(self):
        self.assertEqual(list(self.results()), ['tickets', 'patches', 'patch-overview', 'patch-entries', 'patch-trend', 'server-health', 'backups', 'backup-detail'])

    def test_against_data_that_works_nothing_fails(self):
        results = self.results()
        self.assertEqual([n for n, r in results.items() if r['status'] == verify.FAILED], [])
        self.assertEqual(results['tickets']['status'], verify.OK)
        self.assertIn('3 tickets', results['tickets']['detail'])
        self.assertIn('overall score', results['patches']['detail'])
        self.assertEqual(results['patch-entries']['status'], verify.OK)

    def test_it_says_how_many_rows_are_not_linked_to_a_client(self):
        results = self.results()
        self.assertIn('not linked', results['backups']['detail'])                       # the Cove customer with no client
        self.assertIn('3 endpoints, 3 linked to a client', results['server-health']['detail'])

    def test_nothing_collected_is_empty_not_failed(self):
        results = self.results(tickets=[], servers=[], cove_devices=[], patch_groups=([], []), org_groups=([], []))
        self.assertEqual(results['tickets']['status'], verify.EMPTY)
        self.assertEqual(results['server-health']['status'], verify.EMPTY)
        self.assertEqual(results['backups']['status'], verify.EMPTY)
        self.assertIn('Cove job is disabled', results['backups']['detail'])
        self.assertEqual(results['patch-entries']['status'], verify.SKIPPED)
        self.assertEqual(results['backup-detail']['status'], verify.SKIPPED)

    def test_a_missing_cove_schema_is_reported_as_empty_with_its_reason(self):
        results = self.results(cove_tables=[])
        self.assertEqual(results['backups']['status'], verify.EMPTY)
        self.assertIn('cove schema was not found', results['backups']['detail'])


class Broken(SimpleTestCase):
    def test_a_section_that_raises_is_failed_with_its_error_and_the_rest_still_run(self):
        def fetch(name, *args, **kwargs):
            if name == 'server-health':
                raise RuntimeError('column "machine_type" does not exist')
            with FakeReports():
                return real(name, *args, **kwargs)
        results = {r['name']: r for r in verify.run_checks(fetch, today=date(2026, 11, 10))}
        self.assertEqual(results['server-health']['status'], verify.FAILED)
        self.assertIn('machine_type', results['server-health']['detail'])
        self.assertIn('RuntimeError', results['server-health']['detail'])
        self.assertEqual(results['tickets']['status'], verify.OK)                       # one failure does not stop the others
        self.assertEqual(results['backups']['status'], verify.OK)

    def test_a_section_that_answers_not_found_is_a_failure(self):
        results = {r['name']: r for r in verify.run_checks(lambda name, *a, **k: None, today=date(2026, 11, 10))}
        self.assertTrue(all(r['status'] in (verify.FAILED, verify.SKIPPED) for r in results.values()))
        self.assertIn('not found', results['tickets']['detail'])

    def test_a_very_long_error_is_shortened(self):
        def fetch(name, *a, **k):
            raise ValueError('x' * 5000)
        detail = verify.run_checks(fetch, today=date(2026, 11, 10))[0]['detail']
        self.assertLess(len(detail), 300)

    def test_the_summary_counts_each_kind(self):
        results = [{'status': verify.OK}, {'status': verify.OK}, {'status': verify.FAILED}, {'status': verify.EMPTY}, {'status': verify.SKIPPED}]
        counts, text = verify.summary(results)
        self.assertEqual(text, '2 OK, 1 empty, 1 failed, 1 skipped')
        self.assertEqual(counts[verify.FAILED], 1)

    def test_the_time_each_check_took_is_recorded(self):
        ticks = iter(range(0, 1000, 3))
        results = verify.run_checks(lambda *a, **k: {'tickets': {'rows': []}}, today=date(2026, 11, 10), clock=lambda: next(ticks))
        self.assertEqual(results[0]['seconds'], 3)


class ServerPatchingCheck(SimpleTestCase):
    def results(self, **fake):
        with FakeReports(**fake):
            return {r['name']: r for r in verify.run_checks(lambda name, *a, **k: real(name, *a, **k), today=date(2026, 11, 10))}

    def test_the_check_says_how_many_servers_were_matched_to_a_device_for_patching(self):
        detail = self.results()['server-health']['detail']
        self.assertIn('3 endpoints, 3 linked to a client, 2 matched to an N-central device for patching, 1 of them disconnected', detail)

    def test_patching_that_cannot_be_read_fails_the_check_even_though_the_page_would_still_load(self):
        with self.assertLogs('api.data.servers', level='ERROR'):
            result = self.results(server_patch_error=True)['server-health']
        self.assertEqual(result['status'], verify.FAILED)
        self.assertIn('patching could not be read', result['detail'])

    def test_no_servers_at_all_is_empty_not_a_patching_failure(self):
        result = self.results(servers=[])['server-health']
        self.assertEqual(result['status'], verify.EMPTY)


class ImportedAtCheck(SimpleTestCase):
    def check(self, rows=None, error=None):
        def query(sql):
            if error:
                raise error
            return rows
        return verify.check_imported_at(query)

    def row(self, schema, tables, with_column):
        return {'table_schema': schema, 'tables': tables, 'with_column': with_column}

    def test_applied_everywhere_is_ok_and_says_how_many_tables(self):
        result = self.check([self.row('autotask', 6, 6), self.row('ncentral', 13, 13), self.row('cove', 6, 6)])
        self.assertEqual(result['status'], verify.OK)
        self.assertIn('autotask 6, ncentral 13, cove 6', result['detail'])

    def test_not_applied_yet_is_empty_and_says_what_to_run(self):
        result = self.check([self.row('autotask', 6, 0), self.row('ncentral', 13, 0)])
        self.assertEqual(result['status'], verify.EMPTY)
        self.assertIn('2026-11-12_imported_at.sql', result['detail'])

    def test_applied_to_some_tables_only_is_a_failure_that_names_them(self):
        result = self.check([self.row('autotask', 6, 6), self.row('ncentral', 13, 9)])
        self.assertEqual(result['status'], verify.FAILED)
        self.assertIn('ncentral has it on 9 of 13 tables', result['detail'])
        self.assertNotIn('autotask', result['detail'])

    def test_a_schema_that_is_not_installed_is_simply_not_mentioned(self):
        self.assertEqual(self.check([self.row('cove', 6, 6)])['status'], verify.OK)

    def test_no_collector_schema_at_all_is_empty(self):
        self.assertEqual(self.check([])['status'], verify.EMPTY)

    def test_a_database_error_is_reported_as_a_failure(self):
        result = self.check(error=RuntimeError('permission denied for information_schema'))
        self.assertEqual(result['status'], verify.FAILED)
        self.assertIn('permission denied', result['detail'])

    def test_it_only_reads_column_names(self):
        sql = verify._IMPORTED_AT_SQL.upper()
        self.assertTrue(sql.startswith('SELECT'))
        self.assertTrue(all(word not in sql for word in ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP')))
        self.assertIn('INFORMATION_SCHEMA', sql)

    def test_it_is_added_to_the_other_checks_only_when_a_query_is_given(self):
        with FakeReports():
            without = verify.run_checks(lambda name, *a, **k: real(name, *a, **k), today=date(2026, 11, 10))
            with_it = verify.run_checks(lambda name, *a, **k: real(name, *a, **k), today=date(2026, 11, 10), query=lambda sql: [self.row('cove', 6, 6)])
        self.assertNotIn('imported_at', [r['name'] for r in without])
        self.assertEqual([r['name'] for r in with_it][-1], 'imported_at')
