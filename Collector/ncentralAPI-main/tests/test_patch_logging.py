"""Offline tests: no HTTP, DB, or persistent system files touched."""
import importlib
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Reuse psycopg2 fallback from the existing test module.
import test_patch_safety  # noqa: F401
collector = importlib.import_module('ncentral_patch')


class CollectorLoggingTests(unittest.TestCase):
    def tearDown(self):
        for handler in list(collector.LOGGER.handlers):
            collector.LOGGER.removeHandler(handler)
            handler.close()

    def test_creates_file_and_writes_success_with_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'patch.log'
            with patch.object(collector, 'collect', return_value={
                'devices': 2, 'entries': 17, 'missing': 3,
                'unavailable': 0, 'errors': []}):
                collector.run_collection_with_logging(dry_run=False,
                    quiet=True, concurrency=1, log_file=path)
            content = path.read_text(encoding='utf-8')
            self.assertIn('SUCCESS mode=LIVE', content)
            self.assertIn('duration_seconds=', content)
            self.assertIn('entries=17', content)

    def test_log_handler_closed_after_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patch.log'
            with patch.object(collector, 'collect', return_value={
                'devices': 0, 'entries': 0, 'missing': 0,
                'unavailable': 0, 'errors': []}):
                collector.run_collection_with_logging(
                    dry_run=True, quiet=True, concurrency=1, log_file=path)
            self.assertFalse(any(isinstance(h, logging.FileHandler)
                                 for h in collector.LOGGER.handlers))
            path.unlink()  # Deletion must work immediately, including on Windows.
            self.assertFalse(path.exists())

    def test_appends_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patch.log'
            path.write_text('earlier log entry\n', encoding='utf-8')
            with patch.object(collector, 'collect', return_value={
                'devices': 0, 'entries': 0, 'missing': 0,
                'unavailable': 0, 'errors': []}):
                collector.run_collection_with_logging(dry_run=True,
                    quiet=True, concurrency=1, log_file=path)
            content = path.read_text(encoding='utf-8')
            self.assertTrue(content.startswith('earlier log entry\n'))
            self.assertIn('SUCCESS mode=DRY_RUN', content)
            self.assertIn('database_writes=0', content)

    def test_partial_run_logs_errors_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patch.log'
            with patch.object(collector, 'collect', return_value={
                'devices': 2, 'entries': 4, 'missing': 1,
                'unavailable': 1, 'errors': ['device 99: HTTP 404']}):
                collector.run_collection_with_logging(dry_run=False,
                    quiet=True, concurrency=1, log_file=path)
            content = path.read_text(encoding='utf-8')
            self.assertIn('PARTIAL mode=LIVE', content)
            self.assertIn('unavailable=1', content)
            self.assertIn('COLLECTION_ERROR device 99: HTTP 404', content)

    def test_exception_logs_failure_and_elapsed_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patch.log'
            with patch.object(collector, 'collect', side_effect=RuntimeError('offline test')):
                with self.assertRaises(RuntimeError):
                    collector.run_collection_with_logging(dry_run=False,
                        quiet=True, concurrency=1, log_file=path)
            content = path.read_text(encoding='utf-8')
            self.assertIn('FAILED mode=LIVE duration_seconds=', content)
            self.assertIn('RuntimeError: offline test', content)

    def test_dry_run_tracks_unavailable_devices_and_errors(self):
        with patch.object(collector.nc, 'get_customers', return_value=[
                    {'orgUnitId': 1, 'orgUnitName': 'Test'}]), \
             patch.object(collector, '_gather', return_value=(
                 [{'device_id': 99, 'coverage': 'unavailable', 'days_since_last_install': None,
                   'device_name': 'd', 'entry_count': 0, 'missing_count': 0,
                   'missing_security_count': 0, 'missing_windows_count': 0,
                   'missing_thirdparty_count': 0, 'missing_definition_count': 0}],
                 [], ['device 99: HTTP 404'])):
            result = collector.collect(dry_run=True, verbose=False)
            self.assertEqual(result['unavailable'], 1)
            self.assertEqual(len(result['errors']), 1)
