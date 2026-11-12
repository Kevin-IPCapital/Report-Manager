"""A fake reporting database for tests: `FakeReports` replaces `api.data.common.query`, so no PostgreSQL,
no source schemas and no network are needed. It also records every query it receives."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from api.clients import Resolver

UTC = timezone.utc


def at(day, hour=8, minute=0, aware=True):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC if aware else None)


def ticket(**overrides):
    row = dict(snapshot_id=7, ticket_id=1, ticket_number='T1', company_id=101, company_name='Acme', title='VPN down',
               description='d', resolution=None, ticket_type='Incident', priority='High', issue_type='Network',
               sub_issue_type='VPN', status='In Progress', sla_met=None, first_response_met=None,
               first_response_at=None, first_response_due_at=None, created_at=at(5), completed_at=None)
    row.update(overrides)
    return row


def default_tickets():
    return [
        # Responded 30 minutes after the due time: the only red row.
        ticket(ticket_id=1, ticket_number='T1', first_response_due_at=at(5, 10), first_response_at=at(5, 10, 30)),
        # Responded on time and completed.
        ticket(ticket_id=2, ticket_number='T2', priority='Critical', status='Complete', sla_met=True, first_response_met=True,
               first_response_due_at=at(6, 9), first_response_at=at(6, 8, 20), created_at=at(6), completed_at=at(6, 12)),
        # NULL classification, no response yet, and a timestamp stored without a time zone.
        ticket(ticket_id=3, ticket_number='T3', priority=None, company_name=None, created_at=at(7, 9, aware=False)),
    ]


def default_patch_groups():
    latest = [
        {'patch_type': 'windows-security', 'category_id': 'g1', 'category': 'Security Updates', 'total': 10, 'missing': 2},
        {'patch_type': 'third-party', 'category_id': None, 'category': None, 'total': 4, 'missing': 1},
    ]
    previous = [{'patch_type': 'windows-security', 'category_id': 'g1', 'category': 'Security Updates', 'total': 10, 'missing': 4}]
    return latest, previous


def default_entries():
    base = dict(run_id=5, category='Security Updates', category_id='g1', result='Installed', is_security=True,
                is_third_party=False, kb='KB5000001')
    return [
        dict(base, device_id=11, device_name='SRV-11', title='Cumulative update', is_missing=True, installed_at=None),
        dict(base, device_id=12, device_name=None, title='Servicing stack', is_missing=False, installed_at=at(3, aware=False)),
        dict(base, device_id=13, device_name='SRV-13', title='Unknown state', is_missing=None, installed_at=None, result=None),
    ]


def default_servers():
    now = datetime.now(UTC)
    base = dict(customer_name='Acme', site_id='S1', machine_type='server', os_type='windows', agent_version='23.4', mitigation_mode='protect',
                is_up_to_date=True, infected=False, is_active=True, is_decommissioned=False, network_status='connected')
    return [
        dict(base, agent_id='a2', computer_name='SRV-B', captured_at=now - timedelta(hours=2)),
        dict(base, agent_id='a1', computer_name='SRV-A', captured_at=now - timedelta(hours=1), is_active=False),
        dict(base, agent_id='a3', customer_name='Zed', site_id=None, computer_name='PC-1', captured_at=now - timedelta(minutes=5), infected=True),
    ]


class FakeReports:
    """Context manager. Answers each known query shape with fixture rows; anything else fails the test."""

    def __init__(self, tickets=None, patch_groups=None, entries=None, servers=None, resolver=None, org_units=None, weights=None, shared=None, runs=None, device_customers=None, technician_columns=None, resources=None,
                 cove_tables=None, cove_devices=None, cove_flags=None, cove_customers=None, cove_detail=None, org_groups=None, server_patching=None, server_patch_error=False):
        self.tickets = default_tickets() if tickets is None else tickets
        self.patch_groups = default_patch_groups() if patch_groups is None else patch_groups
        self.entries = default_entries() if entries is None else entries
        self.servers = default_servers() if servers is None else servers
        # Client 1 is "Acme" (Autotask company 101, SentinelOne site S1, N-central org units 10 and 20); client 2 is "Zed".
        self.resolver = resolver or Resolver({('autotask', '101'): 1, ('sentinelone', 'S1'): 1}, {'acme': 1, 'zed': 2})
        self.org_units = {1: [10, 20], 2: []} if org_units is None else org_units
        self.weights = {} if weights is None else weights      # the viewer's own
        self.shared = {} if shared is None else shared         # the shared defaults, used only when a test passes `shared`
        self._shared_given = shared is not None
        self.device_customers = [{'org_unit_id': 10, 'customer_name': 'Acme Logistics', 'devices': 5},
                                 {'org_unit_id': 20, 'customer_name': 'Acme Logistics (Cape Town)', 'devices': 3},
                                 {'org_unit_id': 30, 'customer_name': 'Blue Crane', 'devices': 4}] if device_customers is None else device_customers
        self.runs = [{'run_id': 5, 'finished_at': at(2, aware=False)}, {'run_id': 6, 'finished_at': at(3, aware=False)}] if runs is None else runs
        self.technician_columns = [] if technician_columns is None else technician_columns   # rows from information_schema
        self.resources = [] if resources is None else resources
        self.server_patching = default_server_patching() if server_patching is None else server_patching   # (devices, installed, backlog)
        self.server_patch_error = server_patch_error   # makes the patching queries fail, to show the page still loads
        self.org_groups = default_org_groups() if org_groups is None else org_groups   # (latest, previous): rows with org_unit_id
        self.cove_tables = list(COVE_TABLES) if cove_tables is None else cove_tables
        self.cove_devices = default_cove_devices() if cove_devices is None else cove_devices
        self.cove_flags = default_cove_flags() if cove_flags is None else cove_flags
        self.cove_customers = [{'id': 4, 'customer_name': 'Acme'}] if cove_customers is None else cove_customers
        self.cove_detail = default_cove_detail() if cove_detail is None else cove_detail
        self.calls = []

    def __call__(self, sql, params=()):
        self.calls.append((sql, list(params)))
        for marker, position in (('/* server-patch-devices */', 0), ('/* server-patch-installed */', 1), ('/* server-patch-backlog */', 2), ('/* server-patch-latest */', 3), ('/* server-patch-good */', 4)):   # first: nothing else may swallow them
            if marker in sql:
                if self.server_patch_error:
                    raise RuntimeError('relation "ncentral.patch_backlog" does not exist')
                return [dict(r) for r in self.server_patching[position]]
        if "FROM information_schema.tables WHERE table_schema = 'cove'" in sql:
            return [{'table_name': t} for t in self.cove_tables]
        if 'FROM cove.customers' in sql:
            return [dict(r) for r in self.cove_customers]
        if 'SELECT DISTINCT ON (d.device_id)' in sql:
            return [dict(r) for r in self.cove_devices]
        if 'FROM cove.device d' in sql and 'WHERE d.device_id = %s' in sql:
            return [dict(r) for r in self.cove_devices if r['device_id'] == params[0]][:1]
        if 'FROM cove.red_flag r JOIN latest_snapshots' in sql:
            return [dict(r) for r in self.cove_flags]
        if 'FROM cove.red_flag WHERE' in sql:
            return [dict(r) for r in self.cove_detail['flags']]
        if 'FROM cove.datasource_session WHERE' in sql:
            return [dict(r) for r in self.cove_detail['datasources']]
        if 'FROM cove.m365_source WHERE' in sql:
            return [dict(r) for r in self.cove_detail['m365']]
        if 'FROM information_schema.columns' in sql:
            return [dict(r) for r in self.technician_columns]
        if 'FROM autotask.resource' in sql:
            return [dict(r) for r in self.resources]
        if 'DISTINCT ON (ticket_id)' in sql:
            return [dict(r) for r in self.tickets]
        if 'GROUP BY 1, 2, 3, 4' in sql:   # per customer: checked first, because the plain query's text is the start of this one
            latest, previous = self.org_groups
            return [dict(r) for r in (previous if 'inventory_rank = 2' in sql else latest)]
        if 'GROUP BY 1, 2, 3' in sql:
            latest, previous = self.patch_groups
            return [dict(r) for r in (previous if 'inventory_rank = 2' in sql else latest)]
        if 'FROM ncentral.device WHERE org_unit_id IS NOT NULL' in sql:
            return [dict(r) for r in self.device_customers]
        if 'FROM ncentral.patch_run WHERE finished_at IS NOT NULL ORDER BY run_id' in sql:
            return [dict(r) for r in self.runs]
        if 'FROM named WHERE' in sql:
            return [dict(r) for r in self.entries]
        if 'sentinelone.agent' in sql:
            return [dict(r) for r in self.servers]
        raise AssertionError(f'Unexpected report query: {sql[:120]}')

    def __enter__(self):
        self._patchers = [
            patch('api.data.common.query', self),
            patch('api.clients.load_resolver', lambda: self.resolver),
            patch('api.clients.ncentral_org_map', lambda: {'10': (1, 'Acme'), '20': (1, 'Acme')}),
            patch('api.clients.org_units_for', lambda client_id: self.org_units.get(int(client_id), [])),
            patch('api.scoring.weights_for', lambda user: dict(self.weights)),
        ]
        if self._shared_given:   # otherwise the real shared weights (the test database) are used, which database tests rely on
            self._patchers.append(patch('api.scoring.shared_weights', lambda: dict(self.shared)))
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patchers:
            p.stop()


class ReportsFakeMixin:
    """Put first in the bases of a Django TestCase: `self.reports` is the active FakeReports."""

    def setUp(self):
        super().setUp()
        self.reports = FakeReports()
        self.reports.__enter__()
        self.addCleanup(self.reports.__exit__)


class CacheClearMixin:
    """Sign-in throttling counts attempts in the cache, so every test starts with an empty one."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()


