"""Client groups and new people: the rules in api/groups.py. No database: these are pure."""
from django.test import SimpleTestCase

from api import groups

CLIENTS = {1, 2, 3}                                  # the company's clients; client 99 belongs to another company


def group(body=None, taken=(), clients=CLIENTS):
    return groups.clean_group({'name': 'Retail', **(body or {})}, taken_names=taken, company_client_ids=clients)


def person(body=None, taken=(), valid=(10, 11), admin=False):
    base = {'username': 'thandi', 'email': 'thandi@acme.test'}
    return groups.clean_person({**base, **(body or {})}, taken_usernames=taken, valid_group_ids=valid, can_set_admin=admin)


def update(body, valid=(10, 11), admin=False, is_self=False, owner=False):
    return groups.clean_person_update(body, valid_group_ids=valid, can_set_admin=admin, is_self=is_self, target_is_owner=owner)


class ClientGroups(SimpleTestCase):
    def test_a_group_with_a_name_and_clients(self):
        self.assertEqual(group({'clients': [3, 1]}), ({'name': 'Retail', 'all_clients': False, 'clients': [1, 3]}, None))

    def test_the_name_is_tidied_and_needed_and_limited(self):
        self.assertEqual(group({'name': '  Big   retail  '})[0]['name'], 'Big retail')
        for bad in (None, '', '   ', 5, ['x']):
            self.assertEqual(group({'name': bad}), (None, 'Give the group a name'), bad)
        self.assertEqual(group({'name': 'x' * 81})[1], 'A name can have at most 80 characters')
        self.assertIsNone(group({'name': 'x' * 80})[1])

    def test_a_name_already_in_use_is_refused_ignoring_case_and_spaces(self):
        self.assertEqual(group(taken=['RETAIL'])[1], 'There is already a group with that name')
        self.assertEqual(group({'name': 'big  retail'}, taken=['Big retail'])[1], 'There is already a group with that name')
        self.assertIsNone(group(taken=['Wholesale'])[1])

    def test_all_clients_holds_no_list_of_its_own(self):
        self.assertEqual(group({'all_clients': True, 'clients': [1, 2]}), ({'name': 'Retail', 'all_clients': True, 'clients': []}, None))
        self.assertEqual(group({'all_clients': 'yes'})[1], 'all_clients must be true or false')

    def test_a_client_that_is_not_the_companys_is_refused_and_not_named_as_anyone_elses(self):
        self.assertEqual(group({'clients': [1, 99]})[1], 'Unknown client ID: 99')
        self.assertEqual(group({'clients': [4]}, clients=set())[1], 'Unknown client ID: 4')

    def test_the_list_of_clients_must_be_a_short_list_of_whole_numbers_without_repeats(self):
        self.assertEqual(group({'clients': [2, 2, 1]})[0]['clients'], [1, 2])
        for bad in ('1', [True], [1.5], [None], {'a': 1}):
            self.assertEqual(group({'clients': bad})[1], 'Send clients as a list of IDs (at most 500)', bad)
        self.assertEqual(group({'clients': list(range(501))}, clients=set(range(501)))[1], 'Send clients as a list of IDs (at most 500)')
        self.assertIsNone(group({'clients': list(range(500))}, clients=set(range(500)))[1])

    def test_an_empty_group_is_allowed_and_a_body_that_is_not_an_object_is_not(self):
        self.assertEqual(group({'clients': []})[0]['clients'], [])
        self.assertEqual(groups.clean_group([], taken_names=(), company_client_ids=CLIENTS), (None, 'Send the group as an object'))


class WhatGroupsGive(SimpleTestCase):
    def test_all_clients_gives_every_client_of_the_company_including_later_ones(self):
        self.assertEqual(groups.group_clients([{'all_clients': True, 'clients': []}], {1, 2, 3}), {1, 2, 3})
        self.assertEqual(groups.group_clients([{'all_clients': True, 'clients': []}], {1, 2, 3, 4}), {1, 2, 3, 4})

    def test_a_list_gives_those_clients_and_never_one_outside_the_company(self):
        self.assertEqual(groups.group_clients([{'all_clients': False, 'clients': [1, 99]}], {1, 2, 3}), {1})

    def test_several_groups_are_added_together(self):
        both = [{'all_clients': False, 'clients': [1]}, {'all_clients': False, 'clients': [3]}]
        self.assertEqual(groups.group_clients(both, {1, 2, 3}), {1, 3})
        self.assertEqual(groups.group_clients(both + [{'all_clients': True, 'clients': []}], {1, 2, 3}), {1, 2, 3})

    def test_no_groups_give_nothing(self):
        self.assertEqual(groups.group_clients([], {1, 2}), set())
        self.assertEqual(groups.group_clients([{'all_clients': False, 'clients': []}], {1, 2}), set())


