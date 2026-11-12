"""Data-layer tests against a fake reporting database. No PostgreSQL needed."""
from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase

from api import data
from api.data import patches as patch_data
from api.test_support import FakeReports, RESOURCE_COLUMNS, TECHNICIAN_COLUMNS, cove_device, default_tickets, ticket


class TicketData(SimpleTestCase):
    def rows(self, **kwargs):
        with FakeReports(**kwargs) as fake:
            self.fake = fake
            return data.get_section('tickets', date(2026, 10, 1), date(2026, 10, 31))['tickets']['rows']

    def test_only_a_late_first_response_is_red_and_it_says_why(self):
        by_number = {r['ticket_number']: r for r in self.rows()}
        self.assertEqual(by_number['T1']['rowStatus'], 'bad')
        self.assertEqual(by_number['T1']['reason'], 'First response 30m after the due time')
        for number in ('T2', 'T3'):
            self.assertIsNone(by_number[number]['rowStatus'])
            self.assertEqual(by_number[number]['reason'], '')

    def test_hours_are_derived_from_timestamps(self):
        t2 = {r['ticket_number']: r for r in self.rows()}['T2']
        self.assertEqual(t2['response_hours'], 0.3333)
        self.assertEqual(t2['resolution_hours'], 4.0)
        t3 = {r['ticket_number']: r for r in self.rows()}['T3']
        self.assertIsNone(t3['response_hours'])
        self.assertIsNone(t3['resolution_hours'])

    def test_null_classification_becomes_unspecified_and_naive_times_are_utc(self):
        t3 = {r['ticket_number']: r for r in self.rows()}['T3']
        self.assertEqual((t3['priority'], t3['company_name'], t3['rowTone']), ('Unspecified', 'Unspecified', 'unspecified'))
        self.assertEqual(t3['created_at'], '2026-10-07T09:00:00Z')
        self.assertIsNone(t3['first_response_at'])

    def test_critical_priority_keeps_its_tone_and_technician_is_unavailable(self):
        t2 = {r['ticket_number']: r for r in self.rows()}['T2']
        self.assertEqual(t2['rowTone'], 'critical')
        self.assertIsNone(t2['technician'])

    def test_query_uses_newest_version_and_an_inclusive_date_range(self):
        self.rows()
        sql, params = next(call for call in self.fake.calls if 'DISTINCT ON' in call[0])
        self.assertIn('DISTINCT ON (ticket_id)', sql)
        self.assertIn('created_at >= %s', sql)
        self.assertIn('created_at < %s', sql)
        self.assertEqual([p.isoformat() for p in params], ['2026-10-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00'])

    def test_tickets_carry_their_master_client(self):
        by_number = {r['ticket_number']: r for r in self.rows()}
        self.assertEqual(by_number['T1']['client_id'], 1)       # Autotask company 101 is mapped
        self.assertEqual(by_number['T3']['client_id'], 1)       # its name is NULL, but the ID still maps
        self.assertEqual([r['client_id'] for r in self.rows(tickets=[ticket(company_id=None, company_name=None)])], [None])  # never guessed

    def test_an_unmapped_id_falls_back_to_the_client_name_or_alias(self):
        rows = self.rows(tickets=[ticket(company_id=999, company_name='ACME'), ticket(ticket_id=2, company_id=998, company_name='Other')])
        self.assertEqual([r['client_id'] for r in rows], [1, None])

    def test_technician_is_unavailable_while_the_source_does_not_collect_it(self):
        with FakeReports() as fake:
            body = data.get_section('tickets', date(2026, 10, 1), date(2026, 10, 31))
        self.assertFalse(body['technician_available'])
        self.assertTrue(all(r['technician'] is None and r['technician_id'] is None for r in body['tickets']['rows']))
        self.assertNotIn('technician', [col['key'] for col in body['tickets']['columns']])
        self.assertTrue(all('assigned_resource_id' not in sql for sql, _ in fake.calls if 'DISTINCT ON' in sql))  # never asks for a missing column

    def test_technician_names_appear_once_the_source_has_the_data(self):
        tickets = [ticket(ticket_id=1, assigned_resource_id=7), ticket(ticket_id=2, assigned_resource_id=8),
                   ticket(ticket_id=3, assigned_resource_id=None)]
        resources = [{'resource_id': 7, 'first_name': 'Thandi', 'last_name': 'Nkosi'}]
        with FakeReports(tickets=tickets, technician_columns=TECHNICIAN_COLUMNS + RESOURCE_COLUMNS, resources=resources) as fake:
            body = data.get_section('tickets', date(2026, 10, 1), date(2026, 10, 31))
        self.assertTrue(body['technician_available'])
        self.assertEqual([r['technician'] for r in body['tickets']['rows']], ['Thandi Nkosi', 'Resource 8', 'Unassigned'])  # unknown id: never guessed
        self.assertEqual([r['technician_id'] for r in body['tickets']['rows']], [7, 8, None])
        self.assertIn('technician', [col['key'] for col in body['tickets']['columns']])
        self.assertTrue(any('assigned_resource_id' in sql for sql, _ in fake.calls if 'DISTINCT ON' in sql))

    def test_assignments_without_a_resource_table_show_the_resource_id(self):
        with FakeReports(tickets=[ticket(assigned_resource_id=7)], technician_columns=TECHNICIAN_COLUMNS) as fake:
            body = data.get_section('tickets', date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(body['tickets']['rows'][0]['technician'], 'Resource 7')
        self.assertFalse(any('FROM autotask.resource' in sql for sql, _ in fake.calls))

    def test_empty_result(self):
        self.assertEqual(self.rows(tickets=[]), [])
        self.assertEqual(len(default_tickets()), 3)  # the fixture itself is unchanged by the fake


class PatchData(SimpleTestCase):
    def test_types_are_ordered_and_scored_per_category(self):
        with FakeReports():
            body = data.get_section('patches')
        self.assertEqual([t['key'] for t in body['types']], ['windows-security', 'windows-patching', 'third-party'])
        security = body['types'][0]['categories'][0]
        self.assertEqual((security['installed'], security['missing'], security['compliance'], security['previous']), (8, 2, 80.0, 60.0))
        self.assertEqual(body['types'][1]['categories'], [])

    def test_missing_category_and_missing_previous_are_not_invented(self):
        with FakeReports():
            third = data.get_section('patches')['types'][2]['categories'][0]
        self.assertEqual((third['category'], third['category_id'], third['compliance'], third['previous']), ('Uncategorised', None, 75.0, None))

    def test_previous_run_uses_the_second_newest_inventory(self):
        with FakeReports() as fake:
            data.get_section('patches')
        ranks = sorted('inventory_rank = 2' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql)
        self.assertEqual(ranks, [False, True])

    def test_scores_use_category_compliance_and_skip_missing_values(self):
        with FakeReports():
            body = data.get_section('patches')
        scores = {r['key']: (r['current'], r['previous']) for r in body['rows'] if not r.get('group')}
        self.assertEqual(scores, {'windows-security': (80.0, 60.0), 'windows-patching': (None, None), 'third-party': (75.0, None)})
        self.assertEqual(body['overall'], {'current': 77.5, 'previous': 60.0})
        self.assertEqual((body['client'], body['mapped']), (None, True))

    def test_weights_change_the_scores(self):
        with FakeReports(weights={'windows-security': 3}):
            self.assertEqual(data.get_section('patches')['overall']['current'], 78.75)
        with FakeReports(weights={'windows-security:g1': 0}):  # a zero weight leaves the category out
            body = data.get_section('patches')
        self.assertIsNone(body['rows'][1]['current'])
        self.assertEqual(body['overall']['current'], 75.0)

    def test_a_report_with_its_own_weights_ignores_the_viewers_weights(self):
        override = {'third-party': 3}
        with FakeReports(weights={'windows-security': 3}, shared={}):          # the viewer's own weights would give 78.75
            self.assertEqual(data.get_section('patches')['overall']['current'], 78.75)
            self.assertEqual(data.get_section('patches', params={'weights': override})['overall']['current'], 76.25)   # (80 x 1 + 75 x 3) / 4

    def test_a_reports_weights_sit_on_top_of_the_shared_defaults(self):
        with FakeReports(shared={'windows-security': 3}):
            self.assertEqual(data.get_section('patches', params={'weights': {'third-party': 1}})['overall']['current'], 78.75)      # shared 3 still applies
            self.assertEqual(data.get_section('patches', params={'weights': {'windows-security': 1}})['overall']['current'], 77.5)  # the report's own wins

    def test_a_report_without_weights_uses_the_viewers(self):
        with FakeReports(weights={'windows-security': 3}):
            self.assertEqual(data.get_section('patches', params={'weights': {}})['overall']['current'], 78.75)

    def test_client_scope_limits_the_devices_and_reports_unmapped_clients(self):
        with FakeReports() as fake:
            body = data.get_section('patches', params={'client': '1'})
        self.assertTrue(body['mapped'])
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql))
        with FakeReports() as fake:
            body = data.get_section('patches', params={'client': '2'})  # linked to no N-central org unit
        self.assertFalse(body['mapped'])
        self.assertTrue(all('AND FALSE' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql))

    def test_the_client_filter_is_built_from_where_devices_live(self):
        with FakeReports():
            options = data.get_section('patches')['clients']
        self.assertEqual([(o['key'], o['label'], o['devices'], o['linked']) for o in options],
                         [('client:1', 'Acme', 8, True),            # two org units merged under the linked client
                          ('org:30', 'Blue Crane', 4, False)])      # not linked: shown by its N-central customer name

    def test_an_unlinked_customer_can_be_selected_by_org_unit(self):
        with FakeReports() as fake:
            body = data.get_section('patches', params={'org': '30'})
        self.assertEqual((body['org'], body['client'], body['mapped']), ('30', None, True))
        self.assertTrue(all('dev.org_unit_id IN (30)' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql))

    def test_inventory_query_can_be_rebuilt_as_of_a_run(self):
        sql = patch_data.inventory_query(1, as_of=12)
        self.assertIn('d.run_id <= 12', sql)
        self.assertNotIn('d.run_id <=', patch_data.inventory_query(1))

    def test_entries_installed_is_the_opposite_of_missing(self):
        with FakeReports() as fake:
            rows = data.get_section('patch-entries', params={'type': 'windows-security', 'category': 'g1'})['entries']['rows']
        self.assertEqual([r['installed'] for r in rows], [False, True, None])
        self.assertEqual([r['device'] for r in rows], ['SRV-11', '12', 'SRV-13'])  # the name, else the ID
        self.assertEqual(rows[1]['installed_at'], '2026-10-03T08:00:00Z')
        self.assertEqual(fake.calls[0][1], ['windows-security', 'g1'])

    def test_entries_for_an_unknown_type_are_rejected(self):
        with FakeReports() as fake:
            self.assertIsNone(data.get_section('patch-entries', params={'type': 'nope'}))
        self.assertEqual(fake.calls, [])  # nothing was queried