TECHNICIAN_COLUMNS = [{'table_name': 'ticket', 'column_name': 'assigned_resource_id'}]
RESOURCE_COLUMNS = [{'table_name': 'resource', 'column_name': name} for name in ('resource_id', 'first_name', 'last_name')]


COVE_TABLES = ('customers', 'snapshot', 'device', 'datasource_session', 'm365_source', 'red_flag')


def cove_device(**overrides):
    row = dict(snapshot_id=20, device_id=1, device_name='SRV-FILE', device_alias=None, customer='Acme', contact_email='ops@acme.test',
               hostname='srv-file', os='Windows Server 2022', os_type='Server', account_type='Endpoint/Server', client_version='24.1',
               client_old=False, os_eol=None, is_server=True, is_m365=False, used_storage_bytes=3 * 1024 ** 3, dedup_efficiency=40,
               storage_location='Cove Cloud', strict_location_policy=False, reporting_enabled=True, reporting_frequency=7,
               dr_testing='Not Configured', immutable_enabled=True, fips_enabled=False, passphrase_set=True, lsv_enabled=True,
               lsv_status='Healthy', cloud_status='Healthy', retention_days=120, last_success=at(9, 3), days_since_success=1,
               health='OK', red_flag_count=0, active_datasources='Files', captured_at=at(10, 6))
    row.update(overrides)
    return row