class NewPerson(SimpleTestCase):
    def test_a_person_with_the_minimum(self):
        fields, error = person()
        self.assertIsNone(error)
        self.assertEqual(fields, {'username': 'thandi', 'email': 'thandi@acme.test', 'first_name': '', 'last_name': '', 'client_groups': [], 'is_staff': False})

    def test_names_groups_and_text_are_tidied(self):
        fields, _ = person({'username': ' thandi ', 'first_name': ' Thandi ', 'last_name': 'Nkosi', 'client_groups': [11, 10, 10]})
        self.assertEqual((fields['username'], fields['first_name'], fields['last_name'], fields['client_groups']), ('thandi', 'Thandi', 'Nkosi', [10, 11]))

    def test_the_username_is_required_valid_and_free(self):
        self.assertEqual(person({'username': ''})[1], 'Username is required')
        self.assertEqual(person({'username': None})[1], 'Username is required')
        self.assertEqual(person({'username': 'has space'})[1], 'A username can only have letters, numbers and . @ + - _ (up to 150)')
        self.assertEqual(person({'username': 'x' * 151})[1], 'A username can only have letters, numbers and . @ + - _ (up to 150)')
        self.assertIsNone(person({'username': 'thandi.n@acme-1'})[1])
        self.assertEqual(person(taken=['THANDI'])[1], 'That username is taken')

    def test_the_email_is_required_because_the_link_is_sent_there(self):
        self.assertEqual(person({'email': ''})[1], 'Email is required: the link to choose a password is sent there')
        self.assertEqual(person({'email': '   '})[1], 'Email is required: the link to choose a password is sent there')
        for bad in ('thandi', 'a@b', '@acme.test', 'a b@acme.test'):
            self.assertEqual(person({'email': bad})[1], 'That does not look like an email address', bad)
        self.assertEqual(person({'email': 'a' * 250 + '@b.co'})[1], 'Email must be text of at most 254 characters')
        self.assertEqual(person({'email': 5})[1], 'Email must be text of at most 254 characters')

    def test_a_group_that_is_not_the_companys_is_refused(self):
        self.assertEqual(person({'client_groups': [10, 77]})[1], 'Unknown client group: 77')
        self.assertEqual(person({'client_groups': 'all'})[1], 'Send client_groups as a list of IDs (at most 50)')
        self.assertEqual(person({'client_groups': [True]})[1], 'Send client_groups as a list of IDs (at most 50)')

    def test_only_the_platform_owner_can_make_an_administrator(self):
        self.assertEqual(person({'is_staff': True})[1], 'Only the platform owner can make an administrator')
        self.assertTrue(person({'is_staff': True}, admin=True)[0]['is_staff'])
        self.assertEqual(person({'is_staff': 'yes'}, admin=True)[1], 'is_staff must be true or false')

    def test_a_body_that_is_not_an_object_is_refused(self):
        self.assertEqual(groups.clean_person('x', taken_usernames=(), valid_group_ids=(), can_set_admin=False), (None, 'Send the person as an object'))

    def test_no_password_is_ever_part_of_it(self):
        fields, _ = person({'password': 'chosen-for-them'})
        self.assertNotIn('password', fields)                                    # the person chooses their own, from the link


