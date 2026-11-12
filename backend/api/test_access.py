"""Client-scoped access: what someone limited to some clients can and cannot read. Run against the fake reporting database, so every
section is checked without a real one. (The database side, the endpoints and the command are in api.tests.ClientAccessApi.)"""
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from api import access
from api.access import AccessDenied
from api.clients import Resolver
from api.data import get_section
from api.test_support import FakeReports, ticket

PERIOD = (date(2026, 10, 1), date(2026, 10, 31))
EVERYONE, NOBODY = None, frozenset()


def names(payload, key):
    return [r[key] for r in payload]


class Unrestricted(SimpleTestCase):
    def test_someone_with_no_limit_sees_everything_as_before(self):
        with FakeReports():
            self.assertEqual(len(get_section('server-health', allowed=EVERYONE)['servers']), 3)
            self.assertEqual(len(get_section('backups', allowed=EVERYONE)['rows']), 3)
            self.assertEqual(len(get_section('patches', allowed=EVERYONE)['clients']), 2)

    def test_a_signed_out_or_unknown_user_with_no_limit_given_is_not_limited(self):
        with FakeReports():
            self.assertEqual(len(get_section('server-health', user=None)['servers']), 3)


class Rows(SimpleTestCase):
    def test_servers_only_of_the_allowed_clients(self):
        with FakeReports():
            self.assertEqual(names(get_section('server-health', allowed=frozenset({1}))['servers'], 'computer_name'), ['SRV-A', 'SRV-B'])
            self.assertEqual(names(get_section('server-health', allowed=frozenset({2}))['servers'], 'computer_name'), ['PC-1'])
            self.assertEqual(len(get_section('server-health', allowed=frozenset({1, 2}))['servers']), 3)
            self.assertEqual(get_section('server-health', allowed=NOBODY)['servers'], [])

    def test_backups_leave_out_other_clients_and_anything_not_linked_to_a_client(self):
        with FakeReports():
            self.assertEqual(names(get_section('backups', allowed=frozenset({1}))['rows'], 'device_name'), ['SRV-FILE'])
            both = names(get_section('backups', allowed=frozenset({1, 2}))['rows'], 'device_name')
            self.assertEqual(sorted(both), ['PC-RECEPTION', 'SRV-FILE'])             # the unmapped Cove customer is never shown to a limited person
            self.assertEqual(get_section('backups', allowed=NOBODY)['rows'], [])

    def test_tickets_only_of_the_allowed_clients(self):
        resolver = Resolver({('autotask', '101'): 1, ('autotask', '202'): 2}, {})
        rows = [ticket(ticket_id=1, ticket_number='A1', company_id=101, company_name='Acme'), ticket(ticket_id=2, ticket_number='Z1', company_id=202, company_name='Zed'),
                ticket(ticket_id=3, ticket_number='U1', company_id=303, company_name='Nobody Linked')]
        with FakeReports(tickets=rows, resolver=resolver):
            def numbers(allowed):
                return names(get_section('tickets', *PERIOD, allowed=allowed)['tickets']['rows'], 'ticket_number')
            self.assertEqual(numbers(frozenset({1})), ['A1'])
            self.assertEqual(numbers(frozenset({2})), ['Z1'])
            self.assertEqual(sorted(numbers(frozenset({1, 2}))), ['A1', 'Z1'])           # a ticket of a company with no client is not shown
            self.assertEqual(numbers(NOBODY), [])
            self.assertEqual(sorted(numbers(EVERYONE)), ['A1', 'U1', 'Z1'])

    def test_the_other_parts_of_the_answer_are_kept(self):
        with FakeReports():
            body = get_section('backups', allowed=frozenset({1}))
            self.assertTrue(body['available'] and body['loaded'])
            self.assertTrue(body['columns'])


class BackupDetail(SimpleTestCase):
    def detail(self, device, allowed):
        with FakeReports():
            return get_section('backup-detail', params={'device': str(device)}, allowed=allowed)

    def test_a_device_of_an_allowed_client_opens(self):
        self.assertEqual(self.detail(1, frozenset({1}))['device']['device_name'], 'SRV-FILE')

    def test_a_device_of_another_client_is_the_same_as_one_that_does_not_exist(self):
        self.assertIsNone(self.detail(1, frozenset({2})))
        self.assertIsNone(self.detail(2, frozenset({1})))

    def test_nobody_and_unlinked_devices_cannot_open_any(self):
        self.assertIsNone(self.detail(1, NOBODY))
        self.assertIsNone(self.detail(3, frozenset({1, 2})))                              # device 3 belongs to no client

    def test_everyone_still_opens_every_device_and_it_says_which_client(self):
        self.assertEqual(self.detail(2, EVERYONE)['device']['client_id'], 2)
        self.assertIsNone(self.detail(3, EVERYONE)['device']['client_id'])


