"""Offline regression tests for source inventory delta calculations."""
import importlib
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import test_patch_safety  # provide fake psycopg2 if unavailable

collector = importlib.import_module('ncentral_patch')


def device(device_id=7, **kw):
    row = {'device_id': device_id, 'device_name': 'server', 'device_class': 'server',
           'os': 'Windows', 'customer_name': 'Example', 'site_name': 'Main',
           'coverage': 'windows', 'entry_count': 1, 'installed_count': 0,
           'missing_count': 1, 'missing_security_count': 1,
           'missing_windows_count': 1, 'missing_thirdparty_count': 0,
           'missing_definition_count': 0, 'last_installed_at': None,
           'undated_installed_count': 0, 'patch_monitor_states': {'patch':'ok'},
           'reboot_monitor_state': None, 'days_since_last_install': 99,
           'error': None}
    row.update(kw)
    return row


def entry(device_id=7, title='KB123456', **kw):
    row = {'device_id': device_id, 'device_name': 'server', 'title': title, 'category': 'Security',
           'category_id': '1', 'result': 'Not Installed', 'is_missing': True,
           'is_security': True, 'is_third_party': False,
           'installed_at': None, 'kb': title}
    row.update(kw)
    return row


class DeltaTests(unittest.TestCase):
    def test_new_device_and_entry(self):
        result = collector._compare_inventory([device()], [entry()], {}, {})
        self.assertEqual((result['device_new'], result['entry_new']), (1, 1))
        self.assertEqual(result['entry_removed'], 0)

    def test_unchanged_ignores_capture_age_and_run(self):
        prior = device(days_since_last_install=10, run_id=5, captured_at='old')
        current = device(days_since_last_install=11)
        result = collector._compare_inventory([current], [entry()],
                                               {7: prior}, {(7, 'KB123456'): entry()})
        self.assertEqual((result['device_unchanged'], result['entry_unchanged']), (1, 1))
        self.assertEqual(result['device_changed'], 0)

    def test_entry_content_change_changes_device_without_count_change(self):
        old = entry(result='Not Installed')
        new = entry(result='Installed', is_missing=False)
        result = collector._compare_inventory([device()], [new],
                                               {7: device()}, {(7, old['title']):old})
        self.assertEqual(result['device_changed'], 1)
        self.assertEqual(result['entry_changed'], 1)

    def test_missing_old_entry_counts_as_removed(self):
        result = collector._compare_inventory([device(entry_count=0, missing_count=0)], [],
                                               {7: device()}, {(7, 'KB123456'):entry()})
        self.assertEqual(result['entry_removed'], 1)
        self.assertEqual(result['device_changed'], 1)

    def test_unavailable_does_not_resolve_entries(self):
        result = collector._compare_inventory([device(coverage='unavailable')], [],
                                               {7: device()}, {(7,'KB123456'):entry()})
        self.assertEqual(result['device_unavailable'], 1)
        self.assertEqual(result['entry_removed'], 0)
        self.assertEqual(result['device_new'], 0)

    def test_duplicate_titles_are_counted_once(self):
        first = entry()
        result = collector._compare_inventory([device()], [first, first], {}, {})
        self.assertEqual(result['entry_new'], 1)

    def test_previous_inventory_fetches_latest_usable_snapshot(self):
        # Test SQL constraints and cursor conversion without real DB access.
        cursor = MagicMock()
        cursor.description = [('run_id',), ('device_id',), ('coverage',)]
        cursor.fetchall.side_effect = [[(8,7,'windows')], []]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        d, e = collector._previous_inventory(conn, [7], before_run_id=10)
        self.assertEqual(d[7]['run_id'], 8)
        self.assertEqual(e, {})
        sql = cursor.execute.call_args_list[0].args[0]
        self.assertIn('dp.coverage <> \'unavailable\'', sql)
        self.assertIn('dp.run_id < %s', sql)

    def test_dry_run_without_db_is_comparison_unavailable(self):
        with patch.dict('os.environ', {'NCENTRAL_PG_DSN': ''}), \
             patch.object(collector.nc,'get_customers',return_value=[{'orgUnitId':1,'orgUnitName':'X'}]), \
             patch.object(collector,'_gather',return_value=([device()], [entry()], [])), \
             patch.object(collector,'connect') as db:
            result = collector.collect(dry_run=True, verbose=False)
            db.assert_not_called()
            self.assertFalse(result['comparison_available'])

    def test_dry_run_compares_without_writes(self):
        conn = MagicMock()
        with patch.dict('os.environ', {'NCENTRAL_PG_DSN': 'postgresql://fake'}), \
             patch.object(collector.nc,'get_customers',return_value=[{'orgUnitId':1,'orgUnitName':'X'}]), \
             patch.object(collector,'_gather',return_value=([device()], [entry()], [])), \
             patch.object(collector,'connect',return_value=conn), \
             patch.object(collector,'_previous_inventory',return_value=({},{})), \
             patch.object(collector,'_write') as writer:
            result = collector.collect(dry_run=True, verbose=False)
            writer.assert_not_called()
            self.assertEqual(result['device_new'],1)
            self.assertTrue(result['comparison_available'])
            conn.close.assert_called_once()

    def test_log_reports_deltas_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'log.txt'
            result = dict(devices=1,entries=1,missing=0,unavailable=0,errors=[],
                          comparison_available=True, **collector._empty_changes())
            result['device_changed'] = 1
            with patch.object(collector,'collect',return_value=result):
                collector.run_collection_with_logging(dry_run=True,quiet=True,concurrency=1,log_file=path)
            log = path.read_text(encoding='utf-8')
            self.assertIn('CHANGE_SUMMARY mode=DRY_RUN devices[new=0,changed=1',log)
            self.assertIn('duration_seconds=',log)
            self.assertIn('database_writes=0',log)


if __name__ == '__main__':
    unittest.main()