class ChangingAPerson(SimpleTestCase):
    def test_only_what_was_sent_is_returned(self):
        self.assertEqual(update({'first_name': ' Thandi '}), ({'first_name': 'Thandi'}, None))
        self.assertEqual(update({'client_groups': [11, 10]}), ({'client_groups': [10, 11]}, None))

    def test_nothing_sent_or_nothing_recognised_is_refused(self):
        self.assertEqual(update({}), (None, 'Send what to change'))
        self.assertEqual(update({'colour': 'blue'}), (None, 'Nothing to change'))
        self.assertEqual(groups.clean_person_update([], valid_group_ids=(), can_set_admin=False, is_self=False, target_is_owner=False), (None, 'Send what to change'))

    def test_the_email_stays_valid_and_cannot_be_emptied(self):
        self.assertEqual(update({'email': 'new@acme.test'})[0], {'email': 'new@acme.test'})
        self.assertEqual(update({'email': ''})[1], 'Email cannot be empty: the link to choose a password is sent there')
        self.assertEqual(update({'email': 'nope'})[1], 'That does not look like an email address')

    def test_a_person_can_be_deactivated_and_brought_back_but_nobody_can_deactivate_themselves(self):
        self.assertEqual(update({'is_active': False})[0], {'is_active': False})
        self.assertEqual(update({'is_active': True}, is_self=True)[0], {'is_active': True})
        self.assertEqual(update({'is_active': False}, is_self=True)[1], 'You cannot deactivate yourself')
        self.assertEqual(update({'is_active': 0})[1], 'is_active must be true or false')

    def test_the_administrator_rank_is_the_platform_owners_to_change_and_never_your_own_to_remove(self):
        self.assertEqual(update({'is_staff': True})[1], 'Only the platform owner can change who is an administrator')
        self.assertEqual(update({'is_staff': True}, admin=True)[0], {'is_staff': True})
        self.assertEqual(update({'is_staff': False}, admin=True, is_self=True)[1], 'You cannot remove your own administrator rank')
        self.assertEqual(update({'is_staff': 'no'}, admin=True)[1], 'is_staff must be true or false')

    def test_nobody_but_the_owner_may_touch_the_owner(self):
        self.assertEqual(update({'first_name': 'X'}, owner=True)[1], 'Only the platform owner can change the platform owner')
        self.assertEqual(update({'first_name': 'X'}, owner=True, admin=True)[0], {'first_name': 'X'})

    def test_groups_must_be_the_companys(self):
        self.assertEqual(update({'client_groups': [10, 77]})[1], 'Unknown client group: 77')
        self.assertEqual(update({'client_groups': []})[0], {'client_groups': []})                    # taking someone out of every group is allowed


class LinksAndShapes(SimpleTestCase):
    def test_the_link_goes_to_the_page_forgot_password_uses(self):
        self.assertEqual(groups.setup_link('https://reports.example/', 'MQ', 'abc-123'), 'https://reports.example/reset?uid=MQ&token=abc-123')
        self.assertEqual(groups.setup_link('http://localhost:5173', 'MQ', 't'), 'http://localhost:5173/reset?uid=MQ&token=t')

    def test_the_email_says_how_long_the_link_works_and_what_to_do_when_it_has_not(self):
        subject, body = groups.setup_email('Thandi', 'https://x/reset?uid=1&token=t', 60)
        self.assertEqual(subject, 'Your Report Manager account')
        self.assertIn('Hello Thandi,', body)
        self.assertIn('https://x/reset?uid=1&token=t', body)
        self.assertIn('60 minutes', body)
        self.assertIn('Forgot password', body)
        self.assertTrue(groups.setup_email('', 'l', 60)[1].startswith('Hello,'))

    def test_it_says_honestly_whether_an_email_was_really_sent(self):
        self.assertEqual(groups.how_it_was_sent('django.core.mail.backends.console.EmailBackend'), 'console')
        self.assertEqual(groups.how_it_was_sent('django.core.mail.backends.smtp.EmailBackend'), 'sent')
        self.assertEqual(groups.how_it_was_sent('django.core.mail.backends.smtp.EmailBackend', failed=True), 'failed')

    def test_a_group_as_the_api_gives_it(self):
        payload = groups.group_payload(5, 'Retail', 0, [3, 1], ['zed', 'Amy'], {'id': 1, 'name': 'Alpha'})
        self.assertEqual(payload, {'id': 5, 'name': 'Retail', 'all_clients': False, 'clients': [1, 3], 'members': ['Amy', 'zed'], 'company': {'id': 1, 'name': 'Alpha'}})

    def test_a_person_as_the_access_overview_gives_them(self):
        payload = groups.person_payload('thandi', '', None, is_staff=0, is_owner=False, is_active=True, company=None, clients=[2, 1], client_groups=[11, 10])
        self.assertEqual(payload, {'username': 'thandi', 'name': 'thandi', 'email': '', 'is_staff': False, 'is_owner': False, 'is_active': True, 'company': None,
                                   'clients': [1, 2], 'client_groups': [10, 11]})