def default_cove_devices():
    return [
        cove_device(),
        cove_device(device_id=2, device_name='PC-RECEPTION', customer='Zed', is_server=False, os_type='Workstation', health='WARNING',
                    red_flag_count=1, retention_days=45, used_storage_bytes=None, immutable_enabled=None, snapshot_id=21),
        cove_device(device_id=3, device_name=None, hostname='m365-mailboxes', customer='Unmapped Co', is_server=False, is_m365=True,
                    health='CRITICAL', red_flag_count=2, days_since_success=12, last_success=at(1, 3), snapshot_id=22),
    ]


def default_cove_flags():
    return [
        {'snapshot_id': 21, 'device_id': 2, 'category': 'retention', 'flag': 'RETENTION SHORT: 45 days (recommended: 90+)'},
        {'snapshot_id': 22, 'device_id': 3, 'category': 'backup_currency', 'flag': 'NO SUCCESSFUL BACKUP IN 12 DAYS'},
        {'snapshot_id': 22, 'device_id': 3, 'category': None, 'flag': 'Exchange: Failed'},
    ]


def default_cove_detail():
    return {'flags': [{'category': 'retention', 'flag': 'RETENTION SHORT: 45 days (recommended: 90+)'}],
            'datasources': [{'ds_code': 'D01', 'ds_name': 'Files', 'status': 'Completed', 'status_code': '4', 'error_count': 0, 'duration_seconds': 600,
                             'sent_bytes': 1024 ** 3, 'protected_bytes': 3 * 1024 ** 3, 'item_count': 9000, 'retention_days': 45, 'last_success': at(9, 3),
                             'last_session': at(9, 3), 'ok_days': 28, 'failed_days': 0, 'warning_days': 1, 'missed_days': 1, 'max_fail_streak': 0,
                             'recurring_failures': False, 'chronic_warnings': False, 'chronic_missed': False, 'imm_active': True, 'imm_last_status': 'Completed'}],
            'm365': []}