class Patches(SimpleTestCase):
    def patches(self, allowed, name='patches', params=None):
        with FakeReports() as fake:
            self.fake = fake
            return get_section(name, params=params or {}, allowed=allowed)

    def scoped_sql(self):
        return [sql for sql, _ in self.fake.calls if 'GROUP BY 1, 2, 3' in sql or 'FROM named' in sql]

    def test_the_scores_are_limited_in_the_query_to_the_clients_customers(self):
        self.patches(frozenset({1}))
        self.assertTrue(self.scoped_sql())
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql in self.scoped_sql()))

    def test_without_a_limit_there_is_no_customer_clause(self):
        self.patches(EVERYONE)
        self.assertFalse(any('dev.org_unit_id IN' in sql for sql in self.scoped_sql()))

    def test_a_person_with_no_clients_reads_nothing(self):
        body = self.patches(NOBODY)
        self.assertTrue(all(' AND FALSE' in sql for sql in self.scoped_sql()))
        self.assertFalse(body['mapped'])
        self.assertEqual(body['clients'], [])

    def test_a_client_with_no_customer_linked_reads_nothing_either(self):
        self.patches(frozenset({2}))                                                      # client 2 has no N-central customer
        self.assertTrue(all(' AND FALSE' in sql for sql in self.scoped_sql()))

    def test_the_client_choices_leave_out_other_clients_and_unlinked_customers(self):
        self.assertEqual([o['key'] for o in self.patches(frozenset({1}))['clients']], ['client:1'])
        self.assertEqual([o['key'] for o in self.patches(EVERYONE)['clients']], ['client:1', 'org:30'])

    def test_naming_a_client_outside_the_limit_is_refused(self):
        with self.assertRaises(AccessDenied):
            self.patches(frozenset({2}), params={'client': '1'})
        self.assertEqual(self.patches(frozenset({1}), params={'client': '1'})['client'], '1')

    def test_naming_a_customer_is_allowed_only_if_it_belongs_to_an_allowed_client(self):
        self.assertEqual(self.patches(frozenset({1}), params={'org': '10'})['org'], '10')
        for allowed, org in ((frozenset({1}), '30'), (frozenset({2}), '10'), (NOBODY, '10')):    # 30 is not linked to any client
            with self.assertRaises(AccessDenied, msg=f'{allowed} {org}'):
                self.patches(allowed, params={'org': org})

    def test_entries_are_limited_and_refused_the_same_way(self):
        self.patches(frozenset({1}), 'patch-entries', {'type': 'windows-security', 'category': 'g1'})
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql in self.scoped_sql() if 'FROM named' in sql))
        with self.assertRaises(AccessDenied):
            self.patches(frozenset({2}), 'patch-entries', {'type': 'windows-security', 'category': 'g1', 'client': '1'})

    def test_the_overview_is_limited_to_the_clients_and_never_reads_the_others(self):
        body = self.patches(frozenset({1}), 'patch-overview')
        self.assertEqual([c['key'] for c in body['clients']], ['client:1'])
        self.assertTrue(all('dev.org_unit_id IN (10, 20)' in sql for sql, _ in self.fake.calls if 'GROUP BY 1, 2, 3, 4' in sql))

    def test_a_client_given_as_a_number_or_text_is_treated_the_same(self):
        for value in ('1', 1):
            self.assertEqual(self.patches(frozenset({1}), params={'client': value})['client'], value)


