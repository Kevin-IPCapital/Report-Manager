import re
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from config.env import database_from_url

from . import access as access_lib, auth
from .models import RefreshToken
from . import snapshots
from .models import Client, ClientAccess, ClientAlias, ClientGap, ClientGroup, ClientSourceMap, Company, CompanyMember, PatchSnapshot, ScoreWeight
from .clients import Resolver
from .test_support import CacheClearMixin, FakeReports, ReportsFakeMixin, at

H = {'HTTP_X_REQUESTED_WITH': 'fetch'}
SECTION = '/api/sections/tickets?from=2026-10-03&to=2026-11-03'


@override_settings(DEV_LOGIN_BYPASS=False)
class AuthFlow(CacheClearMixin, ReportsFakeMixin, TestCase):
    def setUp(self):
        super().setUp()
        get_user_model().objects.create_user('demo', password='demo-pass')

    def login(self, password='demo-pass'):
        return self.client.post('/api/auth/login', {'username': 'demo', 'password': password},
                                content_type='application/json', **H)

    def cookie(self):
        return self.client.cookies['refresh_token'].value

    def refresh(self, token=None):
        if token:
            self.client.cookies['refresh_token'] = token
        return self.client.post('/api/auth/refresh', **H)

    def test_protected_endpoint_needs_access_token(self):
        access = self.login().json()['access']
        self.assertEqual(self.client.get(SECTION).status_code, 401)
        self.assertEqual(self.client.get(SECTION, HTTP_AUTHORIZATION=f'Bearer {access}').status_code, 200)

    def test_bad_credentials_and_missing_header(self):
        self.assertEqual(self.login('wrong').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', {}, content_type='application/json').status_code, 403)

    def test_date_validation(self):
        access = self.login().json()['access']
        bad = self.client.get('/api/sections/tickets?from=2026-11-03&to=2026-10-03', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(bad.status_code, 400)

    def test_rotation_and_leeway_replay(self):
        self.login()
        t1 = self.cookie()
        self.assertEqual(self.refresh().status_code, 200)
        t2 = self.cookie()
        self.assertNotEqual(t1, t2)
        replay = self.refresh(t1)  # lost-response retry inside the leeway window
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.cookie(), t2)  # same successor, no fork

    def test_reuse_after_leeway_revokes_whole_family(self):
        self.login()
        t1 = self.cookie()
        self.refresh()
        t2 = self.cookie()
        RefreshToken.objects.exclude(rotated_at=None).update(rotated_at=timezone.now() - timedelta(seconds=60))
        self.assertEqual(self.refresh(t1).status_code, 401)
        self.assertEqual(self.refresh(t2).status_code, 401)  # legitimate holder is logged out too

    def test_repeated_failures_are_throttled_even_for_the_right_password(self):
        for _ in range(5):
            self.assertEqual(self.login('wrong').status_code, 401)
        self.assertEqual(self.login('wrong').status_code, 429)
        self.assertEqual(self.login().status_code, 429)  # until the window passes

    def test_a_successful_login_resets_the_count(self):
        for _ in range(4):
            self.login('wrong')
        self.assertEqual(self.login().status_code, 200)
        for _ in range(4):
            self.assertEqual(self.login('wrong').status_code, 401)

    def test_throttling_is_per_username(self):
        for _ in range(5):
            self.login('wrong')
        other = self.client.post('/api/auth/login', {'username': 'someone-else', 'password': 'x'}, content_type='application/json', **H)
        self.assertEqual(other.status_code, 401)

    def test_logout_revokes(self):
        self.login()
        t1 = self.cookie()
        self.assertEqual(self.client.post('/api/auth/logout', **H).status_code, 200)
        self.assertEqual(self.refresh(t1).status_code, 401)


@override_settings(DEV_LOGIN_BYPASS=False)
class SectionShapes(ReportsFakeMixin, TestCase):
    """Endpoint wiring, authentication and shapes. The data logic itself is covered in test_data.py."""

    def setUp(self):
        super().setUp()
        get_user_model().objects.create_user('demo', password='demo-pass')
        self.client.post('/api/auth/login', {'username': 'demo', 'password': 'demo-pass'}, content_type='application/json', **H)
        access = self.client.post('/api/auth/refresh', **H).json()['access']
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_patches_and_entries_endpoints(self):
        body = self.client.get('/api/sections/patches', **self.auth).json()
        self.assertEqual([t['key'] for t in body['types']], ['windows-security', 'windows-patching', 'third-party'])
        url = '/api/sections/patch-entries?type=windows-security&category=g1'
        rows = self.client.get(url, **self.auth).json()['entries']['rows']
        self.assertEqual([r['installed'] for r in rows], [False, True, None])
        self.assertEqual(self.client.get('/api/sections/patch-entries?type=nope', **self.auth).status_code, 404)

    def test_patch_trend_shape(self):
        trend = self.client.get('/api/sections/patch-trend', **self.auth).json()
        self.assertEqual(set(trend), {'heading', 'type', 'x', 'y'})
        self.assertEqual(len(trend['x']), len(trend['y']))

    def test_backups_endpoints(self):
        body = self.client.get('/api/sections/backups', **self.auth).json()
        self.assertEqual([r['health'] for r in body['rows']], ['CRITICAL', 'WARNING', 'OK'])
        detail = self.client.get('/api/sections/backup-detail?device=2', **self.auth).json()
        self.assertEqual(detail['device']['device_name'], 'PC-RECEPTION')
        self.assertEqual(self.client.get('/api/sections/backup-detail?device=999', **self.auth).status_code, 404)
        self.assertEqual(self.client.get('/api/sections/backup-detail?device=abc', **self.auth).status_code, 400)

    def test_server_health_and_unknown_section(self):
        servers = self.client.get('/api/sections/server-health', **self.auth).json()['servers']
        self.assertEqual(len(servers), 3)
        wanted = {'computer_name', 'network_status', 'data_age_hours', 'machine_type', 'os_type', 'agent_version',
                  'mitigation_mode', 'is_up_to_date', 'infected', 'is_active'}
        self.assertTrue(all(wanted <= set(s) for s in servers))
        self.assertEqual(self.client.get('/api/sections/nope', **self.auth).status_code, 404)

    def post(self, path, body):
        return self.client.post(path, body, content_type='application/json', **self.auth, **H)

    def test_client_parameter_must_be_an_id_and_scopes_patches(self):
        self.assertEqual(self.client.get('/api/sections/patches?client=abc', **self.auth).status_code, 400)
        body = self.client.get('/api/sections/patches?client=1', **self.auth).json()
        self.assertEqual((body['client'], body['mapped']), ('1', True))
        self.assertEqual(self.client.get('/api/sections/patches?org=x', **self.auth).status_code, 400)
        self.assertEqual(self.client.get('/api/sections/patches?org=30', **self.auth).json()['org'], '30')
        self.assertEqual([o['key'] for o in body['clients']], ['client:1', 'org:30'])

    def test_tickets_endpoint_requires_dates_and_returns_rows(self):
        self.assertEqual(self.client.get('/api/sections/tickets', **self.auth).status_code, 400)
        rows = self.client.get(SECTION, **self.auth).json()['tickets']['rows']
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(1 for r in rows if r['rowStatus'] == 'bad'), 1)
        self.assertFalse(self.client.get(SECTION, **self.auth).json()['technician_available'])


@override_settings(DEV_LOGIN_BYPASS=True)
class DevBypass(TestCase):
    def test_any_credentials_succeed_and_user_is_created(self):
        r = self.client.post('/api/auth/login', {'username': 'a', 'password': 'anything'}, content_type='application/json', **H)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(get_user_model().objects.filter(username='a').exists())
        self.assertIn('refresh_token', r.cookies)


class DatabaseUrls(SimpleTestCase):
    def test_postgres_url(self):
        cfg = database_from_url('postgres://app:p%40ss@db.example.com:5433/reports?sslmode=require', Path('/x'))
        self.assertEqual((cfg['USER'], cfg['PASSWORD'], cfg['HOST'], cfg['PORT'], cfg['NAME']), ('app', 'p@ss', 'db.example.com', 5433, 'reports'))
        self.assertEqual(cfg['OPTIONS'], {'sslmode': 'require'})

    def test_sqlite_url_is_relative_to_base_dir(self):
        self.assertEqual(database_from_url('sqlite:///db.sqlite3', Path('/x'))['NAME'], Path('/x/db.sqlite3'))

    def test_unknown_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            database_from_url('mysql://a:b@h/db', Path('/x'))


