"""Offline tests for collector job plumbing; no external services, no database."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from api.auth import can_manage_collectors
from api.collection_freshness import SOURCE_QUERIES
from api.collection_health_core import JOB_LABELS, JOB_NAMES
from api.management.commands.run_collection import (JOB_COMMANDS, NO_DRY_RUN, Command, JobLock, format_log,
                                                    load_job_config)

BACKEND = Path(settings.BASE_DIR)
# collector_jobs.json is the user's own file (it is never shipped, so an archive cannot overwrite their settings): these tests judge
# the shipped defaults by the shipped example, and only check the shape of the user's file when it exists.
EXAMPLE = BACKEND / 'collector_jobs.example.json'
LIVE = BACKEND / 'collector_jobs.json'


class CollectionConfigTests(SimpleTestCase):
    def test_log_includes_success_and_duration(self):
        line = format_log("SUCCESS", "ncentral_inventory", 0.123, 0)
        self.assertIn("SUCCESS", line)
        self.assertIn("duration_seconds=0.123", line)
        self.assertIn("exit_code=0", line)

    def test_lock_file_created_and_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "job.lock"
            with JobLock(path):
                self.assertTrue(path.exists())
            path.unlink()

    def test_config_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "jobs.json"
            p.write_text('{"jobs": {"test": {"enabled": false}}}')
            self.assertFalse(load_job_config(p)["jobs"]["test"]["enabled"])


class JobRegistry(SimpleTestCase):
    """A job must be listed in every registry, or it fails in a confusing place. These tests catch drift."""

    def test_every_registry_lists_the_same_jobs(self):
        self.assertEqual(set(JOB_NAMES), set(JOB_COMMANDS))
        self.assertEqual(set(JOB_NAMES), set(SOURCE_QUERIES))
        self.assertTrue(NO_DRY_RUN <= set(JOB_COMMANDS))
        self.assertEqual(len(JOB_NAMES), len(set(JOB_NAMES)))
        self.assertEqual(set(JOB_NAMES), set(JOB_LABELS))

    def test_config_files_are_complete_and_valid(self):
        for path in [EXAMPLE] + ([LIVE] if LIVE.is_file() else []):
            name = path.name
            config = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(set(config['jobs']), set(JOB_NAMES), name)
            for job, cfg in config['jobs'].items():
                self.assertTrue(1 <= cfg['interval_minutes'] <= 10080, f'{name} {job} interval')
                self.assertTrue(1 <= cfg['timeout_seconds'] <= 7200, f'{name} {job} timeout')
                self.assertIsInstance(cfg['schedule_enabled'], bool)
            for source_dir, _script, *_ in JOB_COMMANDS.values():
                self.assertIn(source_dir, config['source_directories'], name)

    def test_every_collector_script_exists(self):
        config = json.loads(EXAMPLE.read_text(encoding='utf-8'))
        for job, (source_dir, script, *_args) in JOB_COMMANDS.items():
            path = (BACKEND / config['source_directories'][source_dir] / script).resolve()
            self.assertTrue(path.is_file(), f'{job}: {path} not found')

    def test_the_shipped_example_keeps_failing_and_new_collectors_off(self):
        jobs = json.loads(EXAMPLE.read_text(encoding='utf-8'))['jobs']
        self.assertFalse(jobs['autotask_tickets']['enabled'])    # failing upstream
        self.assertFalse(jobs['cove_backup']['enabled'])         # new: enable it after its first manual run
        for name in ('autotask_tickets', 'sentinelone_monthly', 'cove_backup'):
            self.assertFalse(jobs[name]['schedule_enabled'], name)   # only the three N-central jobs are recommended to run on a schedule

    def test_dry_run_is_refused_for_collectors_that_ignore_it(self):
        for job in NO_DRY_RUN:
            source_dir, script, *_ = JOB_COMMANDS[job]
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / script).write_text('')
                config = Path(directory) / 'jobs.json'
                config.write_text(json.dumps({'source_directories': {source_dir: directory}, 'jobs': {job: {'enabled': True}}}))
                with self.assertRaisesMessage(CommandError, 'no safe dry-run'):
                    Command().handle(job=job, config=str(config), dry_run=True)


class CollectorAccessRule(SimpleTestCase):
    staff, plain = SimpleNamespace(is_staff=True), SimpleNamespace(is_staff=False)

    @override_settings(DEBUG=False, DEV_COLLECTOR_HEALTH_FOR_ALL=False)
    def test_only_staff_by_default(self):
        self.assertTrue(can_manage_collectors(self.staff))
        self.assertFalse(can_manage_collectors(self.plain))

    @override_settings(DEBUG=True, DEV_COLLECTOR_HEALTH_FOR_ALL=True)
    def test_dev_flag_admits_everyone_in_debug(self):
        self.assertTrue(can_manage_collectors(self.plain))

    @override_settings(DEBUG=False, DEV_COLLECTOR_HEALTH_FOR_ALL=True)
    def test_dev_flag_is_ignored_outside_debug(self):
        self.assertFalse(can_manage_collectors(self.plain))