class SeveralClients(SimpleTestCase):
    def patches(self, allowed, params):
        with FakeReports():
            return get_section('patches', params=params, allowed=allowed)

    def test_every_client_named_must_be_allowed(self):
        self.assertEqual(self.patches(frozenset({1, 2}), {'client': '1,2'})['client'], '1,2')
        for named in ('1,3', '3,1', '2,3,1'):
            with self.assertRaises(AccessDenied, msg=named):
                self.patches(frozenset({1, 2}), {'client': named})

    def test_every_customer_named_must_belong_to_an_allowed_client(self):
        self.assertEqual(self.patches(frozenset({1}), {'org': '10,20'})['org'], '10,20')
        for named in ('10,30', '30,10', '30'):                                          # 30 is not linked to any client
            with self.assertRaises(AccessDenied, msg=named):
                self.patches(frozenset({1}), {'org': named})

    def test_a_client_and_a_customer_together_are_both_checked(self):
        with self.assertRaises(AccessDenied):
            self.patches(frozenset({1}), {'client': '1', 'org': '30'})

    def test_an_unreadable_list_is_refused_not_ignored(self):
        for bad in ('1,x', '1;2', '1,', "1' OR '1"):
            with self.assertRaises(AccessDenied, msg=bad):
                self.patches(frozenset({1}), {'client': bad})

    def test_what_is_read_is_the_chosen_clients_inside_the_allowed_ones(self):
        with FakeReports() as fake:
            get_section('patches', params={'client': '1'}, allowed=frozenset({1, 2}))
        sql = [s for s, _ in fake.calls if 'GROUP BY 1, 2, 3' in s]
        self.assertTrue(sql and all('dev.org_unit_id IN (10, 20)' in s for s in sql))

    def test_someone_limited_to_a_client_cannot_widen_it_by_adding_one(self):
        with self.assertRaises(AccessDenied):
            self.patches(frozenset({1}), {'client': '1,2'})


class Trend(SimpleTestCase):
    def test_someone_with_no_clients_gets_nothing(self):
        with FakeReports():
            body = get_section('patch-trend', params={}, allowed=NOBODY)
        self.assertEqual((body['x'], body['y']), ([], []))
        self.assertIn('No clients', body['heading'])

    def test_naming_a_client_outside_the_limit_is_refused(self):
        with FakeReports(), self.assertRaises(AccessDenied):
            get_section('patch-trend', params={'client': '1'}, allowed=frozenset({2}))


class Decision(SimpleTestCase):
    """The limits that need no database."""

    def user(self, **kw):
        return SimpleNamespace(**{'is_staff': False, 'is_superuser': False, **kw})

    def test_nobody_is_limited_while_access_is_not_enforced(self):
        with override_settings(CLIENT_ACCESS_ENFORCED=False):
            for user in (self.user(), self.user(is_staff=True)):
                self.assertIsNone(access.allowed_client_ids(user))
            self.assertFalse(access.is_limited(self.user()))

    def test_the_platform_owner_is_never_limited_even_when_access_is_enforced(self):
        with override_settings(CLIENT_ACCESS_ENFORCED=True):
            owner = SimpleNamespace(is_staff=True, is_superuser=True)
            self.assertIsNone(access.allowed_client_ids(owner))
            self.assertTrue(access.is_owner(owner))
            self.assertFalse(access.is_owner(self.user(is_staff=True)))     # a company administrator is not the owner

    def test_no_user_means_no_limit(self):
        with override_settings(CLIENT_ACCESS_ENFORCED=True):
            self.assertIsNone(access.allowed_client_ids(None))

    def test_enforcement_is_off_unless_the_setting_is_true(self):
        for value, expected in ((True, True), (False, False), (None, False)):
            with override_settings(CLIENT_ACCESS_ENFORCED=value):
                self.assertEqual(access.enforced(), expected)

    def test_a_limited_member_is_the_one_who_is_neither_an_administrator_nor_the_owner(self):
        with override_settings(CLIENT_ACCESS_ENFORCED=True):
            self.assertTrue(access.is_scoped_member(self.user()))
            self.assertFalse(access.is_scoped_member(self.user(is_staff=True)))
            self.assertFalse(access.is_scoped_member(SimpleNamespace(is_staff=True, is_superuser=True)))
        with override_settings(CLIENT_ACCESS_ENFORCED=False):
            self.assertFalse(access.is_scoped_member(self.user()))


class TheCompanyWall(SimpleTestCase):
    """scope_for: who sees which clients. Company A has clients 1 and 2, company B has 5 and 6, client 9 is unclaimed."""

    def scope(self, **kw):
        base = dict(owner=False, staff=False, has_company=True, company_clients={1, 2}, unclaimed={9}, granted=set())
        return access.scope_for(**{**base, **kw})

    def test_the_owner_sees_everything(self):
        self.assertIsNone(self.scope(owner=True))

    def test_an_administrator_sees_their_company_and_what_nobody_has_claimed_yet_and_never_another_company(self):
        admin = self.scope(staff=True)
        self.assertEqual(set(admin), {1, 2, 9})
        self.assertTrue(admin.unlinked)                                   # and data linked to no client, so there is something to link
        for other_company in (5, 6):
            self.assertNotIn(other_company, admin)

    def test_an_administrator_cannot_widen_their_own_view_by_naming_another_companys_client(self):
        with self.assertRaises(AccessDenied):
            access.check_request({'client': '5'}, self.scope(staff=True))

    def test_a_member_sees_only_the_clients_they_were_given_inside_their_company(self):
        member = self.scope(granted={1, 5, 9})
        self.assertEqual(set(member), {1})                                # 5 is another company's, 9 is unclaimed: neither applies to a member
        self.assertFalse(member.unlinked)

    def test_a_member_given_nothing_sees_nothing(self):
        self.assertEqual(set(self.scope()), set())

    def test_someone_in_no_company_sees_nothing_whatever_they_are(self):
        for staff in (False, True):
            scope = self.scope(has_company=False, staff=staff, granted={1})
            self.assertEqual((set(scope), scope.unlinked), (set(), False))

    def test_a_scope_is_a_set_of_client_ids_that_also_remembers_what_it_may_do_with_unlinked_data(self):
        scope = access.Scope({1, 2}, unlinked=True)
        self.assertIn(1, scope)
        self.assertEqual(len(scope), 2)
        self.assertTrue(scope.unlinked)
        self.assertFalse(access.Scope({1}).unlinked)
        self.assertFalse(access.Scope().unlinked)
        self.assertIsInstance(scope, frozenset)