@override_settings(DEV_LOGIN_BYPASS=False, REGISTRATION_OPEN=True)
class Accounts(CacheClearMixin, TestCase):
    def post(self, path, body):
        return self.client.post(path, body, content_type='application/json', **H)

    def test_register_signs_in_and_validates(self):
        ok = self.post('/api/auth/register', {'username': 'new', 'email': 'n@example.com', 'password': 'long-enough-1'})
        self.assertEqual(ok.status_code, 200)
        self.assertIn('access', ok.json())
        self.assertEqual(self.post('/api/auth/register', {'username': 'NEW', 'password': 'long-enough-1'}).status_code, 409)
        self.assertEqual(self.post('/api/auth/register', {'username': 'x', 'password': 'short'}).status_code, 400)

    @override_settings(REGISTRATION_OPEN=False)
    def test_registration_can_be_closed(self):
        self.assertEqual(self.post('/api/auth/register', {'username': 'new', 'password': 'long-enough-1'}).status_code, 403)

    def test_password_reset_flow(self):
        get_user_model().objects.create_user('kev', 'kev@example.com', 'old-password-1')
        self.assertEqual(self.post('/api/auth/forgot', {'identifier': 'nobody@example.com'}).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)  # same answer, no email, for unknown accounts
        self.post('/api/auth/forgot', {'identifier': 'kev@example.com'})
        uid, token = re.search(r'uid=([^&\s]+)&token=(\S+)', mail.outbox[0].body).groups()
        self.assertEqual(self.post('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'new-password-1'}).status_code, 200)
        self.assertEqual(self.post('/api/auth/login', {'username': 'kev', 'password': 'new-password-1'}).status_code, 200)
        self.assertEqual(self.post('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'again-password-1'}).status_code, 400)


    def test_common_and_similar_passwords_are_rejected_on_register(self):
        for password in ('password', '12345678'):
            self.assertEqual(self.post('/api/auth/register', {'username': 'new', 'password': password}).status_code, 400, password)
        similar = self.post('/api/auth/register', {'username': 'kevinmynhardt', 'password': 'kevinmynhardt1'})
        self.assertEqual(similar.status_code, 400)

    def test_a_weak_reset_password_is_rejected_and_the_link_stays_usable(self):
        get_user_model().objects.create_user('kev', 'kev@example.com', 'old-password-1')
        self.post('/api/auth/forgot', {'identifier': 'kev@example.com'})
        uid, token = re.search(r'uid=([^&\s]+)&token=(\S+)', mail.outbox[0].body).groups()
        self.assertEqual(self.post('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'password'}).status_code, 400)
        self.assertEqual(self.post('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'new-password-1'}).status_code, 200)

    def test_reset_requests_are_throttled(self):
        for _ in range(5):
            self.assertEqual(self.post('/api/auth/forgot', {'identifier': 'kev@example.com'}).status_code, 200)
        self.assertEqual(self.post('/api/auth/forgot', {'identifier': 'kev@example.com'}).status_code, 429)


@override_settings(DEBUG=False, DEV_COLLECTOR_HEALTH_FOR_ALL=False)
class CollectorEndpointAccess(TestCase):
    """The collector endpoints can read logs and start processes, so a non-staff user must get 403 on all of them."""

    def setUp(self):
        User = get_user_model()
        self.plain = User.objects.create_user('plain', password='x')
        self.staff = User.objects.create_user('boss', password='x', is_staff=True)

    def bearer(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {auth.make_access(user)}'}

    def test_non_staff_is_forbidden_everywhere(self):
        for path in ('/api/collectors/health', '/api/collectors/execution', '/api/collectors/cove_backup/latest-log'):
            self.assertEqual(self.client.get(path, **self.bearer(self.plain)).status_code, 403, path)
        run = self.client.post('/api/collectors/run', {'job': 'all'}, content_type='application/json', **self.bearer(self.plain))
        self.assertEqual(run.status_code, 403)

    def test_anonymous_is_unauthorised(self):
        self.assertEqual(self.client.get('/api/collectors/health').status_code, 401)

    def test_staff_is_admitted(self):
        with patch('api.collection_health.source_capture_times', return_value={}), patch('api.collection_health.current_alerts', return_value=[]):
            response = self.client.get('/api/collectors/health', **self.bearer(self.staff))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['alerts'], [])
        self.assertIn('cove_backup', [j['job'] for j in response.json()['jobs']])


class MakeStaff(TestCase):
    def test_grant_and_remove(self):
        get_user_model().objects.create_user('kev', password='x')
        call_command('makestaff', 'kev')
        self.assertTrue(get_user_model().objects.get(username='kev').is_staff)
        call_command('makestaff', 'kev', '--remove')
        self.assertFalse(get_user_model().objects.get(username='kev').is_staff)

    def test_unknown_user(self):
        with self.assertRaises(CommandError):
            call_command('makestaff', 'ghost')


class StaffClient:
    """Helpers for the endpoint tests below: a staff user and an ordinary user, and JSON calls as either."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.staff = User.objects.create_user('boss', password='x', is_staff=True)
        self.plain = User.objects.create_user('plain', password='x')

    def call(self, method, path, user, body=None):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {auth.make_access(user)}'}
        if method == 'get':
            return self.client.get(path, **headers)
        return getattr(self.client, method)(path, body or {}, content_type='application/json', **headers)


class ClientApi(StaffClient, CacheClearMixin, TestCase):
    def make(self, name='Acme', **extra):
        return self.call('post', '/api/clients', self.staff, {'name': name, **extra})

    def test_create_with_sources_and_aliases(self):
        made = self.make(sources=[{'source': 'autotask', 'external_id': 101}, {'source': 'ncentral', 'external_id': '10'},
                                  {'source': 'autotask', 'external_id': '101'}],
                         aliases=['Acme Logistics', 'ACME  logistics', 'Acme'])
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual(body['sources'], [{'source': 'autotask', 'external_id': '101'}, {'source': 'ncentral', 'external_id': '10'}])
        self.assertEqual(body['aliases'], ['Acme Logistics'])  # the duplicate spelling and the client's own name are dropped

    def test_an_id_or_alias_belongs_to_one_client_and_names_are_unique(self):
        self.make(sources=[{'source': 'autotask', 'external_id': '101'}], aliases=['AL'])
        for body in ({'name': 'Other', 'sources': [{'source': 'autotask', 'external_id': '101'}]},
                     {'name': 'Other', 'aliases': ['al']}, {'name': 'al'}, {'name': 'ACME'}, {'name': ''}):
            self.assertEqual(self.call('post', '/api/clients', self.staff, body).status_code, 400, body)
        same_id_other_source = self.make('Other', sources=[{'source': 'ncentral', 'external_id': '101'}])
        self.assertEqual(same_id_other_source.status_code, 201)  # IDs are only unique within one source

    def test_unknown_source_and_missing_id_are_rejected(self):
        self.assertEqual(self.make(sources=[{'source': 'nope', 'external_id': '1'}]).status_code, 400)
        self.assertEqual(self.make(sources=[{'source': 'cove', 'external_id': ' '}]).status_code, 400)

    def test_edit_replaces_sources_and_aliases(self):
        client_id = self.make(sources=[{'source': 'autotask', 'external_id': '101'}], aliases=['AL']).json()['id']
        edited = self.call('put', f'/api/clients/{client_id}', self.staff,
                           {'name': 'Acme Ltd', 'is_active': False, 'sources': [{'source': 'cove', 'external_id': '4'}], 'aliases': []})
        self.assertEqual((edited.status_code, edited.json()['name'], edited.json()['is_active']), (200, 'Acme Ltd', False))
        self.assertEqual(edited.json()['sources'], [{'source': 'cove', 'external_id': '4'}])
        self.assertEqual((ClientSourceMap.objects.count(), ClientAlias.objects.count()), (1, 0))
        # Re-saving a client with its own unchanged name, ID and alias is not a conflict with itself.
        again = self.call('put', f'/api/clients/{client_id}', self.staff, {'name': 'Acme Ltd', 'sources': [{'source': 'cove', 'external_id': '4'}], 'aliases': ['AL']})
        self.assertEqual(again.status_code, 200)

    def test_a_clients_linked_customers_carry_their_names_and_not_only_their_ids(self):
        self.make('Acme', sources=[{'source': 'autotask', 'external_id': '101'}, {'source': 'ncentral', 'external_id': '7'}, {'source': 'cove', 'external_id': '55'}])
        found = ({'autotask': [{'external_id': 101, 'customer_name': 'IP Capital: BITCO'}], 'ncentral': [{'external_id': 7, 'customer_name': 'IP Capital (BitCo)'}]}, ['cove'])
        with patch('api.clients.source_customers', return_value=found):
            links = self.call('get', '/api/clients', self.staff).json()[0]['sources']
        self.assertEqual({(s['source'], s['external_id']): s['customer_name'] for s in links},
                         {('autotask', '101'): 'IP Capital: BITCO', ('ncentral', '7'): 'IP Capital (BitCo)', ('cove', '55'): None})   # Cove could not be read: no name, never a guess

    def test_the_other_client_endpoints_still_answer_without_customer_names(self):
        made = self.make('Acme', sources=[{'source': 'autotask', 'external_id': '101'}]).json()
        self.assertEqual(made['sources'], [{'source': 'autotask', 'external_id': '101'}])

    def test_only_staff_can_change_clients_but_anyone_signed_in_can_list_them(self):
        client_id = self.make().json()['id']
        self.assertEqual(self.call('post', '/api/clients', self.plain, {'name': 'X'}).status_code, 403)
        self.assertEqual(self.call('put', f'/api/clients/{client_id}', self.plain, {'name': 'X'}).status_code, 403)
        self.assertEqual(len(self.call('get', '/api/clients', self.plain).json()), 1)
        self.assertEqual(self.client.get('/api/clients').status_code, 401)

    FOUND = ({'autotask': [{'external_id': 101, 'customer_name': 'IP Capital: BITCO'}, {'external_id': 102, 'customer_name': 'Blue Crane'}],
              'ncentral': [{'external_id': 7, 'customer_name': 'IP Capital (BitCo)'}]}, ['cove', 'sentinelone'])

    def resolution(self, user=None):
        with patch('api.clients.source_customers', return_value=self.FOUND):
            return self.call('get', '/api/clients/resolution', user or self.staff)

    def test_resolution_groups_unlinked_customers_and_flags_the_inference(self):
        body = self.resolution().json()
        names = {p['name']: p for p in body['proposals']}
        group = names['IP Capital: BITCO']
        self.assertEqual([m['source'] for m in group['members']], ['autotask', 'ncentral'])
        self.assertTrue(group['inferred'])
        self.assertIn('inference', group['message'])
        self.assertEqual(group['missing_from'], [])                       # cove and sentinelone could not be read, so not counted
        self.assertEqual(names['Blue Crane']['discrepancy'], 'Found in Autotask. Not found in N-central.')
        self.assertEqual([s['available'] for s in body['sources']], [True, True, False, False])
        self.assertEqual(body['summary']['proposals'], 2)

    def test_resolution_is_staff_only(self):
        self.assertEqual(self.resolution(self.plain).status_code, 403)

    def test_confirming_a_group_creates_a_client_with_links_and_the_other_spellings_as_names(self):
        group = next(p for p in self.resolution().json()['proposals'] if p['name'] == 'IP Capital: BITCO')
        result = self.call('post', '/api/clients/resolve', self.staff, {'groups': [{'name': group['name'], 'members': group['members']}]}).json()
        self.assertEqual((result['created'], result['extended'], result['errors']), (1, 0, []))
        client = self.call('get', '/api/clients', self.staff).json()[0]
        self.assertEqual(client['sources'], [{'source': 'autotask', 'external_id': '101'}, {'source': 'ncentral', 'external_id': '7'}])
        # "IP Capital (BitCo)" has the same words, so it needs no alias: the client's own name already matches it.
        self.assertEqual(client['aliases'], [])
        again = self.resolution().json()
        self.assertEqual([p['name'] for p in again['proposals']], ['Blue Crane'])      # the linked customers are gone from the list

    def test_confirming_can_add_customers_to_an_existing_client(self):
        client_id = self.make('Blue Crane Mining', aliases=['Blue Crane']).json()['id']
        proposal = next(p for p in self.resolution().json()['proposals'] if p['members'][0]['external_id'] == '102')   # named after the existing client, so find it by its customer
        self.assertEqual(proposal['existing_client_id'], client_id)
        result = self.call('post', '/api/clients/resolve', self.staff, {'groups': [{'client_id': client_id, 'members': proposal['members']}]}).json()
        self.assertEqual((result['created'], result['extended']), (0, 1))
        self.assertEqual(self.call('get', '/api/clients', self.staff).json()[0]['sources'], [{'source': 'autotask', 'external_id': '102'}])

    def test_a_conflicting_group_is_reported_and_the_others_still_save(self):
        self.make('Blue Crane')
        members = [{'source': 'autotask', 'external_id': '102', 'customer_name': 'Blue Crane'}]
        fine = [{'source': 'ncentral', 'external_id': '7', 'customer_name': 'IP Capital (BitCo)'}]
        result = self.call('post', '/api/clients/resolve', self.staff, {'groups': [{'name': 'Blue Crane', 'members': members},
                                                                                 {'name': 'IP Capital', 'members': fine}]}).json()
        self.assertEqual((result['created'], len(result['errors'])), (1, 1))
        self.assertIn('already used', result['errors'][0]['error'])

    def test_resolve_needs_staff_and_valid_input(self):
        self.assertEqual(self.call('post', '/api/clients/resolve', self.plain, {'groups': [{'members': []}]}).status_code, 403)
        self.assertEqual(self.call('post', '/api/clients/resolve', self.staff, {'groups': []}).status_code, 400)
        empty = self.call('post', '/api/clients/resolve', self.staff, {'groups': [{'name': 'X', 'members': []}]}).json()
        self.assertEqual(empty['errors'][0]['error'], 'No customers in this group')

    def test_gaps_are_recorded_the_first_time_they_are_seen_and_the_page_says_since_when(self):
        body = self.resolution().json()
        self.assertEqual({g.key for g in ClientGap.objects.filter(resolved_at__isnull=True)}, {'unlinked:autotask:101', 'unlinked:autotask:102', 'unlinked:ncentral:7'})
        group = next(p for p in body['proposals'] if p['name'] == 'IP Capital: BITCO')
        self.assertIsNotNone(group['since'])
        self.assertEqual(body['summary']['open_gaps'], 3)

    def test_a_gap_that_stays_keeps_the_date_it_was_first_seen(self):
        self.resolution()
        old = timezone.now() - timedelta(days=9)
        ClientGap.objects.update(first_seen_at=old, last_seen_at=old)
        body = self.resolution().json()
        gap = ClientGap.objects.get(key='unlinked:autotask:102')
        self.assertEqual(gap.first_seen_at, old)                       # still the first time
        self.assertGreater(gap.last_seen_at, old)                      # but seen again
        blue = next(p for p in body['proposals'] if p['name'] == 'Blue Crane')
        self.assertTrue(blue['since'].startswith(old.strftime('%Y-%m-%dT%H:%M')))

    def test_a_gap_that_goes_away_is_resolved_and_listed_as_recently_resolved(self):
        group = next(p for p in self.resolution().json()['proposals'] if p['name'] == 'IP Capital: BITCO')
        self.call('post', '/api/clients/resolve', self.staff, {'groups': [{'name': group['name'], 'members': group['members']}]})
        body = self.resolution().json()
        resolved = ClientGap.objects.filter(resolved_at__isnull=False)
        self.assertEqual({g.key for g in resolved}, {'unlinked:autotask:101', 'unlinked:ncentral:7'})
        self.assertEqual({r['source'] for r in body['recently_resolved']}, {'autotask', 'ncentral'})
        self.assertEqual(body['summary']['open_gaps'], 1)              # Blue Crane is still unlinked

    def test_a_source_that_could_not_be_read_does_not_close_its_gaps(self):
        now = timezone.now()
        ClientGap.objects.create(key='unlinked:cove:77', kind='unlinked', source='cove', label='Gone', external_id='77', first_seen_at=now, last_seen_at=now)
        self.resolution()                                              # cove is among the sources that could not be read
        self.assertIsNone(ClientGap.objects.get(key='unlinked:cove:77').resolved_at)

    def test_a_gap_whose_source_was_read_and_no_longer_has_it_is_closed(self):
        now = timezone.now()
        ClientGap.objects.create(key='unlinked:autotask:999', kind='unlinked', source='autotask', label='Gone', external_id='999', first_seen_at=now, last_seen_at=now)
        self.resolution()
        self.assertIsNotNone(ClientGap.objects.get(key='unlinked:autotask:999').resolved_at)

    def test_a_failure_in_the_record_does_not_stop_the_page(self):
        with patch('api.gaps.refresh_gaps', side_effect=RuntimeError('database trouble')), self.assertLogs('api.views', level='ERROR'):   # logged, not printed
            response = self.resolution()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['open_gaps'], 0)
        self.assertEqual(len(response.json()['proposals']), 2)
        self.assertIsNone(response.json()['proposals'][0]['since'])

    def test_missing_and_stale_gaps_of_a_client_are_recorded_against_it(self):
        client_id = self.make('Acme', sources=[{'source': 'autotask', 'external_id': '999'}]).json()['id']
        body = self.resolution().json()
        keys = {g.key for g in ClientGap.objects.filter(resolved_at__isnull=True)}
        self.assertIn(f'stale:{client_id}:autotask:999', keys)
        row = next(r for r in body['clients'] if r['id'] == client_id)
        self.assertEqual(len(row['since']), len(row['discrepancies']))
        self.assertTrue(all(row['since']))
        self.assertEqual(ClientGap.objects.get(key=f'stale:{client_id}:autotask:999').client_id, client_id)

    def test_deleting_a_client_does_not_leave_a_gap_pointing_at_nothing(self):
        client_id = self.make('Acme', sources=[{'source': 'autotask', 'external_id': '999'}]).json()['id']
        self.resolution()
        Client.objects.filter(pk=client_id).delete()
        self.assertFalse(ClientGap.objects.filter(client_id=client_id).exists())

    def test_resolution_reports_clients_missing_from_a_source(self):
        self.make('Acme', sources=[{'source': 'autotask', 'external_id': '999'}])
        row = self.resolution().json()['clients'][0]
        self.assertEqual(row['stale'], [{'source': 'autotask', 'external_id': '999'}])
        self.assertIn('No N-central customer is linked to this client.', row['discrepancies'])


class ReportsApi(StaffClient, CacheClearMixin, TestCase):
    CONFIG = {'version': 2, 'title': 'Monthly pack', 'annex': True, 'blocks': [{'type': 'tickets', 'filters': {'customer': 'Acme'}}, {'type': 'backups'}]}

    def make(self, user=None, name='Monthly', **extra):
        return self.call('post', '/api/reports', user or self.plain, {'name': name, 'config': self.CONFIG, **extra})

    def test_a_report_needs_a_signed_in_user(self):
        self.assertEqual(self.client.get('/api/reports').status_code, 401)
        self.assertEqual(self.client.post('/api/reports', {}, content_type='application/json').status_code, 401)

    def test_creating_and_listing_your_own_reports(self):
        created = self.make()
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual((body['name'], body['owner'], body['mine'], body['shared']), ('Monthly', 'plain', True, False))
        self.assertEqual([b['id'] for b in body['config']['blocks']], ['b1', 'b2'])      # the stored config is the normalised one
        self.assertEqual(body['config']['blocks'][0]['filters']['customer'], 'Acme')
        listed = self.call('get', '/api/reports', self.plain).json()
        self.assertEqual([r['name'] for r in listed], ['Monthly'])

    def test_reports_are_private_unless_shared(self):
        self.make(self.plain, 'Private')
        self.assertEqual(self.call('get', '/api/reports', self.staff).json(), [])
        self.make(self.staff, 'Company pack', shared=True)
        seen = self.call('get', '/api/reports', self.plain).json()
        self.assertEqual([(r['name'], r['mine']) for r in seen], [('Company pack', False), ('Private', True)])

    def test_only_staff_can_share_a_report(self):
        response = self.make(self.plain, 'Mine', shared=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Only staff', response.json()['error'])
        self.assertEqual(self.make(self.staff, 'Theirs', shared=True).status_code, 201)

    def test_a_name_is_unique_per_owner_ignoring_case(self):
        self.make(self.plain, 'Monthly')
        self.assertEqual(self.make(self.plain, 'monthly').status_code, 400)
        self.assertEqual(self.make(self.staff, 'Monthly').status_code, 201)          # another owner may use the same name

    def test_invalid_input_is_refused_with_a_reason(self):
        for body in ({'name': '', 'config': self.CONFIG}, {'name': 'x' * 121, 'config': self.CONFIG}, {'name': 'A', 'config': {'version': 2, 'blocks': []}},
                     {'name': 'A', 'config': {'version': 9, 'blocks': [{'type': 'tickets'}]}}, {'name': 'A', 'config': self.CONFIG, 'shared': 'yes'}, {'name': 'A'}):
            response = self.call('post', '/api/reports', self.plain, body)
            self.assertEqual(response.status_code, 400, body)
            self.assertTrue(response.json()['error'])

    def test_a_version_one_template_is_stored_as_version_two(self):
        old = {'version': 1, 'sections': {'tickets': True, 'patches': True}, 'filters': {'customer': 'Acme', 'from': '2026-09-01', 'to': '2026-09-30'}}
        stored = self.call('post', '/api/reports', self.plain, {'name': 'Old', 'config': old}).json()['config']
        self.assertEqual((stored['version'], [b['type'] for b in stored['blocks']], stored['period']), (2, ['tickets', 'patches'], {'from': '2026-09-01', 'to': '2026-09-30'}))

    def test_updating_and_deleting_your_own_report(self):
        report_id = self.make().json()['id']
        changed = self.call('put', f'/api/reports/{report_id}', self.plain, {'name': 'Renamed', 'config': {**self.CONFIG, 'title': 'New title'}})
        self.assertEqual((changed.status_code, changed.json()['name'], changed.json()['config']['title']), (200, 'Renamed', 'New title'))
        kept = self.call('put', f'/api/reports/{report_id}', self.plain, {'name': 'Renamed again'}).json()
        self.assertEqual(kept['config']['title'], 'New title')                       # a body without config keeps the stored one
        self.assertEqual(self.call('delete', f'/api/reports/{report_id}', self.plain).status_code, 200)
        self.assertEqual(self.call('get', '/api/reports', self.plain).json(), [])

    def test_other_people_cannot_see_edit_or_delete_a_private_report(self):
        report_id = self.make(self.staff, 'Boss only').json()['id']
        self.assertEqual(self.call('put', f'/api/reports/{report_id}', self.plain, {'name': 'x'}).status_code, 404)
        self.assertEqual(self.call('delete', f'/api/reports/{report_id}', self.plain).status_code, 404)

    def test_a_shared_report_can_be_read_by_everyone_but_changed_only_by_its_owner_or_staff(self):
        report_id = self.make(self.staff, 'Company pack', shared=True).json()['id']
        self.assertEqual(self.call('put', f'/api/reports/{report_id}', self.plain, {'name': 'Hijacked'}).status_code, 403)
        self.assertEqual(self.call('delete', f'/api/reports/{report_id}', self.plain).status_code, 403)
        self.assertEqual(self.call('put', f'/api/reports/{report_id}', self.staff, {'name': 'Company pack 2'}).status_code, 200)

    def test_staff_can_change_another_persons_shared_report_and_the_owner_stays(self):
        other = get_user_model().objects.create_user('boss2', password='x', is_staff=True)
        report_id = self.make(self.staff, 'Company pack', shared=True).json()['id']
        changed = self.call('put', f'/api/reports/{report_id}', other, {'name': 'Edited by the other boss'})
        self.assertEqual((changed.status_code, changed.json()['owner'], changed.json()['mine']), (200, 'boss', False))
        self.assertEqual(self.call('delete', f'/api/reports/{report_id}', other).status_code, 200)

    def test_private_reports_stay_invisible_to_staff_as_well(self):
        report_id = self.make(self.plain, 'Theirs').json()['id']
        self.assertEqual(self.call('get', '/api/reports', self.staff).json(), [])
        self.assertEqual(self.call('put', f'/api/reports/{report_id}', self.staff, {'name': 'Edited'}).status_code, 404)

    def test_an_unknown_report_is_a_404(self):
        self.assertEqual(self.call('put', '/api/reports/999', self.plain, {'name': 'x'}).status_code, 404)
        self.assertEqual(self.call('delete', '/api/reports/999', self.plain).status_code, 404)

    def test_the_owner_may_keep_a_report_shared_after_losing_staff_but_not_share_a_new_one(self):
        report_id = self.make(self.staff, 'Pack', shared=True).json()['id']
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.call('put', f'/api/reports/{report_id}', self.staff, {'name': 'Pack renamed'}).status_code, 200)
        self.assertEqual(self.make(self.staff, 'Another', shared=True).status_code, 400)


class ReportWeightsParameter(StaffClient, CacheClearMixin, ReportsFakeMixin, TestCase):
    def get(self, query):
        return self.call('get', f'/api/sections/patches?{query}', self.plain)

    def test_a_valid_weights_parameter_is_accepted(self):
        self.assertEqual(self.get('weights=%7B%22third-party%22%3A3%7D').status_code, 200)

    def test_a_bad_weights_parameter_is_a_400(self):
        for raw in ('not-json', '%5B1%5D', '%7B%22Bad%20Key%22%3A1%7D', '%7B%22third-party%22%3A-1%7D', '%7B%22third-party%22%3A%221%22%7D', '%22text%22'):
            self.assertEqual(self.get(f'weights={raw}').status_code, 400, raw)

    def test_an_overlong_weights_parameter_is_refused(self):
        self.assertEqual(self.get('weights=' + 'a' * 20001).status_code, 400)


class SeveralClientsApi(StaffClient, CacheClearMixin, TestCase):
    """Naming several clients at once, and the combined history of several clients."""

    def setUp(self):
        super().setUp()
        company = Company.objects.create(name='Alpha')
        for user in (self.staff, self.plain):
            CompanyMember.objects.create(user=user, company=company)
        self.acme = Client.objects.create(name='Acme', company=company)
        self.zed = Client.objects.create(name='Zed', company=company)

    def fake(self):
        return FakeReports(resolver=Resolver({}, {'acme': self.acme.pk, 'zed': self.zed.pk}), org_units={self.acme.pk: [10, 20], self.zed.pk: [30]})

    def get(self, path, user=None):
        with self.fake():
            return self.call('get', path, user or self.plain)

    def snapshot(self, client, installed, missing, run=1, kind='windows-security'):
        component = {'type': kind, 'category_id': 'g', 'category': 'c', 'installed': installed, 'missing': missing, 'compliance': None}
        PatchSnapshot.objects.create(run_id=run, client=client, captured_at=timezone.now(), scores={}, components=[component])

    def test_several_clients_are_accepted_and_echoed(self):
        response = self.get(f'/api/sections/patches?client={self.acme.pk},{self.zed.pk}')
        self.assertEqual((response.status_code, response.json()['client']), (200, f'{self.acme.pk},{self.zed.pk}'))
        self.assertEqual(self.get('/api/sections/patches?client=1&org=10,20').status_code, 200)

    def test_a_list_that_is_not_numbers_is_a_400_with_a_reason(self):
        for query in ('client=1,', 'client=a,b', 'client=1;2', 'client=' + ','.join(str(i) for i in range(51)), 'org=1;2', 'org=-1', 'device=abc', "client=1'%20OR%20'1"):
            response = self.get(f'/api/sections/patches?{query}')
            self.assertEqual(response.status_code, 400, query)
            self.assertIn('must be a number', response.json()['error'])

    def test_the_combined_history_adds_the_counts_up(self):
        self.snapshot(self.acme, 60, 0)
        self.snapshot(self.zed, 20, 20)
        body = self.get(f'/api/sections/patch-trend?client={self.acme.pk},{self.zed.pk}').json()
        self.assertEqual(body['y'], [80.0])                                              # 80 installed of 100, not the average of 100 and 50
        self.assertIn('combined: 2 clients', body['heading'])
        PatchSnapshot.objects.filter(client=self.zed).update(scores={'overall': 50.0})
        only_zed = self.get(f'/api/sections/patch-trend?client={self.zed.pk}').json()
        self.assertEqual(only_zed['y'], [50.0])                                           # one client still reads its own saved scores, with no 'combined' note
        self.assertNotIn('combined', only_zed['heading'])

    def test_a_customer_with_no_history_is_mentioned_in_the_heading(self):
        self.snapshot(self.acme, 10, 0)
        self.snapshot(self.zed, 10, 0)
        body = self.get(f'/api/sections/patch-trend?client={self.acme.pk},{self.zed.pk}&org=99').json()
        self.assertIn('not linked to a client have no history', body['heading'])

    def test_with_no_snapshots_the_combined_history_is_empty_not_invented(self):
        body = self.get(f'/api/sections/patch-trend?client={self.acme.pk},{self.zed.pk}').json()
        self.assertEqual((body['x'], body['y']), ([], []))

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_a_limited_person_gets_the_combined_history_of_their_own_clients_only(self):
        self.snapshot(self.acme, 60, 0)
        self.snapshot(self.zed, 20, 20)
        self.call('put', '/api/access/plain', self.staff, {'clients': [self.acme.pk, self.zed.pk]})
        self.assertEqual(self.get(f'/api/sections/patch-trend?client={self.acme.pk},{self.zed.pk}').json()['y'], [80.0])
        self.call('put', '/api/access/plain', self.staff, {'clients': [self.acme.pk]})
        self.assertEqual(self.get(f'/api/sections/patch-trend?client={self.acme.pk},{self.zed.pk}').status_code, 403)   # cannot widen it by adding one
        self.assertEqual(self.get(f'/api/sections/patches?client={self.acme.pk},{self.zed.pk}').status_code, 403)


class ClientAccessApi(StaffClient, CacheClearMixin, TestCase):
    """Who may see which clients. The fake reporting data names its customers (Acme, Zed), so each test builds clients with those names and
    the fake data is then resolved against their real IDs (a real test database hands out its own, not 1 and 2)."""

    def setUp(self):
        super().setUp()
        self.company = Company.objects.create(name='Alpha')
        for user in (self.staff, self.plain):
            CompanyMember.objects.create(user=user, company=self.company)
        self.acme = Client.objects.create(name='Acme', company=self.company)
        self.zed = Client.objects.create(name='Zed', company=self.company)

    def fake(self):
        return FakeReports(resolver=Resolver({}, {'acme': self.acme.pk, 'zed': self.zed.pk}), org_units={self.acme.pk: [10, 20], self.zed.pk: []})

    def grant(self, *clients):
        self.call('put', '/api/access/plain', self.staff, {'clients': [c.pk for c in clients]})

    def get(self, path, user=None):
        with self.fake():
            return self.call('get', path, user or self.plain)

    def servers(self, user=None):
        return [s['computer_name'] for s in self.get('/api/sections/server-health', user).json()['servers']]

    def test_nothing_changes_while_access_is_not_enforced(self):
        self.assertEqual(self.servers(), ['SRV-A', 'SRV-B', 'PC-1'])
        self.assertEqual([c['name'] for c in self.call('get', '/api/clients', self.plain).json()], ['Acme', 'Zed'])

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_a_person_with_no_clients_sees_none_once_access_is_enforced(self):
        self.assertEqual(self.servers(), [])
        self.assertEqual(self.get('/api/sections/backups').json()['rows'], [])
        self.assertEqual(self.call('get', '/api/clients', self.plain).json(), [])

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_giving_a_client_shows_only_that_client(self):
        self.grant(self.acme)
        self.assertEqual(self.servers(), ['SRV-A', 'SRV-B'])
        self.assertEqual([c['name'] for c in self.call('get', '/api/clients', self.plain).json()], ['Acme'])
        self.grant(self.acme, self.zed)
        self.assertEqual(self.servers(), ['SRV-A', 'SRV-B', 'PC-1'])

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_administrators_see_all_of_their_companys_clients(self):
        self.assertEqual(self.servers(self.staff), ['SRV-A', 'SRV-B', 'PC-1'])
        self.assertEqual(len(self.call('get', '/api/clients', self.staff).json()), 2)

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_asking_for_a_client_or_customer_you_may_not_see_is_refused_not_shown_empty(self):
        self.grant(self.acme)
        for path in (f'/api/sections/patches?client={self.zed.pk}', f'/api/sections/patch-entries?type=windows-security&client={self.zed.pk}',
                     f'/api/sections/patch-trend?client={self.zed.pk}', '/api/sections/patches?org=30'):
            self.assertEqual(self.get(path).status_code, 403, path)
        self.assertEqual(self.get(f'/api/sections/patches?client={self.acme.pk}').status_code, 200)
        self.assertEqual(self.get('/api/sections/patches?org=10').status_code, 200)             # a customer of Acme

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_a_device_of_another_client_cannot_be_opened_and_looks_like_one_that_does_not_exist(self):
        self.grant(self.acme)
        self.assertEqual(self.get('/api/sections/backup-detail?device=1').status_code, 200)     # SRV-FILE belongs to Acme
        self.assertEqual(self.get('/api/sections/backup-detail?device=2').status_code, 404)     # PC-RECEPTION belongs to Zed
        self.assertEqual(self.get('/api/sections/backup-detail?device=999').status_code, 404)

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_the_trend_is_your_own_clients_never_the_history_of_everyone(self):
        def component(installed, missing):
            return [{'type': 'windows-security', 'category_id': 'g', 'category': 'c', 'installed': installed, 'missing': missing, 'compliance': None}]
        PatchSnapshot.objects.create(run_id=1, client=self.acme, captured_at=timezone.now(), scores={'overall': 100.0}, components=component(60, 0))
        PatchSnapshot.objects.create(run_id=1, client=self.zed, captured_at=timezone.now(), scores={'overall': 50.0}, components=component(20, 20))
        PatchSnapshot.objects.create(run_id=1, client=None, captured_at=timezone.now(), scores={'overall': 12.0})   # everyone combined: not theirs to see
        self.grant(self.acme)
        self.assertEqual(self.get('/api/sections/patch-trend').json()['y'], [100.0])               # Acme's own
        self.grant(self.acme, self.zed)
        body = self.get('/api/sections/patch-trend').json()
        self.assertEqual(body['y'], [80.0])                                                         # their two clients together: 80 installed of 100
        self.assertIn('combined: 2 clients', body['heading'])

    def test_the_trend_for_someone_with_no_limit_is_still_the_combined_history(self):
        PatchSnapshot.objects.create(run_id=1, client=None, captured_at=timezone.now(), scores={'overall': 50.0})
        self.assertEqual(self.get('/api/sections/patch-trend').json()['y'], [50.0])

    def login(self, username):
        response = self.client.post('/api/auth/login', {'username': username, 'password': 'x'}, content_type='application/json', HTTP_X_REQUESTED_WITH='fetch')
        return response.json()['user']

    def test_signing_in_says_whether_you_are_limited(self):
        self.assertFalse(self.login('plain')['scoped'])                                           # not enforced
        with override_settings(CLIENT_ACCESS_ENFORCED=True):
            self.assertTrue(self.login('plain')['scoped'])
            self.assertFalse(self.login('boss')['scoped'])                                        # staff

    def test_who_is_limited_and_to_what(self):
        from . import access
        self.assertIsNone(access.allowed_client_ids(self.plain))                                  # not enforced
        ClientAccess.objects.create(user=self.plain, client=self.acme)
        with override_settings(CLIENT_ACCESS_ENFORCED=True):
            self.assertEqual(access.allowed_client_ids(self.plain), frozenset({self.acme.pk}))
            admin = access.allowed_client_ids(self.staff)                                         # an administrator: their company's clients, not everything
            self.assertEqual((set(admin), admin.unlinked), ({self.acme.pk, self.zed.pk}, True))
            self.assertEqual(access.allowed_client_ids(get_user_model().objects.create_user('new', password='x')), frozenset())   # in no company: nothing
            owner = get_user_model().objects.create_user('top', password='x', is_staff=True, is_superuser=True)
            self.assertIsNone(access.allowed_client_ids(owner))

    def test_the_access_overview_is_for_staff_and_lists_people_and_their_clients(self):
        self.grant(self.acme)
        self.assertEqual(self.call('get', '/api/access', self.plain).status_code, 403)
        self.assertEqual(self.client.get('/api/access').status_code, 401)
        body = self.call('get', '/api/access', self.staff).json()
        self.assertEqual((body['enforced'], [c['name'] for c in body['clients']]), (False, ['Acme', 'Zed']))
        people = {u['username']: u for u in body['users']}
        self.assertEqual(people['plain']['clients'], [self.acme.pk])
        self.assertEqual((people['boss']['is_staff'], people['boss']['clients']), (True, []))

    @override_settings(CLIENT_ACCESS_ENFORCED=True)
    def test_the_access_overview_says_when_the_limits_are_on(self):
        self.assertTrue(self.call('get', '/api/access', self.staff).json()['enforced'])

    def test_setting_access_replaces_what_was_there_and_can_be_repeated(self):
        self.grant(self.acme)
        self.assertEqual(self.call('put', '/api/access/plain', self.staff, {'clients': [self.zed.pk]}).json()['clients'], [self.zed.pk])
        self.assertEqual(list(ClientAccess.objects.filter(user=self.plain).values_list('client_id', flat=True)), [self.zed.pk])
        self.grant(self.zed)
        self.assertEqual(ClientAccess.objects.filter(user=self.plain).count(), 1)                # no duplicate
        self.assertEqual(self.call('put', '/api/access/plain', self.staff, {'clients': []}).json()['clients'], [])
        self.assertEqual(ClientAccess.objects.filter(user=self.plain).count(), 0)

    def test_setting_access_needs_staff_and_valid_input(self):
        self.assertEqual(self.call('put', '/api/access/plain', self.plain, {'clients': []}).status_code, 403)
        self.assertEqual(self.call('put', '/api/access/nobody', self.staff, {'clients': []}).status_code, 404)
        for bad in ({}, {'clients': 'all'}, {'clients': [True]}, {'clients': ['1']}, {'clients': [1.5]}, {'clients': list(range(501))}):
            self.assertEqual(self.call('put', '/api/access/plain', self.staff, bad).status_code, 400, bad)
        response = self.call('put', '/api/access/plain', self.staff, {'clients': [self.acme.pk, 987654]})
        self.assertEqual((response.status_code, 'Unknown client' in response.json()['error']), (400, True))
        self.assertEqual(ClientAccess.objects.filter(user=self.plain).count(), 0)                # nothing was changed by the refused request

    def test_deleting_a_client_or_a_user_removes_their_access(self):
        self.grant(self.acme, self.zed)
        Client.objects.filter(pk=self.zed.pk).delete()
        self.assertEqual(ClientAccess.objects.filter(user=self.plain).count(), 1)
        self.plain.delete()
        self.assertEqual(ClientAccess.objects.count(), 0)

    def test_the_command_lists_grants_and_revokes(self):
        out = StringIO()
        call_command('client_access', 'list', stdout=out)
        self.assertIn('Nobody has been given a client yet.', out.getvalue())
        call_command('client_access', 'grant', 'plain', 'acme', stdout=out)                       # the client name ignores capitals
        call_command('client_access', 'grant', 'plain', 'Acme', stdout=out)
        self.assertEqual(ClientAccess.objects.filter(user=self.plain, client=self.acme).count(), 1)
        self.assertIn('could already see', out.getvalue())
        out = StringIO()
        call_command('client_access', 'list', stdout=out)
        self.assertIn('plain: Acme', out.getvalue())
        call_command('client_access', 'revoke', 'plain', 'Acme', stdout=out)
        self.assertEqual(ClientAccess.objects.count(), 0)

    def test_the_command_refuses_an_unknown_person_or_client_or_missing_arguments(self):
        for args in (('grant', 'nobody', 'Acme'), ('grant', 'plain', 'No Such Client'), ('grant', 'plain'), ('revoke',)):
            with self.assertRaises(CommandError, msg=str(args)):
                call_command('client_access', *args, stdout=StringIO())


@override_settings(CLIENT_ACCESS_ENFORCED=True)
class CompaniesApi(StaffClient, CacheClearMixin, TestCase):
    """Companies are walls. Alpha has clients Acme and Apex, Beta has Zed, and Free Co is unclaimed. `top` is the platform owner; `admin_a`
    and `admin_b` administer Alpha and Beta; `member_a` works for Alpha. The fake reporting data's customers are Acme (Alpha) and Zed (Beta),
    and one Cove customer is linked to no client at all. Each test asks: can one company learn anything about the other?"""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.alpha, self.beta = Company.objects.create(name='Alpha'), Company.objects.create(name='Beta')
        self.owner = User.objects.create_user('top', password='x', is_staff=True, is_superuser=True)
        self.admin_a = User.objects.create_user('admin_a', password='x', is_staff=True)
        self.admin_b = User.objects.create_user('admin_b', password='x', is_staff=True)
        self.member_a = User.objects.create_user('member_a', password='x')
        for user, company in ((self.admin_a, self.alpha), (self.member_a, self.alpha), (self.admin_b, self.beta)):
            CompanyMember.objects.create(user=user, company=company)
        self.a1 = Client.objects.create(name='Acme', company=self.alpha)
        self.a2 = Client.objects.create(name='Apex', company=self.alpha)
        self.b1 = Client.objects.create(name='Zed', company=self.beta)
        self.free = Client.objects.create(name='Free Co')

    def fake(self):
        return FakeReports(resolver=Resolver({}, {'acme': self.a1.pk, 'zed': self.b1.pk}), org_units={self.a1.pk: [10, 20], self.b1.pk: []})

    def get(self, path, user):
        with self.fake():
            return self.call('get', path, user)

    def servers(self, user):
        return [s['computer_name'] for s in self.get('/api/sections/server-health', user).json()['servers']]

    def client_names(self, user):
        return sorted(c['name'] for c in self.call('get', '/api/clients', user).json())

    # ---- the data
    def test_an_administrator_sees_only_their_own_companys_data(self):
        self.assertEqual(self.servers(self.admin_a), ['SRV-A', 'SRV-B'])
        self.assertEqual(self.servers(self.admin_b), ['PC-1'])
        self.assertEqual(self.servers(self.owner), ['SRV-A', 'SRV-B', 'PC-1'])                      # the platform owner sees all of it

    def test_backups_follow_the_wall_and_a_device_with_no_client_is_for_administrators(self):
        def devices(user):
            return sorted(r['device_name'] for r in self.get('/api/sections/backups', user).json()['rows'])
        self.assertEqual(devices(self.admin_a), ['SRV-FILE', 'm365-mailboxes'])
        self.assertEqual(devices(self.admin_b), ['PC-RECEPTION', 'm365-mailboxes'])
        self.assertEqual(devices(self.member_a), [])                                                # given no clients, sees none

    def test_one_company_cannot_open_the_others_device(self):
        self.assertEqual(self.get('/api/sections/backup-detail?device=1', self.admin_a).status_code, 200)   # SRV-FILE: Acme, Alpha
        self.assertEqual(self.get('/api/sections/backup-detail?device=2', self.admin_a).status_code, 404)   # PC-RECEPTION: Zed, Beta
        self.assertEqual(self.get('/api/sections/backup-detail?device=2', self.admin_b).status_code, 200)
        self.assertEqual(self.get('/api/sections/backup-detail?device=1', self.admin_b).status_code, 404)

    def test_naming_the_others_client_is_refused_not_shown_empty(self):
        for path in (f'/api/sections/patches?client={self.b1.pk}', f'/api/sections/patch-trend?client={self.b1.pk}',
                     f'/api/sections/patch-entries?type=windows-security&client={self.b1.pk}', f'/api/sections/patches?client={self.a1.pk},{self.b1.pk}'):
            self.assertEqual(self.get(path, self.admin_a).status_code, 403, path)
        self.assertEqual(self.get(f'/api/sections/patches?client={self.a1.pk}', self.admin_a).status_code, 200)

    def test_an_administrator_in_no_company_sees_nothing(self):
        self.assertEqual(self.servers(self.staff), [])                                              # 'boss' is staff but belongs to no company
        self.assertEqual(self.client_names(self.staff), [])

    def test_the_owner_is_not_limited_to_a_company(self):
        self.assertEqual(self.client_names(self.owner), ['Acme', 'Apex', 'Free Co', 'Zed'])

    # ---- clients
    def test_the_client_list_is_the_companys_clients_and_the_unclaimed_ones(self):
        self.assertEqual(self.client_names(self.admin_a), ['Acme', 'Apex', 'Free Co'])
        self.assertEqual(self.client_names(self.admin_b), ['Free Co', 'Zed'])
        self.assertEqual(self.client_names(self.member_a), [])
        companies = {c['name']: (c['company'] or {}).get('name') for c in self.call('get', '/api/clients', self.owner).json()}
        self.assertEqual(companies, {'Acme': 'Alpha', 'Apex': 'Alpha', 'Zed': 'Beta', 'Free Co': None})

    def test_editing_the_others_client_looks_like_it_does_not_exist(self):
        body = {'name': 'Zed', 'sources': [], 'aliases': []}
        self.assertEqual(self.call('put', f'/api/clients/{self.b1.pk}', self.admin_a, body).status_code, 404)
        self.assertEqual(self.call('put', f'/api/clients/{self.b1.pk}', self.admin_b, body).status_code, 200)
        self.assertEqual(self.call('put', f'/api/clients/{self.free.pk}', self.admin_a, {'name': 'Free Co', 'sources': [], 'aliases': []}).status_code, 200)

    def test_a_new_client_joins_the_creators_company(self):
        def create(user, name, **extra):
            return self.call('post', '/api/clients', user, {'name': name, 'sources': [], 'aliases': [], **extra})
        self.assertEqual(create(self.admin_a, 'New A').json()['company']['name'], 'Alpha')
        self.assertEqual(create(self.admin_b, 'New B').json()['company']['name'], 'Beta')
        self.assertEqual(create(self.admin_a, 'Sneaky', company=self.beta.pk).json()['company']['name'], 'Alpha')   # an administrator cannot choose another company
        self.assertEqual(create(self.owner, 'Owner B', company=self.beta.pk).json()['company']['name'], 'Beta')
        self.assertIsNone(create(self.owner, 'Owner none').json()['company'])
        self.assertEqual(create(self.owner, 'Owner bad', company=987654).status_code, 400)
        self.assertEqual(create(self.member_a, 'By a member').status_code, 403)

    def test_an_unclaimed_client_is_claimed_by_the_first_administrator_and_then_belongs_to_that_company(self):
        claim = lambda user, client: self.call('post', f'/api/clients/{client.pk}/claim', user, {})   # noqa: E731
        self.assertEqual(claim(self.admin_a, self.free).json()['company']['name'], 'Alpha')
        self.assertEqual(claim(self.admin_b, self.free).status_code, 404)                          # now Alpha's, so Beta cannot even see it
        self.assertEqual(self.client_names(self.admin_b), ['Zed'])
        self.assertEqual(claim(self.admin_a, self.free).status_code, 200)                          # claiming your own again changes nothing

    def test_a_claimed_client_cannot_be_taken_by_another_company(self):
        for user in (self.admin_a, self.admin_b):
            response = self.call('post', f'/api/clients/{self.b1.pk if user is self.admin_a else self.a1.pk}/claim', user, {})
            self.assertEqual(response.status_code, 404)
        self.b1.refresh_from_db()
        self.assertEqual(self.b1.company_id, self.beta.pk)

    def test_only_administrators_claim_and_only_for_a_company_they_are_in(self):
        self.assertEqual(self.call('post', f'/api/clients/{self.free.pk}/claim', self.member_a, {}).status_code, 403)
        # 'boss' is staff in no company: with the limits on they see no clients at all, so even the unclaimed one is not found
        self.assertEqual(self.call('post', f'/api/clients/{self.free.pk}/claim', self.staff, {}).status_code, 404)
        with override_settings(CLIENT_ACCESS_ENFORCED=False):   # with the limits off they can see it, and are told why they cannot claim it
            response = self.call('post', f'/api/clients/{self.free.pk}/claim', self.staff, {})
        self.assertEqual((response.status_code, response.json()['error']), (403, 'You are not part of a company'))

    def test_the_platform_owner_gives_a_client_to_any_company_or_takes_it_back(self):
        def claim(**body):
            return self.call('post', f'/api/clients/{self.free.pk}/claim', self.owner, body)
        self.assertEqual(claim(company=self.beta.pk).json()['company']['name'], 'Beta')
        self.assertIsNone(claim(company=None).json()['company'])
        self.assertEqual(claim().status_code, 400)                                                 # must say which
        self.assertEqual(claim(company=987654).status_code, 400)
        self.assertEqual(claim(company=True).status_code, 400)

    def test_error_messages_never_name_the_others_clients(self):
        ClientSourceMap.objects.create(client=self.b1, source='autotask', external_id='555')
        clash = self.call('post', '/api/clients', self.admin_a, {'name': 'Zed', 'sources': [], 'aliases': []})
        self.assertEqual(clash.status_code, 400)
        self.assertNotIn('Zed', clash.json()['error'])
        self.assertIn('another client', clash.json()['error'])
        linked = self.call('post', '/api/clients', self.admin_a, {'name': 'Fresh', 'sources': [{'source': 'autotask', 'external_id': '555'}], 'aliases': []})
        self.assertEqual(linked.status_code, 400)
        self.assertNotIn('Zed', linked.json()['error'])
        self.assertIn('another client', linked.json()['error'])
        owner = self.call('post', '/api/clients', self.owner, {'name': 'Fresh', 'sources': [{'source': 'autotask', 'external_id': '555'}], 'aliases': []})
        self.assertIn('Zed', owner.json()['error'])                                                # the owner may be told

    # ---- client resolution
    ROWS = ({'autotask': [{'external_id': 101, 'customer_name': 'Zed'}, {'external_id': 102, 'customer_name': 'Free Co'}, {'external_id': 103, 'customer_name': 'Loose Ltd'}]},
            ['ncentral', 'sentinelone', 'cove'])

    def resolution(self, user):
        with patch('api.clients.source_customers', return_value=self.ROWS):
            return self.call('get', '/api/clients/resolution', user).json()

    def test_a_customer_linked_to_the_others_client_is_not_offered_as_unlinked(self):
        ClientSourceMap.objects.create(client=self.b1, source='autotask', external_id='101')
        ids = sorted(m['external_id'] for p in self.resolution(self.admin_a)['proposals'] for m in p['members'])
        self.assertEqual([str(i) for i in ids], ['102', '103'])                                     # 101 belongs to a client, so it is not free to claim

    def test_resolution_lists_only_the_clients_the_person_may_see(self):
        names = lambda body: sorted(c['name'] for c in body['clients'])   # noqa: E731
        self.assertEqual(names(self.resolution(self.admin_a)), ['Acme', 'Apex', 'Free Co'])
        self.assertEqual(names(self.resolution(self.admin_b)), ['Free Co', 'Zed'])
        self.assertEqual(self.resolution(self.admin_a)['summary']['clients'], 3)

    def test_what_another_company_has_open_is_neither_closed_nor_shown_by_looking(self):
        ClientSourceMap.objects.create(client=self.b1, source='autotask', external_id='999')       # a link to a customer that does not exist
        now = timezone.now()
        ClientGap.objects.create(key=f'stale:{self.b1.pk}:autotask:999', kind='stale', source='autotask', client=self.b1, external_id='999', label='Zed', first_seen_at=now, last_seen_at=now)
        ClientGap.objects.create(key='missing:x', kind='missing', source='autotask', client=self.b1, label='Old Zed gap', first_seen_at=now, last_seen_at=now, resolved_at=now)
        seen_by_a = self.resolution(self.admin_a)
        self.assertIsNone(ClientGap.objects.get(key=f'stale:{self.b1.pk}:autotask:999').resolved_at)   # Alpha looking did not close Beta's gap
        self.assertEqual(seen_by_a['recently_resolved'], [])                                        # and not Beta's resolved one
        seen_by_owner = self.resolution(self.owner)
        self.assertEqual(seen_by_owner['summary']['open_gaps'] - seen_by_a['summary']['open_gaps'], 1)   # the one difference is Beta's open gap
        self.assertEqual([g['label'] for g in seen_by_owner['recently_resolved']], ['Old Zed gap'])

    def test_resolving_creates_clients_in_the_administrators_company_and_cannot_extend_the_others(self):
        group = {'name': 'Loose Ltd', 'members': [{'source': 'autotask', 'external_id': '103', 'customer_name': 'Loose Ltd'}]}
        with patch('api.clients.source_customers', return_value=self.ROWS):
            self.assertEqual(self.call('post', '/api/clients/resolve', self.admin_a, {'groups': [group]}).json()['created'], 1)
            extend = self.call('post', '/api/clients/resolve', self.admin_a, {'groups': [{'client_id': self.b1.pk, 'members': group['members']}]}).json()
        self.assertEqual(Client.objects.get(name='Loose Ltd').company_id, self.alpha.pk)
        self.assertEqual((extend['extended'], extend['errors'][0]['error']), (0, 'That client no longer exists'))   # the same answer as for a client that is gone

    # ---- the access page
    def test_an_administrator_sees_and_manages_only_their_own_companys_people_and_clients(self):
        body = self.call('get', '/api/access', self.admin_a).json()
        self.assertEqual(sorted(u['username'] for u in body['users']), ['admin_a', 'member_a'])
        self.assertEqual(sorted(c['name'] for c in body['clients']), ['Acme', 'Apex'])               # not the unclaimed one, not Beta's
        self.assertEqual((body['owner'], body['company']['name']), (False, 'Alpha'))
        self.assertEqual(self.call('put', '/api/access/admin_b', self.admin_a, {'clients': []}).status_code, 404)   # a person in another company does not exist to them
        self.assertEqual(self.call('put', '/api/access/plain', self.admin_a, {'clients': []}).status_code, 404)     # nor does one in no company
        self.assertEqual(self.call('put', '/api/access/member_a', self.admin_a, {'clients': [self.b1.pk]}).status_code, 400)
        self.assertEqual(self.call('put', '/api/access/member_a', self.admin_a, {'clients': [self.free.pk]}).status_code, 400)   # unclaimed is not theirs to give
        self.assertEqual(self.call('put', '/api/access/member_a', self.admin_a, {'clients': [self.a1.pk]}).json()['clients'], [self.a1.pk])

    def test_the_owner_sees_everyone_on_the_access_page(self):
        body = self.call('get', '/api/access', self.owner).json()
        self.assertTrue({'admin_a', 'admin_b', 'member_a', 'top'} <= {u['username'] for u in body['users']})
        self.assertTrue(body['owner'])
        self.assertEqual({u['username']: (u['company'] or {}).get('name') for u in body['users']}['admin_b'], 'Beta')

    def test_a_member_sees_only_what_they_were_given_and_never_the_others(self):
        self.call('put', '/api/access/member_a', self.admin_a, {'clients': [self.a2.pk]})
        self.assertEqual(self.client_names(self.member_a), ['Apex'])
        ClientAccess.objects.create(user=self.member_a, client=self.b1)                              # a grant outside their company (cannot be made through the API)
        self.assertEqual(self.client_names(self.member_a), ['Apex'])                                # does nothing: the company wall comes first

    # ---- companies
    def test_the_owner_lists_every_company_and_an_administrator_only_their_own(self):
        owner = {c['name']: c for c in self.call('get', '/api/companies', self.owner).json()}
        self.assertTrue({'Alpha', 'Beta'} <= set(owner))
        self.assertEqual((owner['Alpha']['members'], owner['Alpha']['clients']), (2, 2))
        self.assertEqual([c['name'] for c in self.call('get', '/api/companies', self.admin_a).json()], ['Alpha'])
        self.assertEqual(self.call('get', '/api/companies', self.member_a).status_code, 403)
        self.assertEqual(self.call('get', '/api/companies', self.staff).json(), [])                  # staff in no company see none

    def test_only_the_owner_makes_and_renames_companies(self):
        made = self.call('post', '/api/companies', self.owner, {'name': 'Gamma'})
        self.assertEqual((made.status_code, made.json()['name']), (201, 'Gamma'))
        self.assertEqual(self.call('post', '/api/companies', self.admin_a, {'name': 'Delta'}).status_code, 403)
        for bad in ({'name': ''}, {'name': '  '}, {'name': 'gamma'}, {'name': 'x' * 201}, {}):     # blank, repeated ignoring case, too long
            self.assertEqual(self.call('post', '/api/companies', self.owner, bad).status_code, 400, bad)
        pk = made.json()['id']
        self.assertEqual(self.call('put', f'/api/companies/{pk}', self.owner, {'name': 'Gamma Two'}).json()['name'], 'Gamma Two')
        self.assertEqual(self.call('put', f'/api/companies/{pk}', self.owner, {'name': 'Alpha'}).status_code, 400)
        self.assertEqual(self.call('put', f'/api/companies/{pk}', self.admin_a, {'name': 'Mine'}).status_code, 403)
        self.assertEqual(self.call('put', '/api/companies/987654', self.owner, {'name': 'X'}).status_code, 404)

    def test_only_the_owner_moves_people_between_companies_and_the_grants_are_dropped(self):
        ClientAccess.objects.create(user=self.member_a, client=self.a1)
        moved = self.call('put', '/api/users/member_a/company', self.owner, {'company': self.beta.pk})
        self.assertEqual(moved.json()['company']['name'], 'Beta')
        self.assertFalse(ClientAccess.objects.filter(user=self.member_a).exists())                  # the clients they had were Alpha's
        self.assertEqual(self.call('put', '/api/users/member_a/company', self.admin_a, {'company': self.alpha.pk}).status_code, 403)
        self.assertEqual(self.call('put', '/api/users/member_a/company', self.owner, {'company': 987654}).status_code, 400)
        self.assertEqual(self.call('put', '/api/users/member_a/company', self.owner, {'company': True}).status_code, 400)
        self.assertEqual(self.call('put', '/api/users/member_a/company', self.owner, {}).status_code, 400)
        self.assertEqual(self.call('put', '/api/users/nobody/company', self.owner, {'company': None}).status_code, 404)
        self.call('put', '/api/users/member_a/company', self.owner, {'company': None})
        self.assertFalse(CompanyMember.objects.filter(user=self.member_a).exists())

    # ---- data collection
    def test_company_administrators_may_use_the_collectors_but_the_raw_log_is_the_owners(self):
        denied = self.call('get', '/api/collectors/collector_x/latest-log', self.admin_a)
        self.assertEqual((denied.status_code, denied.json()['error']), (403, 'The raw log is for the platform owner'))
        self.assertEqual(self.call('get', '/api/collectors/collector_x/latest-log', self.member_a).json()['error'], 'Forbidden')
        self.assertNotEqual(self.call('get', '/api/collectors/collector_x/latest-log', self.owner).status_code, 403)
        with override_settings(CLIENT_ACCESS_ENFORCED=False):
            self.assertNotEqual(self.call('get', '/api/collectors/collector_x/latest-log', self.admin_a).status_code, 403)

    # ---- who you are
    def login(self, username):
        response = self.client.post('/api/auth/login', {'username': username, 'password': 'x'}, content_type='application/json', HTTP_X_REQUESTED_WITH='fetch')
        return response.json()['user']

    def test_signing_in_says_which_company_and_what_you_are(self):
        admin, member, owner = self.login('admin_a'), self.login('member_a'), self.login('top')
        self.assertEqual((admin['company']['name'], admin['is_owner'], admin['scoped']), ('Alpha', False, False))
        self.assertEqual((member['company']['name'], member['scoped']), ('Alpha', True))
        self.assertEqual((owner['is_owner'], owner['company']), (True, None))


class CompanySetUp(StaffClient, CacheClearMixin, TestCase):
    """The migration that keeps today's setup working, and the command that sets companies up."""

    def migration(self):
        import importlib
        return importlib.import_module('api.migrations.0007_companies')

    def test_everyone_and_every_client_that_exists_goes_into_the_first_company_and_nobody_else_moves(self):
        from django.apps import apps
        from django.conf import settings
        other = Company.objects.create(name='Elsewhere')
        kept = Client.objects.create(name='Already placed', company=other)
        loose = Client.objects.create(name='Loose')
        self.migration().assign_default_company(apps, None)
        first = Company.objects.get(name=settings.DEFAULT_COMPANY_NAME)
        loose.refresh_from_db(), kept.refresh_from_db()
        self.assertEqual((loose.company_id, kept.company_id), (first.pk, other.pk))                 # only unclaimed clients are placed
        self.assertEqual(set(CompanyMember.objects.filter(company=first).values_list('user__username', flat=True)), {'boss', 'plain'})

    def test_it_can_run_again_without_duplicating_anything(self):
        from django.apps import apps
        self.migration().assign_default_company(apps, None)
        before = (Company.objects.count(), CompanyMember.objects.count())
        self.migration().assign_default_company(apps, None)
        self.assertEqual((Company.objects.count(), CompanyMember.objects.count()), before)

    def command(self, *args):
        out = StringIO()
        call_command('company', *args, stdout=out)
        return out.getvalue()

    def test_the_command_creates_lists_and_moves_people_and_clients(self):
        self.assertIn('Created the company Gamma', self.command('create', 'Gamma'))
        with self.assertRaises(CommandError):
            self.command('create', 'gamma')
        self.command('add-user', 'plain', 'Gamma')
        self.assertEqual(CompanyMember.objects.get(user=self.plain).company.name, 'Gamma')
        client = Client.objects.create(name='Loose')
        self.assertIn('now belongs to Gamma', self.command('claim', 'loose', 'gamma'))
        self.assertIn('unclaimed again', self.command('claim', 'Loose', '-'))
        self.assertIn('Gamma: 1 people', self.command('list'))
        self.command('add-user', 'plain', '-')
        self.assertFalse(CompanyMember.objects.filter(user=self.plain).exists())
        client.refresh_from_db()
        self.assertIsNone(client.company_id)

    def test_the_owner_is_made_and_removed_and_is_staff_too(self):
        self.assertIn('now the platform owner', self.command('owner', 'plain'))
        self.plain.refresh_from_db()
        self.assertEqual((self.plain.is_superuser, self.plain.is_staff), (True, True))
        self.assertIn('plain', self.command('list'))
        call_command('company', 'owner', 'plain', '--remove', stdout=StringIO())
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_superuser)

    def test_the_command_refuses_what_does_not_exist(self):
        for args in (('add-user', 'nobody', 'Gamma'), ('add-user', 'plain', 'No Such Company'), ('claim', 'No Such Client', 'Gamma'), ('owner', 'nobody'), ('create',), ('add-user', 'plain')):
            with self.assertRaises(CommandError, msg=str(args)):
                self.command(*args)

    def test_moving_someone_drops_the_clients_they_were_given_in_the_old_company(self):
        company = Company.objects.create(name='Gamma')
        CompanyMember.objects.create(user=self.plain, company=company)
        client = Client.objects.create(name='Gamma Client', company=company)
        ClientAccess.objects.create(user=self.plain, client=client)
        self.command('add-user', 'plain', '-')
        self.assertFalse(ClientAccess.objects.filter(user=self.plain).exists())


class FreshnessApi(StaffClient, CacheClearMixin, TestCase):
    """The "data as of" endpoint: open to anyone signed in, and only a time, a state and a count."""

    def crafted(self):
        jobs = [{'job': 'ncentral_patches', 'enabled': True, 'last_good_at': '2026-11-09T12:29:00+00:00', 'good_run_age_hours': 44.0, 'freshness': 'STALE',
                 'failures_since_good': 3, 'data_as_of': None, 'data_age_hours': None, 'last_exit_code': 1, 'status': 'FAILED'}]
        return patch('api.collection_health.build_health', return_value={'configured': True, 'checked_at': '2026-11-11T08:00:00+00:00', 'jobs': jobs}), \
            patch('api.collection_health.source_capture_times', return_value={})

    def test_anyone_signed_in_can_read_it_and_nobody_else(self):
        build, times = self.crafted()
        with build, times:
            self.assertEqual(self.call('get', '/api/freshness', self.plain).status_code, 200)
            self.assertEqual(self.call('get', '/api/freshness', self.staff).status_code, 200)
        self.assertEqual(self.client.get('/api/freshness').status_code, 401)

    def test_it_says_when_each_pages_data_was_collected_and_whether_that_is_stale(self):
        build, times = self.crafted()
        with build, times:
            body = self.call('get', '/api/freshness', self.plain).json()
        sources = {s['key']: s for s in body['sources']}
        self.assertEqual(body['checked_at'], '2026-11-11T08:00:00+00:00')
        self.assertEqual(set(sources), {'patches', 'servers', 'backups', 'tickets'})
        self.assertEqual((sources['patches']['state'], sources['patches']['as_of'], sources['patches']['failures_since']), ('stale', '2026-11-09T12:29:00+00:00', 3))
        self.assertEqual(sources['tickets']['state'], 'never')

    def test_it_carries_nothing_from_the_logs_and_the_status_page_stays_for_administrators(self):
        build, times = self.crafted()
        with build, times:
            row = self.call('get', '/api/freshness', self.plain).json()['sources'][0]
        self.assertTrue(set(row) <= {'key', 'label', 'as_of', 'age_hours', 'state', 'failures_since', 'basis', 'enabled'})
        self.assertEqual(self.call('get', '/api/collectors/health', self.plain).status_code, 403)           # unchanged: the detail is not opened up

    def test_it_still_answers_when_nothing_is_configured_or_the_database_cannot_be_read(self):
        with patch('api.collection_health.build_health', return_value={'configured': False, 'checked_at': '2026-11-11T08:00:00+00:00', 'jobs': []}), \
                patch('api.collection_health.source_capture_times', return_value={}):
            response = self.call('get', '/api/freshness', self.plain)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(s['state'] == 'never' for s in response.json()['sources']))


class ClientMasterViewOnPostgres(StaffClient, TestCase):
    """Runs the real view SQL. Skipped unless the test database is PostgreSQL (the view uses its arrays)."""

    def setUp(self):
        super().setUp()
        if connection.vendor != 'postgresql':
            self.skipTest('the client_master view needs PostgreSQL')
        call_command('client_master_view', stdout=StringIO())
        self.addCleanup(lambda: call_command('client_master_view', '--drop', stdout=StringIO()))

    def rows(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT client_id, client_name, is_active, autotask_company_ids, ncentral_org_unit_ids, sentinelone_site_ids, cove_customer_ids FROM client_master ORDER BY client_name')
            return cursor.fetchall()

    def test_one_row_per_client_with_every_reference_in_arrays(self):
        acme = Client.objects.create(name='Acme')
        for source, external in (('autotask', '101'), ('ncentral', '10'), ('ncentral', '20'), ('sentinelone', 'S-9'), ('cove', '4')):
            ClientSourceMap.objects.create(client=acme, source=source, external_id=external)
        Client.objects.create(name='Empty', is_active=False)
        rows = {r[1]: r for r in self.rows()}
        self.assertEqual(rows['Acme'][2:], (True, [101], [10, 20], ['S-9'], [4]))
        self.assertEqual(rows['Empty'][2:], (False, [], [], [], []))         # a client with no links still has a row, with empty arrays

    def test_a_link_with_an_id_that_is_not_a_number_does_not_break_the_view(self):
        acme = Client.objects.create(name='Acme')
        ClientSourceMap.objects.create(client=acme, source='autotask', external_id='not-a-number')
        ClientSourceMap.objects.create(client=acme, source='autotask', external_id='102')
        self.assertEqual(self.rows()[0][3], [102])

    def test_a_query_can_join_to_it_with_any(self):
        acme = Client.objects.create(name='Acme')
        ClientSourceMap.objects.create(client=acme, source='ncentral', external_id='10')
        with connection.cursor() as cursor:
            cursor.execute("SELECT m.client_name FROM (VALUES (10), (20)) AS d(org_unit_id) JOIN client_master m ON d.org_unit_id = ANY (m.ncentral_org_unit_ids)")
            self.assertEqual(cursor.fetchall(), [('Acme',)])

    def test_the_command_can_print_its_sql(self):
        out = StringIO()
        call_command('client_master_view', '--sql', stdout=out)
        self.assertIn('CREATE OR REPLACE VIEW client_master', out.getvalue())


class WeightsApi(StaffClient, CacheClearMixin, TestCase):
    def put(self, user, weights, scope='user'):
        return self.call('put', '/api/weights', user, {'scope': scope, 'weights': weights})

    def test_user_weights_override_the_shared_set(self):
        self.assertEqual(self.put(self.staff, {'third-party': 2, 'windows-security': 3}, 'shared').status_code, 200)
        body = self.put(self.plain, {'third-party': 0}).json()
        self.assertEqual(body['shared'], {'third-party': 2.0, 'windows-security': 3.0})
        self.assertEqual(body['user'], {'third-party': 0.0})
        self.assertEqual(body['effective'], {'third-party': 0.0, 'windows-security': 3.0})
        self.assertEqual(self.call('get', '/api/weights', self.staff).json()['effective'], body['shared'])  # not affected by another user

    def test_only_staff_can_change_the_shared_set(self):
        self.assertEqual(self.put(self.plain, {'third-party': 2}, 'shared').status_code, 403)
        self.assertEqual(ScoreWeight.objects.count(), 0)

    def test_put_replaces_the_set_and_an_empty_object_clears_it(self):
        self.put(self.plain, {'third-party': 2})
        self.put(self.plain, {'windows-security': 4})
        self.assertEqual(self.call('get', '/api/weights', self.plain).json()['user'], {'windows-security': 4.0})
        self.put(self.plain, {})
        self.assertEqual(ScoreWeight.objects.count(), 0)

    def test_invalid_weights_and_scope_are_rejected(self):
        self.assertEqual(self.put(self.plain, {'third-party': -1}).status_code, 400)
        self.assertEqual(self.put(self.plain, {'Bad Key': 1}).status_code, 400)
        self.assertEqual(self.call('put', '/api/weights', self.plain, {'scope': 'everyone', 'weights': {}}).status_code, 400)


class SnapshotTests(StaffClient, CacheClearMixin, ReportsFakeMixin, TestCase):
    RUN = {'run_id': 6, 'finished_at': at(3, aware=False)}

    def trend(self, query=''):
        return self.call('get', f'/api/sections/patch-trend{query}', self.plain).json()

    def test_a_run_is_saved_for_all_devices_and_for_each_linked_client(self):
        acme = Client.objects.create(name='Acme')
        ClientSourceMap.objects.create(client=acme, source='ncentral', external_id='10')
        self.assertEqual(snapshots.capture_run(self.RUN), 2)
        everyone = PatchSnapshot.objects.get(run_id=6, client=None)
        self.assertEqual((everyone.scores['overall'], everyone.scores['windows-security'], everyone.source), (77.5, 80.0, 'live'))
        self.assertEqual(len(everyone.components), 2)
        self.assertTrue(PatchSnapshot.objects.filter(run_id=6, client=acme).exists())
        self.assertEqual(snapshots.capture_run(self.RUN), 2)          # saving the same run again updates, never duplicates
        self.assertEqual(PatchSnapshot.objects.count(), 2)

    def test_the_shared_weights_used_are_saved_with_the_snapshot(self):
        ScoreWeight.objects.create(user=None, key='windows-security', weight=3)
        snapshots.capture_run(self.RUN)
        snapshot = PatchSnapshot.objects.get(client=None)
        self.assertEqual(snapshot.weights, {'windows-security': 3.0})
        self.assertEqual(snapshot.scores['overall'], 78.75)

    def test_nothing_is_saved_when_there_is_nothing_to_score(self):
        self.reports.patch_groups = ([], [])
        self.assertEqual(snapshots.capture_run(self.RUN), 0)
        self.assertEqual(PatchSnapshot.objects.count(), 0)

    def test_backfill_only_adds_runs_that_have_no_snapshot(self):
        self.assertEqual(snapshots.backfill(), 2)          # runs 5 and 6, all devices
        self.assertEqual(set(PatchSnapshot.objects.values_list('source', flat=True)), {'backfill'})
        self.assertEqual(snapshots.backfill(), 0)
        self.assertEqual(snapshots.capture_latest(), 1)    # the newest run again, now as a live snapshot

    def test_trend_comes_from_saved_snapshots_and_is_empty_otherwise(self):
        empty = self.trend()
        self.assertEqual((empty['x'], empty['y']), ([], []))
        acme = Client.objects.create(name='Acme')
        ClientSourceMap.objects.create(client=acme, source='ncentral', external_id='10')
        snapshots.capture_run(self.RUN)
        overall = self.trend('?scope=overall')
        self.assertEqual((overall['x'], overall['y'], overall['heading']), (['2026-10-03'], [77.5], 'Overall Score Trend'))
        self.assertEqual(self.trend('?scope=windows-security')['y'], [80.0])
        self.assertEqual(self.trend(f'?client={acme.pk}')['y'], [77.5])
        self.assertEqual(self.trend('?scope=windows-patching')['y'], [])   # that type had nothing to score



@override_settings(CLIENT_ACCESS_ENFORCED=True)
class ClientGroupsApi(StaffClient, CacheClearMixin, TestCase):
    """Client groups: a named list of clients, or all of a company's. Alpha has clients Acme and Apex, Beta has Zed. `top` is the platform owner,
    `admin_a` and `admin_b` administer Alpha and Beta, `member_a` works for Alpha. A group belongs to one company and holds only its clients."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.alpha, self.beta = Company.objects.create(name='Alpha'), Company.objects.create(name='Beta')
        self.owner = User.objects.create_user('top', password='x', is_staff=True, is_superuser=True)
        self.admin_a = User.objects.create_user('admin_a', password='x', is_staff=True)
        self.admin_b = User.objects.create_user('admin_b', password='x', is_staff=True)
        self.member_a = User.objects.create_user('member_a', password='x')
        for user, company in ((self.admin_a, self.alpha), (self.member_a, self.alpha), (self.admin_b, self.beta)):
            CompanyMember.objects.create(user=user, company=company)
        self.a1 = Client.objects.create(name='Acme', company=self.alpha)
        self.a2 = Client.objects.create(name='Apex', company=self.alpha)
        self.b1 = Client.objects.create(name='Zed', company=self.beta)

    def fake(self):
        return FakeReports(resolver=Resolver({}, {'acme': self.a1.pk, 'zed': self.b1.pk}), org_units={self.a1.pk: [10, 20], self.b1.pk: []})

    def servers(self, user):
        with self.fake():
            return [s['computer_name'] for s in self.call('get', '/api/sections/server-health', user).json()['servers']]

    def make(self, user, **body):
        return self.call('post', '/api/client-groups', user, {'name': 'Retail', **body})

    def group(self, company, name='Retail', clients=(), everyone=False, members=()):
        group = ClientGroup.objects.create(company=company, name=name, all_clients=everyone)
        group.clients.set(clients)
        group.members.set(members)
        return group

    # ---- making and changing groups
    def test_an_administrator_makes_a_group_of_their_own_companys_clients(self):
        made = self.make(self.admin_a, clients=[self.a2.pk, self.a1.pk])
        self.assertEqual(made.status_code, 201)
        body = made.json()
        self.assertEqual((body['name'], body['all_clients'], body['clients'], body['company']['name']), ('Retail', False, sorted([self.a1.pk, self.a2.pk]), 'Alpha'))

    def test_a_client_of_another_company_is_refused(self):
        refused = self.make(self.admin_a, clients=[self.a1.pk, self.b1.pk])
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['error'], f'Unknown client ID: {self.b1.pk}')
        self.assertFalse(ClientGroup.objects.exists())

    def test_a_name_is_unique_within_a_company_but_free_in_another(self):
        self.assertEqual(self.make(self.admin_a).status_code, 201)
        again = self.make(self.admin_a, name='retail')
        self.assertEqual((again.status_code, again.json()['error']), (400, 'There is already a group with that name'))
        self.assertEqual(self.make(self.admin_b).status_code, 201)

    def test_an_all_clients_group_holds_no_list_of_its_own(self):
        made = self.make(self.admin_a, all_clients=True, clients=[self.a1.pk]).json()
        self.assertEqual((made['all_clients'], made['clients']), (True, []))

    def test_the_platform_owner_must_say_which_company(self):
        self.assertEqual(self.make(self.owner).json()['error'], 'Choose a company')
        made = self.make(self.owner, company=self.beta.pk, clients=[self.b1.pk])
        self.assertEqual((made.status_code, made.json()['company']['name']), (201, 'Beta'))

    def test_an_administrator_in_no_company_cannot_make_one(self):
        self.assertEqual(self.make(self.staff).json()['error'], 'You are not part of a company yet')

    def test_an_ordinary_person_cannot_use_group_endpoints(self):
        self.assertEqual(self.call('get', '/api/client-groups', self.member_a).status_code, 403)
        self.assertEqual(self.make(self.member_a).status_code, 403)

    def test_each_company_sees_only_its_own_groups_and_the_owner_sees_all(self):
        self.group(self.alpha, 'Alpha group')
        self.group(self.beta, 'Beta group')
        names = lambda user: sorted(g['name'] for g in self.call('get', '/api/client-groups', user).json())
        self.assertEqual(names(self.admin_a), ['Alpha group'])
        self.assertEqual(names(self.admin_b), ['Beta group'])
        self.assertEqual(names(self.owner), ['Alpha group', 'Beta group'])

    def test_one_company_cannot_change_or_delete_the_others_group(self):
        theirs = self.group(self.beta, 'Beta group', clients=[self.b1])
        url = f'/api/client-groups/{theirs.pk}'
        self.assertEqual(self.call('put', url, self.admin_a, {'name': 'Mine now', 'clients': []}).status_code, 404)
        self.assertEqual(self.call('delete', url, self.admin_a).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual((theirs.name, list(theirs.clients.all())), ('Beta group', [self.b1]))

    def test_a_group_can_be_changed_and_deleted(self):
        mine = self.group(self.alpha, 'Retail', clients=[self.a1], members=[self.member_a])
        url = f'/api/client-groups/{mine.pk}'
        changed = self.call('put', url, self.admin_a, {'name': 'Retail and more', 'clients': [self.a2.pk]})
        self.assertEqual((changed.status_code, changed.json()['name'], changed.json()['clients'], changed.json()['members']), (200, 'Retail and more', [self.a2.pk], ['member_a']))
        self.assertEqual(self.call('delete', url, self.admin_a).status_code, 200)
        self.assertFalse(ClientGroup.objects.filter(pk=mine.pk).exists())

    def test_a_group_cannot_be_renamed_to_the_name_of_another_in_the_company_but_can_keep_its_own(self):
        self.group(self.alpha, 'Taken')
        mine = self.group(self.alpha, 'Mine')
        url = f'/api/client-groups/{mine.pk}'
        self.assertEqual(self.call('put', url, self.admin_a, {'name': 'taken'}).status_code, 400)
        self.assertEqual(self.call('put', url, self.admin_a, {'name': 'Mine', 'clients': [self.a1.pk]}).status_code, 200)

    # ---- what a group gives a person
    def test_a_person_in_a_group_sees_the_clients_of_that_group_and_no_others(self):
        self.assertEqual(self.servers(self.member_a), [])                                         # given nothing, sees nothing
        self.group(self.alpha, 'Acme only', clients=[self.a1], members=[self.member_a])
        self.assertEqual(self.servers(self.member_a), ['SRV-A', 'SRV-B'])                          # Acme's two servers, not Zed's PC-1

    def test_an_all_clients_group_gives_every_client_of_the_company_but_never_another_companys(self):
        self.group(self.alpha, 'Everyone', everyone=True, members=[self.member_a])
        ids = access_lib.allowed_client_ids(self.member_a)
        self.assertEqual(set(ids), {self.a1.pk, self.a2.pk})
        self.assertNotIn(self.b1.pk, ids)

    def test_a_client_made_later_is_in_an_all_clients_group_at_once(self):
        self.group(self.alpha, 'Everyone', everyone=True, members=[self.member_a])
        later = Client.objects.create(name='Later', company=self.alpha)
        self.assertIn(later.pk, access_lib.allowed_client_ids(self.member_a))

    def test_groups_are_added_together_and_with_clients_given_one_by_one(self):
        self.group(self.alpha, 'One', clients=[self.a1], members=[self.member_a])
        ClientAccess.objects.create(user=self.member_a, client=self.a2)
        self.assertEqual(set(access_lib.allowed_client_ids(self.member_a)), {self.a1.pk, self.a2.pk})

    def test_a_group_of_another_company_gives_nothing_even_if_someone_is_put_in_it(self):
        foreign = self.group(self.beta, 'Foreign', clients=[self.b1], members=[self.member_a])
        self.assertEqual(set(access_lib.allowed_client_ids(self.member_a)), set())
        foreign.members.clear()

    def test_taking_a_person_out_of_a_group_takes_its_clients_away(self):
        group = self.group(self.alpha, 'One', clients=[self.a1], members=[self.member_a])
        group.members.remove(self.member_a)
        self.assertEqual(set(access_lib.allowed_client_ids(self.member_a)), set())

    def test_an_administrator_still_sees_the_whole_company_whatever_groups_say(self):
        self.assertEqual(set(access_lib.allowed_client_ids(self.admin_a)), {self.a1.pk, self.a2.pk})

    def test_moving_a_person_to_another_company_drops_the_old_companys_groups(self):
        group = self.group(self.alpha, 'One', clients=[self.a1], members=[self.member_a])
        moved = self.call('put', f'/api/users/{self.member_a.username}/company', self.owner, {'company': self.beta.pk})
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(self.member_a.client_groups.count(), 0)
        self.assertFalse(group.members.filter(pk=self.member_a.pk).exists())

    def test_the_access_overview_lists_the_groups_and_who_is_in_them(self):
        group = self.group(self.alpha, 'One', clients=[self.a1], members=[self.member_a])
        body = self.call('get', '/api/access', self.admin_a).json()
        self.assertEqual([g['name'] for g in body['client_groups']], ['One'])
        person = next(u for u in body['users'] if u['username'] == 'member_a')
        self.assertEqual((person['client_groups'], person['is_active'], person['email']), ([group.pk], True, ''))
        self.assertFalse(body['can_make_admin'])
        self.assertTrue(self.call('get', '/api/access', self.owner).json()['can_make_admin'])


@override_settings(CLIENT_ACCESS_ENFORCED=True)
class PeopleApi(StaffClient, CacheClearMixin, TestCase):
    """Making a person from the Access page: no password is chosen for them, they get a one-time link to choose their own. Alpha and Beta are companies;
    `top` is the platform owner, `admin_a` and `admin_b` administer them, and `retail` is a client group of Alpha's."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.alpha, self.beta = Company.objects.create(name='Alpha'), Company.objects.create(name='Beta')
        self.owner = User.objects.create_user('top', password='x', is_staff=True, is_superuser=True)
        self.admin_a = User.objects.create_user('admin_a', password='x', is_staff=True)
        self.admin_b = User.objects.create_user('admin_b', password='x', is_staff=True)
        for user, company in ((self.admin_a, self.alpha), (self.admin_b, self.beta)):
            CompanyMember.objects.create(user=user, company=company)
        self.a1 = Client.objects.create(name='Acme', company=self.alpha)
        self.retail = ClientGroup.objects.create(company=self.alpha, name='Retail')
        self.retail.clients.set([self.a1])
        self.theirs = ClientGroup.objects.create(company=self.beta, name='Theirs')

    def make(self, user=None, **body):
        return self.call('post', '/api/users', user or self.admin_a, {'username': 'thandi', 'email': 'thandi@acme.test', 'first_name': 'Thandi', **body})

    def raw(self, path, body):
        return self.client.post(path, body, content_type='application/json', **H)

    def token_of(self, link):
        return re.search(r'uid=([^&\s]+)&token=(\S+)', link).groups()

    # ---- making a person
    def test_an_administrator_makes_a_person_in_their_own_company_in_the_groups_chosen(self):
        made = self.make(client_groups=[self.retail.pk])
        self.assertEqual(made.status_code, 201)
        user = get_user_model().objects.get(username='thandi')
        self.assertEqual((user.email, user.first_name, user.is_staff, user.is_active), ('thandi@acme.test', 'Thandi', False, True))
        self.assertEqual(CompanyMember.objects.get(user=user).company, self.alpha)
        self.assertEqual(list(user.client_groups.all()), [self.retail])

    def test_no_password_is_chosen_for_them_and_none_can_be_supplied(self):
        self.make(password='chosen-for-them')
        user = get_user_model().objects.get(username='thandi')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.raw('/api/auth/login', {'username': 'thandi', 'password': 'chosen-for-them'}).status_code, 401)

    def test_the_link_in_the_answer_lets_them_choose_a_password_once_and_sign_in(self):
        link = self.make().json()['setup_link']
        uid, token = self.token_of(link)
        self.assertEqual(self.raw('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'a-good-password-1'}).status_code, 200)
        self.assertEqual(self.raw('/api/auth/login', {'username': 'thandi', 'password': 'a-good-password-1'}).status_code, 200)
        self.assertEqual(self.raw('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'another-password-2'}).status_code, 400)   # used once

    def test_the_link_is_emailed_to_them_and_the_answer_says_so(self):
        body = self.make().json()
        self.assertEqual((body['sent'], body['valid_for_minutes'], body['email']), ('sent', 60, 'thandi@acme.test'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['thandi@acme.test'])
        self.assertIn(body['setup_link'], mail.outbox[0].body)

    def test_if_the_email_cannot_be_sent_the_person_is_still_made_and_the_link_is_still_returned(self):
        with patch('api.views.send_mail', side_effect=OSError('smtp is down')), self.assertLogs('api.views', level='ERROR'):
            made = self.make()
        self.assertEqual(made.status_code, 201)
        self.assertEqual(made.json()['sent'], 'failed')
        self.assertIn('/reset?uid=', made.json()['setup_link'])
        self.assertTrue(get_user_model().objects.filter(username='thandi').exists())

    def test_a_username_that_is_taken_is_refused_whatever_its_case(self):
        self.assertEqual(self.make().status_code, 201)
        again = self.make(username='THANDI')
        self.assertEqual((again.status_code, again.json()['error']), (409, 'That username is taken'))
        self.assertEqual(get_user_model().objects.filter(username__iexact='thandi').count(), 1)

    def test_the_email_is_required(self):
        refused = self.make(email='')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('Email is required', refused.json()['error'])
        self.assertFalse(get_user_model().objects.filter(username='thandi').exists())

    def test_a_group_of_another_company_is_refused(self):
        refused = self.make(client_groups=[self.theirs.pk])
        self.assertEqual((refused.status_code, refused.json()['error']), (400, f'Unknown client group: {self.theirs.pk}'))
        self.assertFalse(get_user_model().objects.filter(username='thandi').exists())

    def test_only_the_platform_owner_can_make_an_administrator(self):
        refused = self.make(is_staff=True)
        self.assertEqual((refused.status_code, refused.json()['error']), (400, 'Only the platform owner can make an administrator'))
        self.assertFalse(get_user_model().objects.filter(username='thandi').exists())
        self.assertEqual(self.make(self.owner, company=self.alpha.pk, is_staff=True).status_code, 201)
        self.assertTrue(get_user_model().objects.get(username='thandi').is_staff)

    def test_the_platform_owner_must_say_which_company(self):
        self.assertEqual(self.make(self.owner).json()['error'], 'Choose a company')
        self.assertEqual(self.make(self.owner, company=self.beta.pk).status_code, 201)
        self.assertEqual(CompanyMember.objects.get(user__username='thandi').company, self.beta)

    def test_an_ordinary_person_cannot_make_people(self):
        User = get_user_model()
        member = User.objects.create_user('member', password='x')
        CompanyMember.objects.create(user=member, company=self.alpha)
        self.assertEqual(self.make(member).status_code, 403)

    # ---- changing and deactivating
    def person(self, username='pat', company=None, password='x'):
        user = get_user_model().objects.create_user(username, f'{username}@acme.test', password)
        CompanyMember.objects.create(user=user, company=company or self.alpha)
        return user

    def test_a_person_can_be_renamed_given_a_new_email_and_put_in_groups(self):
        user = self.person()
        done = self.call('put', '/api/users/pat', self.admin_a, {'first_name': 'Pat', 'email': 'pat@new.test', 'client_groups': [self.retail.pk]})
        self.assertEqual(done.status_code, 200)
        user.refresh_from_db()
        self.assertEqual((user.first_name, user.email, list(user.client_groups.all())), ('Pat', 'pat@new.test', [self.retail]))
        self.assertEqual(self.call('put', '/api/users/pat', self.admin_a, {'client_groups': []}).status_code, 200)
        self.assertEqual(user.client_groups.count(), 0)

    def test_someone_in_another_company_looks_the_same_as_no_one(self):
        self.person('bea', company=self.beta)
        for method, path in (('put', '/api/users/bea'), ('post', '/api/users/bea/link')):
            self.assertEqual(self.call(method, path, self.admin_a, {'first_name': 'X'}).status_code, 404, path)
        self.assertEqual(self.call('put', '/api/users/ghost', self.admin_a, {'first_name': 'X'}).status_code, 404)

    def test_a_deactivated_person_cannot_sign_in_and_can_be_brought_back(self):
        self.person('pat', password='a-good-password-1')
        self.assertEqual(self.raw('/api/auth/login', {'username': 'pat', 'password': 'a-good-password-1'}).status_code, 200)
        self.assertEqual(self.call('put', '/api/users/pat', self.admin_a, {'is_active': False}).status_code, 200)
        self.assertEqual(self.raw('/api/auth/login', {'username': 'pat', 'password': 'a-good-password-1'}).status_code, 401)
        self.assertEqual(self.call('put', '/api/users/pat', self.admin_a, {'is_active': True}).status_code, 200)
        self.assertEqual(self.raw('/api/auth/login', {'username': 'pat', 'password': 'a-good-password-1'}).status_code, 200)

    def test_deactivating_signs_them_out_everywhere_at_once(self):
        user = self.person('pat', password='a-good-password-1')
        self.raw('/api/auth/login', {'username': 'pat', 'password': 'a-good-password-1'})
        self.assertTrue(RefreshToken.objects.filter(user=user, revoked=False).exists())
        self.call('put', '/api/users/pat', self.admin_a, {'is_active': False})
        self.assertFalse(RefreshToken.objects.filter(user=user, revoked=False).exists())

    def test_nobody_can_deactivate_themselves(self):
        refused = self.call('put', '/api/users/admin_a', self.admin_a, {'is_active': False})
        self.assertEqual((refused.status_code, refused.json()['error']), (400, 'You cannot deactivate yourself'))
        self.admin_a.refresh_from_db()
        self.assertTrue(self.admin_a.is_active)

    def test_only_the_platform_owner_changes_who_is_an_administrator_or_touches_the_owner(self):
        self.person()
        self.assertEqual(self.call('put', '/api/users/pat', self.admin_a, {'is_staff': True}).status_code, 403)
        self.assertEqual(self.call('put', '/api/users/pat', self.owner, {'is_staff': True}).status_code, 200)
        CompanyMember.objects.create(user=self.owner, company=self.alpha)
        self.assertEqual(self.call('put', '/api/users/top', self.admin_a, {'first_name': 'X'}).status_code, 403)

    # ---- a fresh link
    def test_a_fresh_link_can_be_made_for_someone_whose_first_expired(self):
        self.person()
        fresh = self.call('post', '/api/users/pat/link', self.admin_a)
        self.assertEqual(fresh.status_code, 200)
        uid, token = self.token_of(fresh.json()['setup_link'])
        self.assertEqual(self.raw('/api/auth/reset', {'uid': uid, 'token': token, 'password': 'a-good-password-1'}).status_code, 200)

    def test_no_link_is_made_for_a_deactivated_person(self):
        user = self.person()
        user.is_active = False
        user.save()
        refused = self.call('post', '/api/users/pat/link', self.admin_a)
        self.assertEqual((refused.status_code, refused.json()['error']), (400, 'This person is deactivated; bring them back first'))

    def test_a_person_with_no_email_gets_a_link_to_hand_over_and_nothing_is_sent(self):
        user = self.person()
        user.email = ''
        user.save()
        body = self.call('post', '/api/users/pat/link', self.admin_a).json()
        self.assertEqual((body['sent'], len(mail.outbox)), ('none', 0))
        self.assertIn('/reset?uid=', body['setup_link'])