def org_group(org, patch_type, category_id, category, total, missing):
    return {'org_unit_id': org, 'patch_type': patch_type, 'category_id': category_id, 'category': category, 'total': total, 'missing': missing}


def default_org_groups():
    """Per customer, matching the fake devices: org units 10 and 20 belong to client 1 (Acme), 30 is an unlinked customer (Blue Crane)."""
    latest = [org_group(10, 'windows-security', 'g1', 'Security Updates', 50, 10), org_group(20, 'windows-security', 'g1', 'Security Updates', 50, 40),
              org_group(10, 'third-party', 'g9', 'Apps', 20, 5), org_group(30, 'windows-security', 'g1', 'Security Updates', 10, 0),
              org_group(None, 'windows-security', 'g1', 'Security Updates', 99, 99)]
    previous = [org_group(10, 'windows-security', 'g1', 'Security Updates', 50, 20), org_group(20, 'windows-security', 'g1', 'Security Updates', 50, 40),
                org_group(30, 'windows-security', 'g1', 'Security Updates', 10, 5)]
    return latest, previous


def default_server_patching():
    """(devices, installed, backlog, attempts, good) for the default servers, with dates relative to now so what is overdue never changes.
    SRV-A is device 101 (srv-a.acme.local) and SRV-B is device 102, both in Acme's customers; PC-1 (Zed) has no device. The oldest missing patch
    is 20 days for SRV-A's security (overdue), 5 days for its third-party (not), and 30 days for SRV-B's Windows patching (overdue). SRV-A's Windows patching
    is up to date, and SRV-B's third-party has no entries at all."""
    from datetime import datetime, timedelta, timezone
    ago = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)   # noqa: E731   N-central stores no time zone
    devices = [{'device_id': 101, 'device_name': 'srv-a.acme.local', 'org_unit_id': 10, 'last_seen': ago(1)}, {'device_id': 102, 'device_name': 'SRV-B', 'org_unit_id': 20, 'last_seen': ago(2)}]
    installed = [{'device_id': 101, 'patch_type': 'windows-security', 'last_installed_at': ago(3), 'missing_now': 2, 'entries': 9},
                 {'device_id': 101, 'patch_type': 'windows-patching', 'last_installed_at': ago(10), 'missing_now': 0, 'entries': 5},
                 {'device_id': 101, 'patch_type': 'third-party', 'last_installed_at': None, 'missing_now': 1, 'entries': 1},      # nothing installed with a date, one missing
                 {'device_id': 102, 'patch_type': 'windows-security', 'last_installed_at': ago(40), 'missing_now': 0, 'entries': 12},
                 {'device_id': 102, 'patch_type': 'windows-patching', 'last_installed_at': None, 'missing_now': 1, 'entries': 1}]
    backlog = [{'device_id': 101, 'patch_type': 'windows-security', 'missing': 2, 'oldest_missing_at': ago(20)},
               {'device_id': 101, 'patch_type': 'third-party', 'missing': 1, 'oldest_missing_at': ago(5)},
               {'device_id': 102, 'patch_type': 'windows-patching', 'missing': 1, 'oldest_missing_at': ago(30)}]
    # SRV-A's newest patch collection worked. SRV-B's answered 404 (N-central cannot find it), so it is disconnected, and its newest USABLE inventory is older.
    attempts = [{'device_id': 101, 'captured_at': ago(0.5), 'coverage': 'full', 'error': None},
                {'device_id': 102, 'captured_at': ago(0.5), 'coverage': 'unavailable',
                 'error': 'Request failed (404) on /api/devices/102/assets: {"message":"DeviceNotFoundException: Device was not found!"}'}]
    good = [{'device_id': 101, 'captured_at': ago(0.5)}, {'device_id': 102, 'captured_at': ago(3)}]
    return devices, installed, backlog, attempts, good