class UnlinkedData(SimpleTestCase):
    """Data linked to no client yet: only an administrator may see it (so they can link it). It belongs to nobody, so it cannot be a company's."""
    ADMIN = access.Scope({1, 2}, unlinked=True)
    MEMBER = access.Scope({1})

    def test_rows_with_no_client_are_for_administrators_only(self):
        rows = [{'client_id': 1}, {'client_id': 5}, {'client_id': None}]
        self.assertEqual(access.only_allowed(rows, self.ADMIN), [{'client_id': 1}, {'client_id': None}])
        self.assertEqual(access.only_allowed(rows, self.MEMBER), [{'client_id': 1}])
        self.assertEqual(access.only_allowed(rows, None), rows)
        self.assertEqual(access.only_allowed(rows, frozenset({1})), [{'client_id': 1}])        # a plain set has no unlinked rights

    def test_row_and_client_rules(self):
        self.assertTrue(access.row_allowed(None, None))
        self.assertTrue(access.row_allowed(None, self.ADMIN))
        self.assertFalse(access.row_allowed(None, self.MEMBER))
        self.assertFalse(access.row_allowed(5, self.ADMIN))
        self.assertTrue(access.client_visible(1, self.MEMBER))
        self.assertFalse(access.client_visible(2, self.MEMBER))
        self.assertTrue(access.client_visible(99, None))

    def test_customers_not_linked_to_any_client_are_an_administrators_to_see(self):
        with FakeReports():                                               # customers 10 and 20 belong to client 1, customer 30 to nobody
            self.assertEqual(access.org_units_of(self.ADMIN), [10, 20, 30])
            self.assertEqual(access.org_units_of(self.MEMBER), [10, 20])
            access.check_request({'org': '30'}, self.ADMIN)
            with self.assertRaises(AccessDenied):
                access.check_request({'org': '30'}, self.MEMBER)

    def test_an_administrators_patch_choices_include_unlinked_customers_and_a_members_do_not(self):
        with FakeReports():
            admin = [o['key'] for o in get_section('patches', allowed=self.ADMIN)['clients']]
            member = [o['key'] for o in get_section('patches', allowed=self.MEMBER)['clients']]
        self.assertEqual((admin, member), (['client:1', 'org:30'], ['client:1']))

    def test_an_administrator_reads_the_unlinked_customers_data_too(self):
        with FakeReports() as fake:
            get_section('patches', allowed=self.ADMIN)
        self.assertTrue(all('dev.org_unit_id IN (10, 20, 30)' in sql for sql, _ in fake.calls if 'GROUP BY 1, 2, 3' in sql))

    def test_backups_and_devices_with_no_client(self):
        with FakeReports():
            admin = names(get_section('backups', allowed=self.ADMIN)['rows'], 'device_name')
            member = names(get_section('backups', allowed=self.MEMBER)['rows'], 'device_name')
            detail = get_section('backup-detail', params={'device': '3'}, allowed=self.ADMIN)
            hidden = get_section('backup-detail', params={'device': '3'}, allowed=self.MEMBER)
        self.assertIn('m365-mailboxes', admin)                            # the Cove customer with no client
        self.assertNotIn('m365-mailboxes', member)
        self.assertIsNotNone(detail)
        self.assertIsNone(hidden)

    def test_the_overview_for_an_administrator_includes_unlinked_customers(self):
        with FakeReports():
            keys = [c['key'] for c in get_section('patch-overview', allowed=self.ADMIN)['clients']]
        self.assertEqual(keys, ['client:1', 'org:30'])
