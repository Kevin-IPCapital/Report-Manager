"""Offline regression tests. No API, DB or network requests are made."""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# The CI/runtime used to test these sources may not have psycopg2 installed.
# Mock its import; database interactions are independently mocked below.
if 'psycopg2' not in sys.modules:
    try:
        import psycopg2
    except ImportError:
        psycopg2 = types.ModuleType('psycopg2')
        extras = types.ModuleType('psycopg2.extras')
        extras.Json = lambda value: value
        extras.execute_values = MagicMock()
        psycopg2.extras = extras
        sys.modules['psycopg2'] = psycopg2
        sys.modules['psycopg2.extras'] = extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
patch_collector = importlib.import_module('ncentral_patch')


def device(identifier):
    return {'deviceId': identifier, 'longName': f'device-{identifier}',
            'deviceClass': 'Server', 'supportedOs': 'Windows'}


def empty_patch():
    return {'patch_entry_count': 0, 'missing': [], 'installed': []}


class GatherTests(unittest.TestCase):
    def setUp(self):
        self.devices = [device(1), device(2)]
        self.patches = []

    def run_gather(self, status=None, concurrency=1):
        def request(identifier):
            if identifier == 1 and status is not None:
                raise patch_collector.nc.NCentralError('API error', status=status)
            return empty_patch()

        def map_devices(devices, worker, limit):
            self.assertEqual(limit, concurrency)
            result = []
            for idx, dev in enumerate(devices):
                result.append(worker(dev, idx))
            return result

        with patch.object(patch_collector.nc, 'get_devices_for_org_unit', return_value=self.devices), \
             patch.object(patch_collector.nc, 'get_device_patch_status', side_effect=request) as fetch, \
             patch.object(patch_collector.nc, 'with_concurrency_limit', side_effect=map_devices):
            try:
                return patch_collector._gather(99, 'Customer', None, concurrency)
            finally:
                self.patches = [c.args[0] for c in fetch.call_args_list]

    def test_404_is_unavailable_and_other_device_continues(self):
        rows, entries, errors = self.run_gather(404)
        self.assertEqual([d['coverage'] for d in rows], ['unavailable', 'no-inventory'])
        self.assertEqual(len(errors), 1)
        self.assertIn('device 1', errors[0])
        self.assertEqual(self.patches, [1, 2])
        self.assertFalse(entries)

    def test_429_stops_before_next_device(self):
        with self.assertRaises(patch_collector.nc.NCentralError) as context:
            self.run_gather(429)
        self.assertEqual(context.exception.status, 429)
        self.assertEqual(self.patches, [1])

    def test_server_errors_are_not_swallowed(self):
        with self.assertRaises(patch_collector.nc.NCentralError):
            self.run_gather(503)
        self.assertEqual(self.patches, [1])

    def test_permission_denied_is_not_mislabeled_as_unavailable(self):
        with self.assertRaises(patch_collector.nc.NCentralError):
            self.run_gather(403)
        self.assertEqual(self.patches, [1])

    def test_other_device_error_preserves_partial_data(self):
        rows, _, errors = self.run_gather(400)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(errors), 1)

    def test_concurrency_is_bounded(self):
        with self.assertRaises(ValueError):
            patch_collector.collect(concurrency=0)
        with self.assertRaises(ValueError):
            patch_collector.collect(concurrency=6)


class CollectFailureTests(unittest.TestCase):
    def test_rate_limit_marks_run_failed_and_skips_other_customers(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (123,)
        with patch.object(patch_collector, 'connect', return_value=conn), \
             patch.object(patch_collector, 'active_org_units', return_value=[(1, 'A'), (2, 'B')]), \
             patch.object(patch_collector, '_gather', side_effect=patch_collector.nc.NCentralError('Rate limited', status=429)) as gather:
            result = patch_collector.collect(verbose=False)
        self.assertEqual(gather.call_count, 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['devices'], 0)
        self.assertGreaterEqual(conn.commit.call_count, 2)  # run insert + failure update
        conn.close.assert_called_once()

    def test_write_error_rolls_back_then_marks_run_failed(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (124,)
        d = dict(device_id=1, coverage='windows')
        with patch.object(patch_collector, 'connect', return_value=conn), \
             patch.object(patch_collector, 'active_org_units', return_value=[(1, 'A'), (2, 'B')]), \
             patch.object(patch_collector, '_gather', return_value=([d], [], [])), \
             patch.object(patch_collector, '_write', side_effect=RuntimeError('write failed')) as write:
            result = patch_collector.collect(verbose=False)
        self.assertEqual(write.call_count, 1)
        self.assertIn('database write failed', result['errors'][0])
        conn.rollback.assert_called()
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
