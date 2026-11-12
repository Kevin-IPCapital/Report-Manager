"""The record of gaps between the source systems and the master client list: what counts as a gap, and what to do with the record."""
from django.test import SimpleTestCase

from api.clients import client_discrepancies, group_customers
from api.gaps import gaps_from, plan_gaps, proposal_since, row_keys

SOURCES = ['autotask', 'ncentral', 'sentinelone', 'cove']


def customers(**by_source):
    return {s: [{'external_id': i, 'customer_name': n} for i, n in rows] for s, rows in by_source.items()}


def client(id=3, name='Acme', links=(), active=True):
    return {'id': id, 'name': name, 'is_active': active, 'aliases': [], 'sources': [{'source': s, 'external_id': i} for s, i in links]}


class Keys(SimpleTestCase):
    def test_keys_are_in_step_with_the_messages_on_the_page(self):
        rows = customers(autotask=[(101, 'Acme')], ncentral=[(10, 'Acme')])
        row = client_discrepancies([client(links=[('autotask', '101'), ('ncentral', '99')])], rows, ['autotask', 'ncentral', 'cove'])[0]
        self.assertEqual(len(row_keys(row)), len(row['discrepancies']))
        self.assertEqual(row_keys(row), ['missing:3:cove', 'stale:3:ncentral:99'])
        self.assertIn('Cove', row['discrepancies'][0])
        self.assertIn('N-central 99', row['discrepancies'][1])

    def test_a_client_with_no_gaps_has_no_keys(self):
        row = client_discrepancies([client(links=[('autotask', '101')])], customers(autotask=[(101, 'Acme')]), ['autotask'])[0]
        self.assertEqual(row_keys(row), [])


class Current(SimpleTestCase):
    def proposals(self, rows, **kwargs):
        return group_customers(rows, set(), {}, {}, kwargs.get('available', SOURCES))

    def test_unlinked_customers_become_gaps_without_a_client(self):
        found = gaps_from(self.proposals(customers(autotask=[(101, 'IP Capital: BITCO')], ncentral=[(7, 'IP Capital (BitCo)')])), [])
        self.assertEqual({g['key'] for g in found}, {'unlinked:autotask:101', 'unlinked:ncentral:7'})
        self.assertTrue(all(g['kind'] == 'unlinked' and g['client_id'] is None for g in found))
        self.assertEqual({g['external_id'] for g in found}, {'101', '7'})

    def test_a_customer_with_no_name_is_labelled_by_its_source_and_id(self):
        found = gaps_from(self.proposals(customers(cove=[(4, None)])), [])
        self.assertEqual(found[0]['label'], 'Cove 4')

    def test_missing_and_stale_gaps_belong_to_the_client(self):
        rows = customers(autotask=[(101, 'Acme')])
        row = client_discrepancies([client(links=[('autotask', '101'), ('ncentral', '99')])], rows, ['autotask', 'ncentral'])[0]
        found = {g['key']: g for g in gaps_from([], [row])}
        self.assertEqual(set(found), {'stale:3:ncentral:99'})                       # ncentral is readable but the customer is gone; no other source to be missing from
        self.assertEqual((found['stale:3:ncentral:99']['kind'], found['stale:3:ncentral:99']['client_id'], found['stale:3:ncentral:99']['label']), ('stale', 3, 'Acme'))

    def test_an_inactive_client_is_not_missing_anywhere(self):
        row = client_discrepancies([client(links=[], active=False)], customers(), ['autotask', 'cove'])[0]
        self.assertEqual(gaps_from([], [row]), [])

    def test_the_same_gap_is_listed_once(self):
        proposals = self.proposals(customers(autotask=[(1, 'Acme'), (1, 'Acme')]))
        self.assertEqual(len(gaps_from(proposals + proposals, [])), 1)


class Plan(SimpleTestCase):
    def gap(self, key, source='cove'):
        return {'key': key, 'source': source}

    def test_a_new_gap_is_created_and_a_known_one_is_touched(self):
        current = [self.gap('unlinked:cove:1'), self.gap('unlinked:cove:2')]
        plan = plan_gaps({'unlinked:cove:1': {'source': 'cove'}}, current, {'cove'})
        self.assertEqual(([g['key'] for g in plan['create']], plan['touch'], plan['resolve']), (['unlinked:cove:2'], ['unlinked:cove:1'], []))

    def test_a_gap_that_has_gone_is_resolved(self):
        plan = plan_gaps({'unlinked:cove:1': {'source': 'cove'}}, [], {'cove'})
        self.assertEqual(plan['resolve'], ['unlinked:cove:1'])

    def test_a_gap_in_a_source_that_could_not_be_read_stays_open(self):
        open_gaps = {'unlinked:cove:1': {'source': 'cove'}, 'unlinked:autotask:5': {'source': 'autotask'}}
        plan = plan_gaps(open_gaps, [], {'autotask'})                                # cove could not be read this time
        self.assertEqual(plan['resolve'], ['unlinked:autotask:5'])

    def test_nothing_known_and_nothing_found_does_nothing(self):
        self.assertEqual(plan_gaps({}, [], {'cove'}), {'create': [], 'touch': [], 'resolve': []})

    def test_a_gap_still_present_is_never_resolved_even_if_its_source_is_readable(self):
        plan = plan_gaps({'k': {'source': 'cove'}}, [self.gap('k')], {'cove'})
        self.assertEqual((plan['create'], plan['touch'], plan['resolve']), ([], ['k'], []))


class Since(SimpleTestCase):
    def test_a_group_has_been_unlinked_since_its_earliest_customer(self):
        proposal = {'members': [{'source': 'autotask', 'external_id': '1'}, {'source': 'ncentral', 'external_id': '2'}]}
        since = {'unlinked:autotask:1': '2026-11-05T08:00:00Z', 'unlinked:ncentral:2': '2026-11-01T08:00:00Z'}
        self.assertEqual(proposal_since(proposal, since), '2026-11-01T08:00:00Z')

    def test_a_group_with_nothing_on_record_has_no_date(self):
        self.assertIsNone(proposal_since({'members': [{'source': 'cove', 'external_id': '9'}]}, {}))