class ServerData(SimpleTestCase):
    def test_servers_are_sorted_and_expose_active_flag(self):
        with FakeReports() as fake:
            servers = data.get_section('server-health')['servers']
        self.assertEqual([s['computer_name'] for s in servers], ['SRV-A', 'SRV-B', 'PC-1'])  # by customer, then computer
        self.assertEqual([s['is_active'] for s in servers], [False, True, True])
        self.assertEqual([s['client_id'] for s in servers], [1, 1, 2])  # two by site ID, one by the client name
        self.assertTrue(all(s['data_age_hours'] >= 0 for s in servers))
        self.assertIn('NOT COALESCE(a.is_decommissioned', fake.calls[0][0])

    def test_unknown_section_is_none(self):
        self.assertIsNone(data.get_section('nope'))


class BackupData(SimpleTestCase):
    def rows(self, **kwargs):
        with FakeReports(**kwargs) as fake:
            self.fake = fake
            self.body = data.get_section('backups')
        return {r['device_name']: r for r in self.body['rows']}

    def test_devices_are_listed_worst_first_with_a_name_even_when_cove_has_none(self):
        self.rows()
        self.assertEqual([r['health'] for r in self.body['rows']], ['CRITICAL', 'WARNING', 'OK'])
        self.assertIn('m365-mailboxes', [r['device_name'] for r in self.body['rows']])   # no device name: the hostname is used
        self.assertEqual((self.body['available'], self.body['loaded']), (True, True))

    def test_device_types_and_row_status(self):
        rows = self.rows()
        self.assertEqual([rows[n]['device_type'] for n in ('SRV-FILE', 'PC-RECEPTION', 'm365-mailboxes')], ['Server', 'Workstation', 'Microsoft 365'])
        self.assertEqual([rows[n]['rowStatus'] for n in ('SRV-FILE', 'PC-RECEPTION', 'm365-mailboxes')], [None, 'warn', 'bad'])

    def test_red_flags_come_from_the_same_snapshot_and_a_missing_category_is_a_session_failure(self):
        rows = self.rows()
        self.assertEqual(rows['SRV-FILE']['flags'], [])
        self.assertEqual(rows['PC-RECEPTION']['flags'], [{'category': 'retention', 'flag': 'RETENTION SHORT: 45 days (recommended: 90+)'}])
        self.assertEqual([f['category'] for f in rows['m365-mailboxes']['flags']], ['backup_currency', 'session_failure'])
        self.assertEqual(rows['m365-mailboxes']['reason'], 'NO SUCCESSFUL BACKUP IN 12 DAYS; Exchange: Failed')

    def test_clients_are_matched_by_the_cove_customer_id_then_by_name(self):
        rows = self.rows()
        self.assertEqual(rows['SRV-FILE']['client_id'], 1)           # "Acme" is Cove customer 4, whose name is also the client's
        self.assertEqual(rows['PC-RECEPTION']['client_id'], 2)       # not in cove.customers: matched by the client's name
        self.assertIsNone(rows['m365-mailboxes']['client_id'])       # unmapped is never guessed

    def test_a_cove_id_link_wins_over_a_name(self):
        from api.clients import Resolver
        resolver = Resolver({('cove', '4'): 7}, {'acme': 1})
        rows = self.rows(resolver=resolver)
        self.assertEqual(rows['SRV-FILE']['client_id'], 7)

    def test_the_current_devices_are_the_newest_snapshot_of_each_load_scope(self):
        self.rows()
        sql = next(s for s, _ in self.fake.calls if 'SELECT DISTINCT ON (d.device_id)' in s)
        self.assertIn('IS NOT DISTINCT FROM s.customer_filter', sql)    # the whole-estate load is a scope as well
        self.assertIn('ORDER BY d.device_id, d.snapshot_id DESC', sql)  # a device in several snapshots: the newest

    def test_values_are_carried_through_and_nothing_is_invented(self):
        rows = self.rows()
        pc = rows['PC-RECEPTION']
        self.assertIsNone(pc['used_storage_bytes'])
        self.assertIsNone(pc['immutable_enabled'])
        self.assertEqual(rows['SRV-FILE']['used_storage_bytes'], 3 * 1024 ** 3)
        self.assertEqual(rows['m365-mailboxes']['days_since_success'], 12)
        self.assertTrue(rows['SRV-FILE']['captured_at'].endswith('Z'))
        self.assertGreaterEqual(rows['SRV-FILE']['data_age_hours'], 0)

    def test_health_text_is_normalised_and_a_missing_customer_is_unspecified(self):
        rows = self.rows(cove_devices=[cove_device(health='warning', customer=None)], cove_flags=[])
        self.assertEqual((rows['SRV-FILE']['health'], rows['SRV-FILE']['customer'], rows['SRV-FILE']['rowStatus']), ('WARNING', 'Unspecified', 'warn'))

    def test_nothing_collected_yet_is_not_the_same_as_no_cove_schema(self):
        self.rows(cove_devices=[])
        self.assertEqual((self.body['available'], self.body['loaded'], self.body['rows']), (True, False, []))
        self.rows(cove_tables=[])
        self.assertEqual((self.body['available'], self.body['loaded']), (False, False))
        self.assertTrue(self.body['columns'])      # the page can still draw its table headings
        self.assertFalse(any('cove.device' in s for s, _ in self.fake.calls))   # nothing else was queried

    def test_a_cove_schema_without_the_optional_tables_still_works(self):
        rows = self.rows(cove_tables=['snapshot', 'device'])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows['PC-RECEPTION']['flags'], [])
        self.assertEqual(rows['SRV-FILE']['client_id'], 1)    # no cove.customers: matched by name

    def test_detail_for_one_device(self):
        with FakeReports() as fake:
            detail = data.get_section('backup-detail', params={'device': '2'})
        self.assertEqual(detail['device']['device_name'], 'PC-RECEPTION')
        self.assertEqual(detail['device']['health'], 'WARNING')
        self.assertEqual(detail['flags'][0]['category'], 'retention')
        self.assertEqual(detail['datasources'][0]['ds_name'], 'Files')
        self.assertTrue(detail['datasources'][0]['last_success'].endswith('Z'))
        self.assertEqual(detail['m365'], [])
        sessions_sql, params = next(call for call in fake.calls if 'FROM cove.datasource_session' in call[0])
        self.assertEqual(params, [21, 2])                      # the snapshot and the device the list used

    def test_detail_for_an_unknown_device_or_without_cove(self):
        with FakeReports():
            self.assertIsNone(data.get_section('backup-detail', params={'device': '999'}))
        with FakeReports(cove_tables=[]):
            self.assertIsNone(data.get_section('backup-detail', params={'device': '1'}))


