import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import sys

# Configure Django with an in-memory SQLite database for authentication-free unit tests.
# The tests only call pure parsing functions: no database or API requests.
sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from api.collection_freshness import annotate_stored_data_age
from api.collection_health_core import build_health, read_job_log, summarise_log

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)

class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.logs = self.base / 'logs' / 'collectors'
        self.logs.mkdir(parents=True)
        (self.base / 'collector_jobs.json').write_text(json.dumps({'jobs': {'ncentral_inventory': {'enabled': True, 'interval_minutes': 60}}}))

    def write(self, name, text):
        (self.logs / (name + '.log')).write_text(text)

    def test_never_run(self):
        result = build_health(self.base, now=NOW)
        self.assertTrue(result['configured'])
        self.assertEqual(result['jobs'][0]['status'], 'NEVER_RUN')
        self.assertEqual(result['jobs'][0]['freshness'], 'UNKNOWN')

    def test_success_current(self):
        self.write('ncentral_inventory', '2026-11-09T09:40:00+00:00 START job=ncentral_inventory duration_seconds=0.000 exit_code=None dry_run=False\n2026-11-09T09:40:08+00:00 SUCCESS job=ncentral_inventory duration_seconds=8.12 exit_code=0 dry_run=False\n')
        item = build_health(self.base, now=NOW)['jobs'][0]
        self.assertEqual((item['status'], item['freshness'], item['last_duration_seconds']), ('SUCCESS', 'CURRENT', 8.12))

    def test_dry_run_not_fresh(self):
        self.write('ncentral_inventory', '2026-11-09T09:50:00+00:00 SUCCESS job=ncentral_inventory duration_seconds=2.5 exit_code=0 dry_run=True\n')
        self.assertEqual(build_health(self.base, now=NOW)['jobs'][0]['freshness'], 'UNKNOWN')

    def test_stale(self):
        self.write('ncentral_inventory', '2026-11-09T07:00:00+00:00 SUCCESS job=ncentral_inventory duration_seconds=2.5 exit_code=0 dry_run=False\n')
        self.assertEqual(build_health(self.base, now=NOW)['jobs'][0]['freshness'], 'STALE')

    def test_partial_nonzero_and_how_many_devices_could_not_be_found(self):
        self.write('ncentral_patches', '2026-11-09T09:00:00+00:00 PARTIAL job=ncentral_patches duration_seconds=18.0 exit_code=1 dry_run=False reason=x devices=58 entries=1074 unavailable=6\n')
        item = build_health(self.base, now=NOW)['jobs'][1]
        self.assertEqual((item['status'], item['last_exit_code'], item['disconnected_devices']), ('PARTIAL', 1, 6))

    def test_only_a_partial_run_reports_disconnected_devices_and_a_missing_count_is_none(self):
        self.write('ncentral_patches', '2026-11-09T09:00:00+00:00 SUCCESS job=ncentral_patches duration_seconds=18.0 exit_code=0 dry_run=False unavailable=3\n')
        self.assertIsNone(build_health(self.base, now=NOW)['jobs'][1]['disconnected_devices'])
        self.write('ncentral_patches', '2026-11-09T09:00:00+00:00 PARTIAL job=ncentral_patches duration_seconds=18.0 exit_code=1 dry_run=False\n')
        self.assertIsNone(build_health(self.base, now=NOW)['jobs'][1]['disconnected_devices'])

    def test_latest_unmatched_start(self):
        self.write('ncentral_inventory', '2026-11-09T09:10:00+00:00 SUCCESS job=ncentral_inventory duration_seconds=10.0 exit_code=0 dry_run=False\n2026-11-09T09:50:00+00:00 START job=ncentral_inventory duration_seconds=0.000 exit_code=None dry_run=False\n')
        self.assertEqual(build_health(self.base, now=NOW)['jobs'][0]['status'], 'STARTED')

    def test_missing_config(self):
        (self.base / 'collector_jobs.json').unlink()
        self.assertFalse(build_health(self.base, now=NOW)['configured'])

    def test_no_log_leak(self):
        self.write('autotask_tickets', '2026-11-09T09:50:00+00:00 FAILED job=autotask_tickets duration_seconds=1.0 exit_code=1 dry_run=False reason=secret\n')
        self.assertNotIn('secret', json.dumps(build_health(self.base, now=NOW)))

    def test_old_log_does_not_mask_latest(self):
        self.write('ncentral_inventory', '2026-11-09T09:10:00+00:00 START job=ncentral_inventory duration_seconds=0.000 exit_code=None dry_run=False\n2026-11-09T09:10:10+00:00 FAILED job=ncentral_inventory duration_seconds=10.0 exit_code=1 dry_run=False\n2026-11-09T09:50:00+00:00 SUCCESS job=ncentral_inventory duration_seconds=2.0 exit_code=0 dry_run=False\n')
        self.assertEqual(build_health(self.base, now=NOW)['jobs'][0]['status'], 'SUCCESS')

