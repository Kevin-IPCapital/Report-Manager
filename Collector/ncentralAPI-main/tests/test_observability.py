import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
import types
# Offline CI environment may lack PostgreSQL native client; mock import only.
try:
    import psycopg2
except ImportError:
    db = types.ModuleType('psycopg2')
    db.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('No live DB in offline tests'))
    extras = types.ModuleType('psycopg2.extras')
    extras.execute_values = lambda *_args, **_kwargs: None
    extras.Json = lambda value: value
    db.extras = extras
    sys.modules['psycopg2'] = db
    sys.modules['psycopg2.extras'] = extras
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ncentral_observability as subject
import ncentral_client as nc


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.sql = ''
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args=None):
        self.sql = sql
        self.db.statements.append((sql, args))
    def fetchone(self):
        if 'pg_try_advisory_lock' in self.sql: return (True,)
        return (17,)


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
    def cursor(self): return FakeCursor(self)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


class Tests(unittest.TestCase):
    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            subject.run(mode='patches')
    def test_invalid_delay(self):
        with self.assertRaises(ValueError):
            subject.run(mode='monitoring', delay_seconds=-1)
    def test_logger_creates_appends_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            logfile = Path(tmp) / 'nested' / 'run.log'
            for i in range(2):
                with patch.object(subject.nc, 'get_customers', return_value=[{'orgUnitId': 5, 'orgUnitName': 'Test'}]), patch.object(subject, '_collect_customer', return_value=({'devices': 2, 'checks': 3, 'events': 1, 'unknown': 0}, [])):
                    out = subject.run(mode='monitoring', dry_run=True, log_file=logfile)
                    self.assertEqual(out['status'], 'SUCCESS')
                self.assertEqual(logging.getLogger('ncentral.observability').handlers, [])
            content = logfile.read_text()
            self.assertEqual(content.count(' INFO SUCCESS mode=monitoring'), 2)
            self.assertIn('duration_seconds=', content)
    def test_dry_run_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.nc, 'get_customers', return_value=[{'orgUnitId': 5, 'orgUnitName': 'Test'}]), patch.object(subject, '_collect_customer', return_value=({'devices': 2, 'checks': 3, 'events': 0, 'unknown': 1}, [])):
            out = subject.run(mode='monitoring', dry_run=True, log_file=Path(tmp)/'l', connect=lambda: self.fail('Should not connect'))
            self.assertEqual(out['devices'], 2)
    def test_live_persists_run_even_when_empty(self):
        conn = FakeConnection()
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.monitoring, 'active_org_units', return_value=[(5, 'Test')]), patch.object(subject, '_collect_customer', return_value=({'devices': 0, 'checks': 0, 'events': 0, 'unknown': 0}, [])):
            out = subject.run(mode='monitoring', dry_run=False, log_file=Path(tmp)/'l', connect=lambda: conn)
        self.assertEqual(out['status'], 'SUCCESS')
        self.assertTrue(conn.closed)
        self.assertTrue(any('INSERT INTO ncentral.poll_run' in sql for sql, _ in conn.statements))
        self.assertTrue(any('UPDATE ncentral.poll_run' in sql for sql, _ in conn.statements))
    def test_failure_rolled_back_and_recorded(self):
        conn = FakeConnection()
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.monitoring, 'active_org_units', return_value=[(5, 'Test')]), patch.object(subject, '_collect_customer', side_effect=RuntimeError('Upstream down')):
            with self.assertRaisesRegex(RuntimeError, 'Upstream down'):
                subject.run(mode='monitoring', dry_run=False, log_file=Path(tmp)/'l', connect=lambda: conn)
            self.assertIn('FAILED mode=monitoring', (Path(tmp)/'l').read_text())
        self.assertTrue(conn.closed)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(any('UPDATE ncentral.poll_run' in sql for sql, _ in conn.statements))
    def test_customer_filter(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.nc, 'get_customers', return_value=[{'orgUnitId': 5, 'orgUnitName': 'A'}, {'orgUnitId': 6, 'orgUnitName': 'B'}]), patch.object(subject, '_collect_customer', return_value=({'devices': 1, 'checks': 0, 'events': 0, 'unknown': 0}, [])) as mock:
            out = subject.run(mode='monitoring', dry_run=True, customer_id=6, log_file=Path(tmp)/'l')
            self.assertEqual(out['customers'], 1)
            self.assertEqual(mock.call_args.args[1], 6)
    def test_partial_customer_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.nc, 'get_customers', return_value=[{'orgUnitId': 5, 'orgUnitName': 'A'}]), patch.object(subject, '_collect_customer', return_value=({'devices': 1, 'checks': 0, 'events': 0, 'unknown': 1}, ['404 device unavailable'])):
            out = subject.run(mode='monitoring', dry_run=True, log_file=Path(tmp)/'l')
            self.assertEqual(out['status'], 'PARTIAL')
    def test_metrics_mode_records_counts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(subject.nc, 'get_customers', return_value=[{'orgUnitId': 5, 'orgUnitName': 'A'}]), patch.object(subject, '_collect_customer', return_value=({'devices': 4, 'samples': 2, 'new': 0, 'stale': 1, 'missing': 2}, [])):
            out = subject.run(mode='metrics', dry_run=True, log_file=Path(tmp)/'l')
            self.assertEqual(out['samples'], 2)
    def test_monitoring_zero_devices_does_not_call_write(self):
        with patch.object(subject.monitoring, '_gather', return_value=([], [], [], [], [])), patch.object(subject.monitoring, '_write') as write:
            counts, errors = subject._collect_customer('monitoring', 5, 'A', subject.datetime.now(), object(), 9)
            self.assertEqual(counts['devices'], 0)
            write.assert_not_called()
    def test_monitoring_client_rate_limit_propagates(self):
        with patch.object(subject.monitoring.nc, 'get_devices_for_org_unit', return_value=[{'deviceId': 9}]), patch.object(subject.monitoring.nc, 'get_active_issues', return_value=[]), patch.object(subject.monitoring.nc, 'get_service_monitor_status', side_effect=nc.NCentralError('rate limited', status=429)):
            with self.assertRaises(nc.NCentralError):
                subject.monitoring._gather(5, 'A', subject.datetime.now())
    def test_metrics_client_server_error_propagates(self):
        with patch.object(subject.metrics.nc, 'get_devices_for_org_unit', return_value=[{'deviceId': 9}]), patch.object(subject.metrics, '_property_name', return_value='IPC Perf Sample'), patch.object(subject.metrics.nc, 'get_device_custom_properties', side_effect=nc.NCentralError('unavailable', status=503)):
            with self.assertRaises(nc.NCentralError):
                subject.metrics._gather(5, 'A', subject.datetime.now())
    def test_metrics_missing_property_is_not_exception(self):
        with patch.object(subject.metrics.nc, 'get_devices_for_org_unit', return_value=[{'deviceId': 9, 'longName': 'Host'}]), patch.object(subject.metrics, '_property_name', return_value='IPC Perf Sample'), patch.object(subject.metrics.nc, 'get_device_custom_properties', return_value=[]):
            rows, stats, errors = subject.metrics._gather(5, 'A', subject.datetime.now())
            self.assertEqual(stats['missing'], 1)
            self.assertEqual(rows, [])
            self.assertEqual(errors, [])

if __name__ == '__main__': unittest.main()