class OverviewData(SimpleTestCase):
    """Every client's own scores. The numbers below are worked out by hand from api.test_support.default_org_groups()."""

    def overview(self, **kwargs):
        user = kwargs.pop('user', None)
        with FakeReports(**kwargs) as fake:
            self.fake = fake
            body = data.get_section('patch-overview', user=user)
        return {c['key']: c for c in body['clients']}

    def scores(self, client):
        return {r['key']: (r['current'], r['previous']) for r in client['rows']}

    def test_each_client_has_its_own_scores_and_the_customers_of_one_client_are_added_together(self):
        clients = self.overview()
        self.assertEqual(list(clients), ['client:1', 'org:30'])                       # ordered by name; the unlinked customer is shown too
        acme = clients['client:1']
        # org units 10 and 20: security 100 patches, 50 missing now (50.0%) and 60 missing before (40.0%); third party 20 with 5 missing (75.0%)
        self.assertEqual(self.scores(acme), {'windows-security': (50.0, 40.0), 'windows-patching': (None, None), 'third-party': (75.0, None)})
        self.assertEqual(acme['overall'], {'current': 62.5, 'previous': 40.0})
        self.assertEqual((acme['label'], acme['linked'], acme['devices'], acme['client'], acme['org']), ('Acme', True, 8, 1, None))

    def test_a_customer_that_is_not_linked_to_a_client_appears_under_its_own_name(self):
        blue = self.overview()['org:30']
        self.assertEqual((blue['label'], blue['linked'], blue['client'], blue['org']), ('Blue Crane', False, None, 30))
        self.assertEqual(self.scores(blue)['windows-security'], (100.0, 50.0))
        self.assertEqual(blue['overall'], {'current': 100.0, 'previous': 50.0})

    def test_the_rows_are_the_three_types_without_the_group_heading(self):
        self.assertEqual([r['key'] for r in self.overview()['client:1']['rows']], ['windows-security', 'windows-patching', 'third-party'])

    def test_devices_with_no_customer_are_left_out(self):
        clients = self.overview()
        self.assertEqual(self.scores(clients['client:1'])['windows-security'][0], 50.0)     # the 99-of-99 row with no customer would have dragged this down

    def test_it_is_two_queries_however_many_clients_there_are(self):
        self.overview()
        grouped = [sql for sql, _ in self.fake.calls if 'GROUP BY 1, 2, 3, 4' in sql]
        self.assertEqual(len(grouped), 2)
        self.assertEqual(sorted('inventory_rank = 2' in sql for sql in grouped), [False, True])
        self.assertTrue(all('JOIN ncentral.device dev' in sql and 'dev.org_unit_id' in sql for sql in grouped))
        self.assertFalse(any('dev.org_unit_id IN' in sql for sql in grouped))                # no limit for someone who may see everything

    def test_the_viewers_own_weights_are_used(self):
        clients = self.overview(weights={'third-party': 3})
        self.assertEqual(clients['client:1']['overall']['current'], 68.75)                  # (50 x 1 + 75 x 3) / 4

    def test_a_client_with_no_patch_data_still_has_a_group_with_empty_scores(self):
        clients = self.overview(org_groups=([], []))
        self.assertEqual(list(clients), ['client:1', 'org:30'])
        self.assertEqual(clients['client:1']['overall'], {'current': None, 'previous': None})

    def test_the_overview_can_be_limited_to_some_clients_and_then_reads_only_their_customers(self):
        with FakeReports() as fake:
            from api.data import patches
            body = patches.overview({}, None, allowed={1})
        self.assertEqual([c['key'] for c in body['clients']], ['client:1'])
        grouped = [sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3, 4' in sql]
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql in grouped))

    def test_someone_limited_to_no_clients_sees_no_one_and_reads_nothing(self):
        with FakeReports() as fake:
            from api.data import patches
            body = patches.overview({}, None, allowed=frozenset())
        self.assertEqual(body['clients'], [])
        self.assertTrue(all(' AND FALSE' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3, 4' in sql))


class SeveralClients(SimpleTestCase):
    """The patch queries can be limited to several clients and customers at once; they add together."""

    def run_patches(self, params, name='patches'):
        with FakeReports() as fake:                                         # client 1 has customers 10 and 20; client 2 has none
            body = data.get_section(name, params=params, allowed=None)
        self.sql = [sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql or 'FROM named' in sql]
        return body

    def test_two_clients_are_the_customers_of_both(self):
        self.run_patches({'client': '1,2'})
        self.assertTrue(self.sql and all('dev.org_unit_id IN (10, 20)' in sql for sql in self.sql))

    def test_clients_and_customers_add_together_instead_of_one_winning(self):
        self.run_patches({'client': '1', 'org': '30'})
        self.assertTrue(all('dev.org_unit_id IN (10, 20, 30)' in sql for sql in self.sql))

    def test_several_customers_alone(self):
        body = self.run_patches({'org': '30,10'})
        self.assertTrue(all('dev.org_unit_id IN (10, 30)' in sql for sql in self.sql))
        self.assertTrue(body['mapped'])
        self.assertEqual((body['client'], body['org']), (None, '30,10'))

    def test_a_client_with_no_customer_adds_nothing_and_alone_reads_nothing(self):
        body = self.run_patches({'client': '2'})
        self.assertFalse(body['mapped'])
        self.assertTrue(all(' AND FALSE' in sql for sql in self.sql))
        self.run_patches({'client': '2', 'org': '30'})
        self.assertTrue(all('dev.org_unit_id IN (30)' in sql for sql in self.sql))

    def test_one_client_still_works_as_before(self):
        self.run_patches({'client': '1'})
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql in self.sql))

    def test_the_entries_follow_the_same_rule(self):
        self.run_patches({'type': 'windows-security', 'category': 'g1', 'client': '1', 'org': '30'}, 'patch-entries')
        self.assertTrue(all('dev.org_unit_id IN (10, 20, 30)' in sql for sql in self.sql if 'FROM named' in sql))

    def test_the_overall_score_is_for_the_chosen_scope_only(self):
        with FakeReports() as fake:
            data.get_section('patches', params={'client': '1,2'}, allowed=None)
        self.assertFalse(any('IS NOT NULL' in sql and 'IN (' not in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql))