def line(when, status, job='ncentral_inventory', dry=False, reason=None):
    code = '0' if status in ('SUCCESS',) else '1'
    extra = f' reason={reason}' if reason else ''
    return f'{when} {status} job={job} duration_seconds={"0.000" if status == "START" else "5.0"} exit_code={"None" if status == "START" else code} dry_run={dry}{extra}\n'


class LastGoodRun(unittest.TestCase):
    """Freshness is judged from the last run that succeeded or completed partly, never from a failure (FRESH-01)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'logs' / 'collectors').mkdir(parents=True)
        self.config = {'ncentral_inventory': {'enabled': True, 'interval_minutes': 60}}
        self.write_config()

    def write_config(self):
        (self.base / 'collector_jobs.json').write_text(json.dumps({'jobs': self.config}))

    def log(self, *lines, job='ncentral_inventory'):
        (self.base / 'logs' / 'collectors' / f'{job}.log').write_text(''.join(lines))

    def job(self):
        return build_health(self.base, now=NOW)['jobs'][0]

    def test_a_failure_after_an_old_success_leaves_the_data_stale_with_the_last_good_time(self):
        self.log(line('2026-11-06T09:00:00+00:00', 'SUCCESS'), line('2026-11-09T09:55:00+00:00', 'START'), line('2026-11-09T09:55:15+00:00', 'FAILED'))
        item = self.job()
        self.assertEqual((item['status'], item['freshness'], item['freshness_basis']), ('FAILED', 'STALE', 'last good run'))
        self.assertEqual(item['last_good_at'], '2026-11-06T09:00:00+00:00')
        self.assertEqual(item['stale_since'], '2026-11-06T10:00:00+00:00')          # the good run plus the 60-minute interval
        self.assertEqual(item['failures_since_good'], 1)
        self.assertAlmostEqual(item['good_run_age_hours'], 73.0)

    def test_a_failure_does_not_make_fresh_data_look_stale(self):
        self.log(line('2026-11-09T09:30:00+00:00', 'SUCCESS'), line('2026-11-09T09:55:00+00:00', 'FAILED'))
        item = self.job()
        self.assertEqual((item['status'], item['freshness'], item['stale_since']), ('FAILED', 'CURRENT', None))

    def test_a_job_that_has_never_succeeded_has_unknown_freshness_and_counts_its_failures(self):
        self.log(*[line(f'2026-11-09T0{h}:00:00+00:00', 'FAILED') for h in (5, 6, 7)])
        item = self.job()
        self.assertEqual((item['freshness'], item['freshness_basis'], item['last_good_at'], item['failures_since_good']), ('UNKNOWN', 'none', None, 3))

    def test_a_partial_run_counts_as_good_because_it_collected_every_device_it_could_reach(self):
        self.log(line('2026-11-09T09:20:00+00:00', 'PARTIAL', reason='unavailable'), line('2026-11-09T09:50:00+00:00', 'FAILED'))
        item = self.job()
        self.assertEqual((item['freshness'], item['last_good_status'], item['failures_since_good']), ('CURRENT', 'PARTIAL', 1))

    def test_a_dry_run_is_never_good_and_a_failed_dry_run_is_not_counted(self):
        self.log(line('2026-11-09T09:50:00+00:00', 'SUCCESS', dry=True), line('2026-11-09T09:55:00+00:00', 'FAILED', dry=True))
        item = self.job()
        self.assertEqual((item['freshness'], item['last_good_at'], item['failures_since_good']), ('UNKNOWN', None, 0))

    def test_failures_are_counted_only_since_the_last_good_run(self):
        self.log(line('2026-11-09T05:00:00+00:00', 'FAILED'), line('2026-11-09T06:00:00+00:00', 'FAILED'), line('2026-11-09T07:00:00+00:00', 'SUCCESS'),
                 line('2026-11-09T08:00:00+00:00', 'FAILED'))
        self.assertEqual(self.job()['failures_since_good'], 1)

    def test_a_run_in_progress_does_not_hide_the_last_good_run(self):
        self.log(line('2026-11-09T09:30:00+00:00', 'SUCCESS'), line('2026-11-09T09:55:00+00:00', 'START'))
        item = self.job()
        self.assertEqual((item['status'], item['freshness'], item['last_good_at']), ('STARTED', 'CURRENT', '2026-11-09T09:30:00+00:00'))

    def test_without_an_interval_freshness_stays_unknown_but_the_age_is_still_given(self):
        self.config['ncentral_inventory'].pop('interval_minutes')
        self.write_config()
        self.log(line('2026-11-09T08:00:00+00:00', 'SUCCESS'))
        item = self.job()
        self.assertEqual((item['freshness'], item['freshness_basis']), ('UNKNOWN', 'none'))
        self.assertAlmostEqual(item['good_run_age_hours'], 2.0)

    def test_only_the_jobs_own_lines_count(self):
        self.log(line('2026-11-09T09:55:00+00:00', 'SUCCESS', job='ncentral_patches'), line('2026-11-09T09:00:00+00:00', 'FAILED'))
        item = self.job()
        self.assertEqual((item['last_good_at'], item['failures_since_good']), (None, 1))

    def test_the_summary_of_an_unreadable_log_is_none(self):
        self.assertIsNone(summarise_log(self.base / 'logs' / 'collectors' / 'missing.log', 'ncentral_inventory'))

    def test_every_field_the_data_collection_page_reads_is_in_the_payload(self):
        # frontend/src/freshness.js and CollectionHealth.jsx read these names; renaming one here breaks the page without any other test failing
        self.log(line('2026-11-09T09:30:00+00:00', 'SUCCESS'))
        jobs = build_health(self.base, now=NOW)['jobs']
        annotate_stored_data_age(jobs, {'ncentral_inventory': NOW - timedelta(minutes=5)}, NOW)
        needed = {'job', 'enabled', 'status', 'last_run_at', 'last_duration_seconds', 'last_good_at', 'good_run_age_hours', 'stale_since', 'failures_since_good',
                  'freshness', 'freshness_basis', 'data_as_of', 'data_age_hours', 'data_age_source', 'interval_minutes'}
        self.assertEqual(needed - set(jobs[0]), set())
        self.assertEqual(jobs[0]['freshness_basis'], 'last good run')

    def test_a_failure_reason_is_never_in_the_health_output(self):
        self.log(line('2026-11-09T09:50:00+00:00', 'FAILED', reason='secret-token-value'))
        self.assertNotIn('secret-token-value', json.dumps(build_health(self.base, now=NOW)))


class StoredTimestampIsAFallback(unittest.TestCase):
    def jobs(self, **fields):
        return [{'job': 'ncentral_inventory', 'interval_minutes': 60, 'freshness': 'UNKNOWN', 'freshness_basis': 'none', **fields}]

    def test_a_job_judged_from_its_last_good_run_is_not_overridden_by_a_fresh_looking_timestamp(self):
        # a loader that writes its snapshot row and then fails leaves a fresh timestamp behind: it must not make the job look current
        jobs = self.jobs(freshness='STALE', freshness_basis='last good run')
        annotate_stored_data_age(jobs, {'ncentral_inventory': NOW - timedelta(minutes=5)}, NOW)
        self.assertEqual((jobs[0]['freshness'], jobs[0]['freshness_basis']), ('STALE', 'last good run'))
        self.assertEqual(jobs[0]['data_age_source'], 'database')     # still shown as information
        self.assertAlmostEqual(jobs[0]['data_age_hours'], 5 / 60)

    def test_with_no_good_run_in_the_log_the_stored_timestamp_gives_freshness(self):
        jobs = self.jobs()
        annotate_stored_data_age(jobs, {'ncentral_inventory': NOW - timedelta(minutes=30)}, NOW)
        self.assertEqual((jobs[0]['freshness'], jobs[0]['freshness_basis']), ('CURRENT', 'stored data'))
        jobs = self.jobs()
        annotate_stored_data_age(jobs, {'ncentral_inventory': NOW - timedelta(hours=5)}, NOW)
        self.assertEqual(jobs[0]['freshness'], 'STALE')

    def test_with_neither_a_good_run_nor_a_timestamp_it_stays_unknown(self):
        jobs = self.jobs()
        annotate_stored_data_age(jobs, {'ncentral_inventory': None}, NOW)
        self.assertEqual((jobs[0]['freshness'], jobs[0]['freshness_basis'], jobs[0]['data_age_source']), ('UNKNOWN', 'none', 'unavailable'))

    def test_a_good_run_in_the_log_is_not_thrown_away_when_the_database_has_nothing(self):
        jobs = self.jobs(freshness='CURRENT', freshness_basis='last good run')
        annotate_stored_data_age(jobs, {'ncentral_inventory': None}, NOW)
        self.assertEqual(jobs[0]['freshness'], 'CURRENT')


if __name__=='__main__':
    unittest.main()


class PublicFreshness(unittest.TestCase):
    """The "data as of" view anyone signed in may see: a time, a state, a count, and nothing else."""

    def job(self, name, **fields):
        base = {'job': name, 'enabled': True, 'status': 'SUCCESS', 'last_run_at': None, 'last_good_at': None, 'good_run_age_hours': None, 'failures_since_good': 0,
                'freshness': 'UNKNOWN', 'freshness_basis': 'none', 'data_as_of': None, 'data_age_hours': None, 'last_exit_code': 0, 'stale_since': None,
                'last_duration_seconds': 1.5, 'last_dry_run': False, 'interval_minutes': 1440}
        return {**base, **fields}

    def sources(self, *jobs):
        from api.collection_health_core import public_freshness
        return {s['key']: s for s in public_freshness(list(jobs))}

    def test_the_four_pages_are_covered_in_order_and_a_job_with_no_entry_is_never_collected(self):
        from api.collection_health_core import PAGE_SOURCES, public_freshness
        result = public_freshness([])
        self.assertEqual([s['key'] for s in result], ['patches', 'servers', 'backups', 'tickets'])
        self.assertEqual([s['key'] for s in result], [k for k, _, _ in PAGE_SOURCES])
        self.assertTrue(all(s['state'] == 'never' and s['as_of'] is None and s['basis'] == 'none' for s in result))

    def test_every_source_is_filled_by_a_real_job(self):
        from api.collection_health_core import JOB_NAMES, PAGE_SOURCES
        self.assertTrue(all(job in JOB_NAMES for _, _, job in PAGE_SOURCES))

    def test_a_job_that_worked_recently_is_current_and_the_time_is_the_last_good_run(self):
        row = self.sources(self.job('ncentral_patches', last_good_at='2026-11-10T08:00:00+00:00', good_run_age_hours=2.0, freshness='CURRENT', freshness_basis='last good run'))['patches']
        self.assertEqual((row['state'], row['as_of'], row['age_hours'], row['basis'], row['failures_since']), ('current', '2026-11-10T08:00:00+00:00', 2.0, 'last good run', 0))

    def test_a_job_that_has_failed_since_is_stale_and_says_how_many_times_and_still_gives_the_last_good_time(self):
        row = self.sources(self.job('ncentral_patches', status='FAILED', last_run_at='2026-11-11T08:00:00+00:00', last_good_at='2026-11-09T12:29:00+00:00',
                                    good_run_age_hours=44.0, freshness='STALE', failures_since_good=3, freshness_basis='last good run'))['patches']
        self.assertEqual((row['state'], row['as_of'], row['failures_since']), ('stale', '2026-11-09T12:29:00+00:00', 3))       # not the time of the failed run

    def test_with_no_good_run_the_newest_stored_row_is_used_and_says_so(self):
        row = self.sources(self.job('cove_backup', data_as_of='2026-11-10T08:20:00+00:00', data_age_hours=5.0, freshness='CURRENT', freshness_basis='stored data'))['backups']
        self.assertEqual((row['state'], row['as_of'], row['age_hours'], row['basis']), ('current', '2026-11-10T08:20:00+00:00', 5.0, 'stored data'))

    def test_a_time_with_no_schedule_to_judge_it_by_is_unknown_not_current(self):
        row = self.sources(self.job('sentinelone_monthly', last_good_at='2026-10-01T00:00:00+00:00', good_run_age_hours=900.0, freshness='UNKNOWN'))['servers']
        self.assertEqual((row['state'], row['as_of']), ('unknown', '2026-10-01T00:00:00+00:00'))

    def test_a_job_that_has_never_worked_is_never_even_if_it_has_failed_and_it_says_whether_it_is_switched_on(self):
        row = self.sources(self.job('autotask_tickets', enabled=False, status='FAILED', failures_since_good=2))['tickets']
        self.assertEqual((row['state'], row['as_of'], row['basis'], row['failures_since'], row['enabled']), ('never', None, 'none', 2, False))

    def test_nothing_from_the_logs_or_the_schedule_can_leak(self):
        row = self.sources(self.job('ncentral_patches', last_good_at='2026-11-10T08:00:00+00:00', good_run_age_hours=2.0, freshness='CURRENT', last_exit_code=1,
                                    last_duration_seconds=99.9, interval_minutes=60, stale_since='x', last_run_at='y'))['patches']
        self.assertEqual(sorted(row), ['age_hours', 'as_of', 'basis', 'enabled', 'failures_since', 'key', 'label', 'state'])

    def test_a_missing_or_odd_count_of_failures_is_a_number(self):
        self.assertEqual(self.sources(self.job('ncentral_patches', failures_since_good=None))['patches']['failures_since'], 0)