class CombinedTrend(SimpleTestCase):
    """Several clients' history taken together: the saved counts are added up per run and scored, never averaged."""

    def comp(self, kind, installed, missing, category='c', category_id='g'):
        return {'type': kind, 'category_id': category_id, 'category': category, 'installed': installed, 'missing': missing, 'compliance': None}

    def snap(self, run, when, components, weights=None):
        return {'run_id': run, 'captured_at': when, 'weights': weights or {}, 'components': components}

    def test_counts_are_added_up_then_scored(self):
        from api.snapshots import combine
        a = self.snap(1, '2026-11-01', [self.comp('windows-security', 60, 0), self.comp('third-party', 5, 5)])
        b = self.snap(1, '2026-11-01', [self.comp('windows-security', 40, 0), self.comp('third-party', 5, 5)])
        result = combine([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['scores'], {'overall': 75.0, 'windows-security': 100.0, 'windows-patching': None, 'third-party': 50.0})   # 100 of 100 and 10 of 20

    def test_it_is_the_combined_position_not_the_average_of_the_two_scores(self):
        from api.snapshots import combine
        big = self.snap(1, 'x', [self.comp('windows-security', 99, 1)])         # 99.0 %
        small = self.snap(1, 'x', [self.comp('windows-security', 0, 1)])        # 0.0 %
        self.assertEqual(combine([big, small])[0]['scores']['windows-security'], 98.02)   # 99 installed of 101 is 98.02 %, not (99.0 + 0.0) / 2 = 49.5

    def test_the_weights_saved_for_the_run_are_used(self):
        from api.snapshots import combine
        snap = self.snap(1, 'x', [self.comp('windows-security', 100, 0), self.comp('third-party', 5, 5)], weights={'third-party': 3})
        self.assertEqual(combine([snap])[0]['scores']['overall'], 62.5)          # (100 x 1 + 50 x 3) / 4

    def test_each_run_stands_alone_and_a_client_missing_from_a_run_is_just_not_in_it(self):
        from api.snapshots import combine
        both = [self.snap(1, '2026-11-01', [self.comp('windows-security', 60, 0)]), self.snap(1, '2026-11-01', [self.comp('windows-security', 40, 0)]),
                self.snap(2, '2026-11-02', [self.comp('windows-security', 50, 50)])]          # only one client has a snapshot for run 2
        first, second = combine(both)
        self.assertEqual((first['scores']['windows-security'], second['scores']['windows-security']), (100.0, 50.0))

    def test_runs_come_out_in_run_order_with_the_latest_time_of_the_run(self):
        from api.snapshots import combine
        snaps = [self.snap(2, '2026-11-02', [self.comp('windows-security', 1, 0)]), self.snap(1, '2026-11-01', [self.comp('windows-security', 1, 0)]),
                 self.snap(1, '2026-11-03', [self.comp('windows-security', 1, 0)])]
        result = combine(snaps)
        self.assertEqual([r['captured_at'] for r in result], ['2026-11-03', '2026-11-02'])   # run 1 then run 2; run 1's time is its latest snapshot

    def test_nothing_to_score_is_not_invented(self):
        from api.snapshots import combine
        result = combine([self.snap(1, 'x', [])])
        self.assertEqual(result[0]['scores']['overall'], None)
        self.assertEqual(combine([]), [])

    def test_the_saved_snapshots_are_not_changed(self):
        from api.snapshots import combine
        snaps = [self.snap(1, 'x', [self.comp('windows-security', 5, 5)])]
        import copy
        before = copy.deepcopy(snaps)
        combine(snaps)
        self.assertEqual(snaps, before)


class ServerPatching(SimpleTestCase):
    """Server Health shows each server's patching, taken from the N-central device with the same name inside the same client."""
    NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    ORG_MAP = {'10': (1, 'Acme'), '20': (1, 'Acme'), '30': (2, 'Zed')}

    def summary(self, servers, devices, installed=(), backlog=(), **kwargs):
        from api.data.servers import patch_summary
        return patch_summary(servers, devices, list(installed), list(backlog), self.ORG_MAP, self.NOW, **kwargs)

    def server(self, name='SRV-A', client=1, agent='a1'):
        return {'agent_id': agent, 'computer_name': name, 'client_id': client}

    def device(self, id, name, org=10):
        return {'device_id': id, 'device_name': name, 'org_unit_id': org}

    def at(self, days):   # a time `days` before NOW, as N-central stores it (no time zone)
        return (self.NOW - timedelta(days=days)).replace(tzinfo=None)

    def inventory(self, device=101, kind='windows-security', last=None, missing=0, entries=1):   # one device's newest inventory for one category
        return {'device_id': device, 'patch_type': kind, 'last_installed_at': last, 'missing_now': missing, 'entries': entries}

    def one(self, installed=(), backlog=(), kind=0, **kwargs):   # the category (0, 1 or 2) of the one matched server
        return self.summary([self.server()], [self.device(101, 'srv-a')], installed, backlog, **kwargs)['a1']['categories'][kind]

    # ---- what counts as the same name
    def test_two_spellings_of_one_computer_name_are_the_same_key(self):
        from api.data.servers import device_key
        self.assertEqual({device_key(n) for n in ('SRV01', 'srv01', ' SRV01 ', 'srv01.corp.local', 'SRV01.ACME.LOCAL')}, {'srv01'})
        self.assertEqual(device_key('192.168.1.5'), '192.168.1.5')                     # an address is not a name with a domain
        self.assertEqual((device_key(''), device_key(None), device_key('   ')), ('', '', ''))
        self.assertNotEqual(device_key('srv01'), device_key('srv010'))                 # nothing is matched by "starts with"

    # ---- who is matched to whom
    def test_a_server_is_matched_to_the_device_with_its_name_in_its_own_client(self):
        result = self.summary([self.server('SRV-A')], [self.device(101, 'srv-a.acme.local')])
        self.assertEqual((result['a1']['status'], result['a1']['device_name']), ('matched', 'srv-a.acme.local'))

    def test_the_same_name_in_another_client_is_not_a_match(self):
        result = self.summary([self.server('SRV-A', client=1)], [self.device(301, 'srv-a', org=30)])     # a device of client 2
        self.assertEqual(result['a1']['status'], 'no-match')

    def test_the_same_name_in_two_clients_matches_each_server_to_its_own_device(self):
        servers = [self.server('srv-a', client=1, agent='a1'), self.server('srv-a', client=2, agent='a2')]
        devices = [self.device(101, 'srv-a', org=10), self.device(301, 'SRV-A', org=30)]
        installed = [self.inventory(101, last=self.at(2)), self.inventory(301, last=self.at(50))]
        result = self.summary(servers, devices, installed)
        self.assertEqual([result[a]['categories'][0]['days_since_last'] for a in ('a1', 'a2')], [2.0, 50.0])

    def test_a_name_that_two_devices_share_in_one_client_is_not_guessed(self):
        result = self.summary([self.server('SRV-A')], [self.device(101, 'srv-a', org=10), self.device(102, 'SRV-A.acme.local', org=20)])
        self.assertEqual(result['a1'], {'status': 'ambiguous'})

    def test_a_server_linked_to_no_client_has_nothing_safe_to_match_in(self):
        self.assertEqual(self.summary([self.server('SRV-A', client=None)], [self.device(101, 'srv-a')])['a1'], {'status': 'unlinked'})

    def test_a_device_in_an_org_unit_linked_to_no_client_cannot_be_matched(self):
        self.assertEqual(self.summary([self.server('SRV-A')], [self.device(101, 'srv-a', org=99)])['a1']['status'], 'no-match')

    def test_a_server_with_no_name_matches_nothing(self):
        self.assertEqual(self.summary([self.server('')], [self.device(101, '')])['a1']['status'], 'no-match')

    # ---- what a matched server shows
    def test_the_three_overview_categories_in_the_overview_order(self):
        from api.data.patches import _PATCH_TYPES
        categories = self.summary([self.server()], [self.device(101, 'srv-a')])['a1']['categories']
        self.assertEqual([(c['key'], c['label']) for c in categories], list(_PATCH_TYPES))

    def test_the_last_added_date_and_how_long_ago(self):
        category = self.one([self.inventory(last=self.at(3.5))])
        self.assertEqual((category['last_installed_at'], category['days_since_last']), ('2026-11-08T00:00:00Z', 3.5))

    def test_a_category_with_no_dated_install_has_no_date_and_is_not_made_up(self):
        categories = self.summary([self.server()], [self.device(101, 'srv-a')], [self.inventory(kind='third-party', last=None, missing=1)])['a1']['categories']
        self.assertEqual([(c['last_installed_at'], c['days_since_last']) for c in categories], [(None, None)] * 3)

    def test_next_due_is_always_empty_because_no_patch_schedule_is_collected(self):
        categories = self.summary([self.server()], [self.device(101, 'srv-a')], [self.inventory(last=self.at(1))])['a1']['categories']
        self.assertEqual([c['next_due'] for c in categories], [None, None, None])
        self.assertTrue(all('next_due' in c for c in categories))

    # ---- up to date: nothing missing, and nothing more
    def test_up_to_date_means_the_inventory_lists_patches_of_that_kind_and_none_is_missing(self):
        self.assertIs(self.one([self.inventory(missing=0, entries=7)])['up_to_date'], True)

    def test_a_category_with_anything_missing_is_not_up_to_date_however_little_or_new(self):
        category = self.one([self.inventory(missing=1, entries=40)], [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 1, 'oldest_missing_at': self.at(0.5)}])
        self.assertEqual((category['up_to_date'], category['missing'], category['overdue']), (False, 1, False))

    def test_a_category_the_inventory_does_not_list_is_neither_up_to_date_nor_not(self):
        self.assertIsNone(self.one([self.inventory(kind='third-party')], kind=0)['up_to_date'])             # nothing listed for security: unknown, not a tick
        self.assertIsNone(self.one([], kind=2)['up_to_date'])
        self.assertEqual(self.one([], kind=2)['missing'], 0)

    def test_a_row_without_the_missing_count_cannot_make_a_category_up_to_date(self):
        category = self.one([{'device_id': 101, 'patch_type': 'windows-security', 'last_installed_at': self.at(2)}])
        self.assertIsNone(category['up_to_date'])

    def test_the_missing_count_is_the_inventorys_not_the_backlogs(self):
        category = self.one([self.inventory(missing=2)], [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 9, 'oldest_missing_at': self.at(3)}])
        self.assertEqual(category['missing'], 2)                                                           # the backlog gives how long, not how many

    # ---- overdue: something is missing, and has been for more than the grace period
    def test_a_patch_missing_for_more_than_the_grace_period_is_overdue_and_exactly_the_grace_period_is_not(self):
        def overdue(days):
            return self.one([self.inventory(missing=1)], [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 1, 'oldest_missing_at': self.at(days)}])['overdue']
        self.assertEqual([overdue(13.9), overdue(14), overdue(14.1), overdue(60)], [False, False, True, True])

    def test_an_old_backlog_entry_does_not_make_a_category_overdue_when_nothing_is_missing_now(self):
        category = self.one([self.inventory(missing=0)], [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 1, 'oldest_missing_at': self.at(90)}])
        self.assertEqual((category['up_to_date'], category['overdue']), (True, False))                      # the two can never contradict each other

    def test_the_grace_period_can_be_changed_in_one_place(self):
        backlog = [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 1, 'oldest_missing_at': self.at(20)}]
        self.assertFalse(self.one([self.inventory(missing=1)], backlog, grace_days=30)['overdue'])

    def test_each_category_is_judged_on_its_own(self):
        installed = [self.inventory(kind='third-party', missing=3, last=self.at(1)), self.inventory(kind='windows-security', missing=0, last=self.at(1))]
        backlog = [{'device_id': 101, 'patch_type': 'third-party', 'missing': 3, 'oldest_missing_at': self.at(40)}]
        categories = {c['key']: c for c in self.summary([self.server()], [self.device(101, 'srv-a')], installed, backlog)['a1']['categories']}
        self.assertEqual([(k, categories[k]['overdue'], categories[k]['up_to_date']) for k in categories],
                         [('windows-security', False, True), ('windows-patching', False, None), ('third-party', True, False)])
        self.assertIsNone(categories['windows-security']['oldest_missing_days'])

    def test_a_patch_missing_on_another_device_does_not_count_for_this_one(self):
        backlog = [{'device_id': 999, 'patch_type': 'windows-security', 'missing': 5, 'oldest_missing_at': self.at(90)}]
        self.assertFalse(self.one([self.inventory(missing=0)], backlog)['overdue'])

    # ---- the page's own answer
    def health(self, **fake):
        with FakeReports(**fake):
            return data.get_section('server-health', allowed=None)

    def test_the_page_carries_each_servers_patching_and_says_it_is_available(self):
        body = self.health()
        servers = {s['computer_name']: s for s in body['servers']}
        self.assertEqual(body['patch_info'], {'available': True, 'grace_days': 14, 'matched': 2, 'disconnected': 1})
        self.assertEqual(servers['SRV-A']['patches']['status'], 'matched')
        security, patching, third = servers['SRV-A']['patches']['categories']
        self.assertEqual((round(security['days_since_last']), security['missing'], security['up_to_date'], security['overdue']), (3, 2, False, True))   # installed 3 days ago, yet a patch missing 20
        self.assertEqual((patching['up_to_date'], patching['missing'], patching['overdue']), (True, 0, False))
        self.assertEqual((third['last_installed_at'], third['up_to_date'], third['overdue']), (None, False, False))                                  # missing 5 days: inside the grace period
        self.assertTrue(servers['SRV-B']['patches']['categories'][1]['overdue'])
        self.assertIsNone(servers['SRV-B']['patches']['categories'][2]['up_to_date'])                                                               # nothing listed
        self.assertEqual(servers['PC-1']['patches']['status'], 'no-match')                                                                          # Zed has no device by that name

    def test_if_the_patching_cannot_be_read_the_page_still_loads_without_it(self):
        with self.assertLogs('api.data.servers', level='ERROR') as logged:   # logged for the administrator, not printed
            body = self.health(server_patch_error=True)
        self.assertIn('Could not read the patching', logged.output[0])
        self.assertEqual(len(body['servers']), 3)
        self.assertEqual(body['patch_info'], {'available': False, 'grace_days': 14, 'matched': 0, 'disconnected': 0})
        self.assertTrue(all(s['patches'] == {'status': 'unavailable'} for s in body['servers']))
        for leaked in ('patch_backlog', 'does not exist', 'relation', 'RuntimeError'):                                   # the reason is logged, never sent
            self.assertNotIn(leaked, str(body))

    def test_a_person_limited_to_one_client_gets_only_their_servers_and_their_patching(self):
        with FakeReports():
            body = data.get_section('server-health', allowed=frozenset({1}))
        self.assertEqual([s['computer_name'] for s in body['servers']], ['SRV-A', 'SRV-B'])
        self.assertTrue(all(s['patches']['status'] == 'matched' for s in body['servers']))

    def test_the_queries_are_fixed_read_only_text_with_a_marker_and_no_input(self):
        from api.data import servers
        for sql in (servers._DEVICES_SQL, servers._installed_sql(), servers._BACKLOG_SQL, servers._ATTEMPT_SQL, servers._good_sql()):
            self.assertTrue(sql.startswith('/* server-patch-'))
            self.assertNotIn('%s', sql)
            self.assertFalse(any(word in sql.upper() for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER ')))
        self.assertIn('inventory_rank = 1', servers._installed_sql())                                                   # the newest usable inventory, like the Overview
        self.assertIn('missing_now', servers._installed_sql())                                                          # what is missing is counted there too
        self.assertIn('resolved_at IS NULL', servers._BACKLOG_SQL)                                                      # only what is still missing


class PatchCalculation(SimpleTestCase):
    """The patch scores come with the working behind them."""

    def patches(self, **kwargs):
        with FakeReports(**kwargs):
            return data.get_section('patches', allowed=None)

    def test_the_answer_carries_the_working_and_it_ends_at_the_score_shown(self):
        body = self.patches()
        calc = body['calculation']
        self.assertEqual(calc['overall']['score'], body['overall']['current'])
        self.assertEqual([t['score'] for t in calc['types']], [r['current'] for r in body['rows'] if not r.get('group')])
        self.assertEqual([t['key'] for t in calc['types']], ['windows-security', 'windows-patching', 'third-party'])

    def test_the_working_uses_the_weights_that_made_the_score(self):
        body = self.patches(weights={'third-party': 3})
        weights = {t['key']: t['weight'] for t in body['calculation']['types']}
        self.assertEqual(weights, {'windows-security': 1, 'windows-patching': 1, 'third-party': 3})
        self.assertEqual(body['calculation']['overall']['score'], body['overall']['current'])

    def test_a_category_is_named_as_it_is_in_the_tables(self):
        names = [c['category'] for t in self.patches()['calculation']['types'] for c in t['categories']]
        self.assertTrue(names)
        self.assertEqual(names, [c['category'] for t in self.patches()['types'] for c in t['categories']])


class DisconnectedDevices(SimpleTestCase):
    """NC-01 (12 Nov 2026): a device that N-central cannot find (its patch collection answered 404 DeviceNotFoundException) counts as disconnected,
    and Server Health says so for that device. The Data Collection page still calls the run Partial."""

    def attempt(self, error=None, coverage='full'):
        return {'device_id': 101, 'captured_at': datetime(2026, 11, 11, tzinfo=timezone.utc), 'coverage': coverage, 'error': error}

    def test_a_usable_collection_is_ok(self):
        from api.data.servers import collection_state
        self.assertEqual(collection_state(self.attempt()), 'ok')

    def test_a_404_or_device_not_found_is_disconnected_whichever_way_it_is_worded(self):
        from api.data.servers import collection_state
        for error in ('Request failed (404) on /api/devices/1/assets: x', 'DeviceNotFoundException: Device was not found!', 'devicenotfound', 'HTTP 404'):
            self.assertEqual(collection_state(self.attempt(error, 'unavailable')), 'disconnected', error)

    def test_any_other_unusable_result_is_a_failure_not_a_disconnection(self):
        from api.data.servers import collection_state
        for error, coverage in (('Request failed (500) on /api/devices/1/assets', 'unavailable'), ('ConnectTimeoutError: connect timeout=15', 'unavailable'),
                                (None, 'unavailable'), ('', 'full')):
            self.assertEqual(collection_state(self.attempt(error, coverage)), 'failed', (error, coverage))

    def test_a_device_never_tried_has_no_state(self):
        from api.data.servers import collection_state
        self.assertIsNone(collection_state(None))

    def test_the_rule_is_the_overviews_rule_an_error_or_unavailable_coverage_is_unusable(self):
        from api.data.servers import collection_state
        self.assertEqual(collection_state(self.attempt(None, 'partial')), 'ok')            # any coverage but unavailable, with no error, is usable
        self.assertNotEqual(collection_state(self.attempt('x', 'full')), 'ok')

    def test_the_server_page_says_which_servers_are_disconnected(self):
        with FakeReports():
            body = data.get_section('server-health', allowed=None)
        states = {s['computer_name']: s['patches']['collection']['state'] for s in body['servers'] if s['patches']['status'] == 'matched'}
        self.assertEqual(states, {'SRV-A': 'ok', 'SRV-B': 'disconnected'})
        self.assertEqual(body['patch_info']['disconnected'], 1)

    def test_a_disconnected_server_still_shows_its_last_successful_inventory_and_its_date(self):
        with FakeReports():
            body = data.get_section('server-health', allowed=None)
        srv_b = next(s for s in body['servers'] if s['computer_name'] == 'SRV-B')
        self.assertEqual(round(srv_b['patches']['inventory']['days_ago']), 3)               # the last good one, not the failed attempt from half a day ago
        self.assertEqual(round(srv_b['patches']['collection']['days_ago'], 1), 0.5)         # when the failed attempt was
        self.assertEqual(srv_b['patches']['categories'][0]['up_to_date'], True)             # the figures are from that last good inventory

    def test_no_error_text_reaches_the_browser(self):
        with FakeReports():
            body = data.get_section('server-health', allowed=None)
        self.assertNotIn('DeviceNotFound', str(body))
        self.assertNotIn('/api/devices', str(body))

    def test_the_other_device_that_answered_fine_is_not_marked(self):
        from api.data.servers import patch_summary
        devices = [{'device_id': 101, 'device_name': 'a', 'org_unit_id': 10, 'last_seen': None}, {'device_id': 102, 'device_name': 'b', 'org_unit_id': 10, 'last_seen': None}]
        servers = [{'agent_id': 'x', 'computer_name': 'a', 'client_id': 1}, {'agent_id': 'y', 'computer_name': 'b', 'client_id': 1}]
        attempts = [self.attempt('Request failed (404) on /api/devices/101/assets', 'unavailable'), {**self.attempt(), 'device_id': 102}]
        result = patch_summary(servers, devices, [], [], {'10': (1, 'Acme')}, datetime(2026, 11, 11, 12, tzinfo=timezone.utc), attempts=attempts)
        self.assertEqual((result['x']['collection']['state'], result['y']['collection']['state']), ('disconnected', 'ok'))


class ServerDataCollected(SimpleTestCase):
    """The Data Collected section: the latest date found for each device on a successful run of each collector."""
    NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

    def server(self, name='SRV-A', client=1, agent='a1', at='2026-11-10T12:00:00Z', hours=24.0):
        return {'agent_id': agent, 'computer_name': name, 'client_id': client, 'last_collected_at': at, 'data_age_hours': hours}

    def matched(self, seen=2.0, inventory=3.0):
        return {'status': 'matched', 'device_name': 'srv-a', 'seen': {'at': 'S', 'days_ago': seen} if seen is not None else None,
                'inventory': {'at': 'I', 'days_ago': inventory} if inventory is not None else None, 'categories': [], 'collection': {'state': 'ok'}}

    def cove(self, *rows, state='ok'):
        return {'state': state, 'rows': [{'snapshot_id': 1, 'device_id': i, 'client_id': 1, 'device_name': 'srv-a', 'hostname': None, 'captured_at': '2026-11-09T06:00:00Z',
                                          'data_age_hours': 60.0, **row} for i, row in enumerate(rows, start=1)]}

    def summary(self, servers=None, patching=None, cove=None):
        from api.data.servers import collected_summary
        servers = servers or [self.server()]
        return collected_summary(servers, {'a1': self.matched()} if patching is None else patching, cove or self.cove({}), self.NOW)

    def by_key(self, items):
        return {i['key']: i for i in items}

    def test_every_server_has_the_four_collectors_in_a_fixed_order(self):
        self.assertEqual([i['key'] for i in self.summary()['a1']], ['sentinelone', 'ncentral-device', 'ncentral-patches', 'cove'])

    def test_sentinelone_is_the_servers_own_record_and_always_found(self):
        item = self.by_key(self.summary()['a1'])['sentinelone']
        self.assertEqual((item['state'], item['at'], item['days_ago']), ('found', '2026-11-10T12:00:00Z', 1.0))

    def test_n_central_gives_when_the_device_was_seen_and_when_its_patch_inventory_was_captured(self):
        items = self.by_key(self.summary()['a1'])
        self.assertEqual([(items[k]['state'], items[k]['at'], items[k]['days_ago']) for k in ('ncentral-device', 'ncentral-patches')], [('found', 'S', 2.0), ('found', 'I', 3.0)])

    def test_a_matched_device_with_no_recorded_date_is_none_not_made_up(self):
        items = self.by_key(self.summary(patching={'a1': self.matched(seen=None, inventory=None)})['a1'])
        self.assertEqual([(items[k]['state'], items[k]['at']) for k in ('ncentral-device', 'ncentral-patches')], [('none', None), ('none', None)])

    def test_an_unmatched_server_says_why_for_both_n_central_items(self):
        for status in ('no-match', 'ambiguous', 'unlinked'):
            items = self.by_key(self.summary(patching={'a1': {'status': status}})['a1'])
            self.assertEqual((items['ncentral-device']['state'], items['ncentral-patches']['state']), (status, status))
            self.assertEqual(items['sentinelone']['state'], 'found')               # the other collectors are unaffected

    def test_patching_that_could_not_be_read_makes_only_the_n_central_items_unavailable(self):
        from api.data.servers import collected_summary
        items = self.by_key(collected_summary([self.server()], None, self.cove({}), self.NOW)['a1'])
        self.assertEqual([items[k]['state'] for k in ('sentinelone', 'ncentral-device', 'ncentral-patches', 'cove')], ['found', 'unavailable', 'unavailable', 'found'])

    def test_a_cove_device_with_the_servers_name_in_its_client_is_matched_and_gives_its_capture_date(self):
        item = self.by_key(self.summary()['a1'])['cove']
        self.assertEqual((item['state'], item['at'], item['days_ago']), ('found', '2026-11-09T06:00:00Z', 2.5))

    def test_a_cove_device_is_matched_by_its_hostname_too_and_ignoring_case_and_domain(self):
        cove = self.cove({'device_name': 'Finance server', 'hostname': 'SRV-A.acme.local'})
        self.assertEqual(self.by_key(self.summary(cove=cove)['a1'])['cove']['state'], 'found')

    def test_the_same_name_in_another_client_is_not_a_cove_match(self):
        self.assertEqual(self.by_key(self.summary(cove=self.cove({'client_id': 2}))['a1'])['cove']['state'], 'no-match')

    def test_two_cove_devices_with_the_name_in_one_client_are_not_guessed(self):
        self.assertEqual(self.by_key(self.summary(cove=self.cove({}, {}))['a1'])['cove']['state'], 'ambiguous')

    def test_one_cove_device_matched_through_both_its_name_and_hostname_is_one_device_not_two(self):
        cove = self.cove({'device_name': 'srv-a', 'hostname': 'SRV-A.acme.local'})
        self.assertEqual(self.by_key(self.summary(cove=cove)['a1'])['cove']['state'], 'found')

    def test_a_server_linked_to_no_client_cannot_be_matched_in_cove(self):
        self.assertEqual(self.by_key(self.summary([self.server(client=None)])['a1'])['cove']['state'], 'unlinked')

    def test_cove_states_when_it_has_nothing_or_cannot_be_read(self):
        for state in ('not-collected', 'unavailable'):
            self.assertEqual(self.by_key(self.summary(cove=self.cove(state=state))['a1'])['cove']['state'], state)

    def test_a_cove_device_with_no_capture_date_is_none(self):
        item = self.by_key(self.summary(cove=self.cove({'captured_at': None}))['a1'])['cove']
        self.assertEqual((item['state'], item['at']), ('none', None))

    def health(self, **fake):
        with FakeReports(**fake):
            return data.get_section('server-health', allowed=None)

    def test_the_page_carries_the_four_collectors_for_every_server(self):
        body = self.health()
        self.assertTrue(all([i['key'] for i in s['collected']] == ['sentinelone', 'ncentral-device', 'ncentral-patches', 'cove'] for s in body['servers']))

    def test_the_page_finds_the_cove_backup_of_a_server_with_the_same_name(self):
        body = self.health(cove_devices=[cove_device(device_name='SRV-A', hostname='srv-a', customer='Acme')])
        srv_a = next(s for s in body['servers'] if s['computer_name'] == 'SRV-A')
        self.assertEqual(self.by_key(srv_a['collected'])['cove']['state'], 'found')

    def test_a_disconnected_device_shows_the_date_of_its_last_successful_patch_inventory_not_the_failed_attempt(self):
        srv_b = next(s for s in self.health()['servers'] if s['computer_name'] == 'SRV-B')
        self.assertEqual(round(self.by_key(srv_b['collected'])['ncentral-patches']['days_ago']), 3)

    def test_if_cove_cannot_be_read_the_page_still_loads(self):
        from unittest.mock import patch
        with patch('api.data.servers.backups.backups', side_effect=RuntimeError('relation "cove.device" does not exist')), \
                self.assertLogs('api.data.servers', level='ERROR'):
            body = self.health()
        self.assertEqual(len(body['servers']), 3)
        self.assertTrue(all(self.by_key(s['collected'])['cove']['state'] == 'unavailable' for s in body['servers']))
        self.assertNotIn('cove.device', str(body))                                             # the reason is logged, never sent

    def test_when_the_patching_cannot_be_read_the_other_collectors_still_show(self):
        with self.assertLogs('api.data.servers', level='ERROR'):
            body = self.health(server_patch_error=True)
        for server in body['servers']:
            items = self.by_key(server['collected'])
            self.assertEqual((items['sentinelone']['state'], items['ncentral-patches']['state']), ('found', 'unavailable'))
