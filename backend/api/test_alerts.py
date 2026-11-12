"""Collector alerts, the worker heartbeat and the worker's tick, with no external services. Uses sqlite files in a temp folder."""
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from api.collection_alerts import (ALERT_DDL, active_rows, current_alerts, evaluate, format_email, plan_alerts,
                                   record)
from api.collector_queue import heartbeat_status, touch_heartbeat
from api.management.commands.collection_worker import Command as Worker
from api.management.commands.run_collection import JobLock

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
FRESH_BEAT = {'last_tick_at': (NOW - timedelta(minutes=1)).isoformat()}
SCHEDULED = {'ncentral_inventory': {'schedule_enabled': True}}


def job(**overrides):
    row = {'job': 'ncentral_inventory', 'enabled': True, 'status': 'SUCCESS', 'last_run_at': '2026-11-09T11:30:00+00:00',
           'interval_minutes': 60, 'data_age_hours': 0.5}
    row.update(overrides)
    return row


def keys(jobs, schedules=SCHEDULED, heartbeat=FRESH_BEAT, config=None, **kwargs):
    return [a['key'] for a in evaluate(jobs, schedules, config or {}, heartbeat, NOW, **kwargs)]


class Rules(SimpleTestCase):
    def test_a_healthy_scheduled_job_raises_nothing(self):
        self.assertEqual(keys([job()]), [])

    def test_a_failed_job_is_reported_with_its_name_and_time(self):
        found = evaluate([job(status='FAILED')], SCHEDULED, {}, FRESH_BEAT, NOW)
        self.assertEqual([(a['key'], a['severity']) for a in found], [('ncentral_inventory:failed', 'error')])
        self.assertIn('N-central inventory failed', found[0]['message'])
        self.assertIn('2026-11-09 11:30 UTC', found[0]['message'])

    def test_disabled_jobs_are_never_reported(self):
        self.assertEqual(keys([job(enabled=False, status='FAILED', data_age_hours=999)]), [])

    def test_partial_results_are_only_reported_when_asked_for(self):
        self.assertEqual(keys([job(status='PARTIAL')]), [])
        self.assertEqual(keys([job(status='PARTIAL')], include_partial=True), ['ncentral_inventory:partial'])
        alert = evaluate([job(status='PARTIAL')], SCHEDULED, {}, FRESH_BEAT, NOW, include_partial=True)[0]
        self.assertEqual(alert['severity'], 'warning')
        self.assertIn('disconnected', alert['message'])                     # the devices N-central cannot find count as disconnected (NC-01)

    def test_a_run_that_never_finished_is_stuck_only_after_its_time_limit(self):
        recent = job(status='STARTED', last_run_at=(NOW - timedelta(minutes=10)).isoformat())
        old = job(status='STARTED', last_run_at=(NOW - timedelta(hours=2)).isoformat())
        config = {'ncentral_inventory': {'timeout_seconds': 1200}}   # 20 min x 1.5 = 30 min
        self.assertEqual(keys([recent], config=config), [])
        self.assertEqual(keys([old], config=config), ['ncentral_inventory:stuck'])

    def test_old_data_is_overdue_after_a_grace_period(self):
        self.assertEqual(keys([job(data_age_hours=1.5)]), [])                 # 90 min: inside 1.5 x 60 + 10
        self.assertEqual(keys([job(data_age_hours=1.7)]), ['ncentral_inventory:overdue'])

    def test_overdue_needs_a_schedule(self):
        self.assertEqual(keys([job(data_age_hours=50)], schedules={'ncentral_inventory': {'schedule_enabled': False}}), [])

    def test_a_scheduled_job_with_no_stored_data_is_a_warning(self):
        found = evaluate([job(data_age_hours=None)], SCHEDULED, {}, FRESH_BEAT, NOW)
        self.assertEqual([(a['key'], a['severity']) for a in found], [('ncentral_inventory:nodata', 'warning')])

    def test_a_silent_or_missing_worker_is_reported_only_when_something_is_scheduled(self):
        stale = {'last_tick_at': (NOW - timedelta(minutes=30)).isoformat()}
        self.assertEqual(keys([job()], heartbeat=stale), ['worker:stale'])
        self.assertEqual(keys([job()], heartbeat={'last_tick_at': None}), ['worker:never'])
        self.assertEqual(keys([job()], heartbeat=None), ['worker:never'])
        self.assertEqual(keys([job()], schedules={'ncentral_inventory': {'schedule_enabled': False}}, heartbeat=stale), [])
        self.assertEqual(keys([job()], heartbeat={'last_tick_at': (NOW - timedelta(minutes=4)).isoformat()}), [])


class Announcing(SimpleTestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.execute(ALERT_DDL)
        self.failed = [{'key': 'a:failed', 'severity': 'error', 'job': 'a', 'message': 'A failed.'}]

    def step(self, alerts, minutes=0):
        now = NOW + timedelta(minutes=minutes)
        plan = plan_alerts(active_rows(self.con), alerts, now, repeat_hours=24)
        record(self.con, plan, now)
        return plan

    def test_a_problem_is_announced_once(self):
        self.assertEqual(len(self.step(self.failed)['new']), 1)
        again = self.step(self.failed, minutes=5)
        self.assertEqual((again['new'], again['repeat'], again['resolved']), ([], [], []))
        self.assertIsNone(format_email(again))

    def test_a_reminder_comes_after_the_repeat_interval_only(self):
        self.step(self.failed)
        self.assertEqual(self.step(self.failed, minutes=23 * 60)['repeat'], [])
        self.assertEqual(len(self.step(self.failed, minutes=25 * 60)['repeat']), 1)

    def test_a_cleared_problem_is_announced_once_with_its_original_message(self):
        self.step(self.failed)
        cleared = self.step([], minutes=10)
        self.assertEqual(cleared['resolved'], [{'key': 'a:failed', 'message': 'A failed.'}])
        self.assertEqual(self.step([], minutes=20)['resolved'], [])
        subject, body = format_email(cleared)
        self.assertEqual(subject, '[Report Manager] Collection alerts resolved')
        self.assertIn('A failed.', body)
        self.assertIn('No problems are open now.', body)

    def test_a_problem_that_comes_back_is_new_again(self):
        self.step(self.failed)
        self.step([], minutes=10)
        self.assertEqual(len(self.step(self.failed, minutes=20)['new']), 1)

    def test_nothing_is_recorded_unless_record_is_called(self):
        plan = plan_alerts(active_rows(self.con), self.failed, NOW)   # an email that failed to send never reaches record()
        self.assertEqual(len(plan['new']), 1)
        self.assertEqual(len(plan_alerts(active_rows(self.con), self.failed, NOW)['new']), 1)

    def test_email_text(self):
        subject, body = format_email(plan_alerts({}, self.failed, NOW))
        self.assertEqual(subject, '[Report Manager] 1 collection alert(s)')
        self.assertIn('New problems:', body)
        self.assertIn('  - A failed.', body)
        self.assertIn('1 problem(s) open now.', body)


class HeartbeatAndWorker(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'collector_jobs.json').write_text(json.dumps({'jobs': {}}))

    def tick(self):
        with override_settings(BASE_DIR=self.base):
            Worker().handle()

    def test_no_heartbeat_until_the_worker_has_run(self):
        self.assertEqual(heartbeat_status(self.base), {'last_tick_at': None, 'state': None, 'age_seconds': None})

    def test_a_heartbeat_is_recorded_and_ages(self):
        touch_heartbeat(self.base, 'IDLE')
        later = datetime.now(timezone.utc) + timedelta(minutes=10)
        status = heartbeat_status(self.base, later)
        self.assertEqual(status['state'], 'IDLE')
        self.assertTrue(599 <= status['age_seconds'] <= 601)

    def test_a_tick_with_nothing_to_do_records_idle(self):
        self.tick()
        self.assertEqual(heartbeat_status(self.base)['state'], 'IDLE')

    def test_a_tick_that_finds_the_worker_busy_exits_quietly_and_still_counts(self):
        with JobLock(self.base / 'logs' / 'collectors' / 'worker.lock'):
            self.tick()                       # must not raise: a busy worker is normal, not a failure
        self.assertEqual(heartbeat_status(self.base)['state'], 'BUSY')

    def test_the_dashboard_alerts_use_the_saved_schedule_and_heartbeat(self):
        (self.base / 'collector_jobs.json').write_text(json.dumps({'jobs': {'ncentral_inventory': {'enabled': True, 'schedule_enabled': True, 'interval_minutes': 60}}}))
        self.assertEqual([a['key'] for a in current_alerts(self.base, [job()], NOW)], ['worker:never'])
        touch_heartbeat(self.base, 'IDLE')
        self.assertEqual(current_alerts(self.base, [job()], datetime.now(timezone.utc)), [])


class OverdueFollowsTheLastGoodRun(SimpleTestCase):
    """The overdue alert uses the same basis as freshness, so a failed run that left a fresh timestamp cannot hide a stale job."""

    def run_rules(self, **fields):
        return evaluate([job(**fields)], SCHEDULED, {}, FRESH_BEAT, NOW)

    def test_a_job_last_good_a_long_time_ago_is_overdue_even_if_the_database_looks_fresh(self):
        found = self.run_rules(freshness_basis='last good run', good_run_age_hours=30, data_age_hours=0.1)
        self.assertEqual([a['key'] for a in found], ['ncentral_inventory:overdue'])
        self.assertIn('last succeeded', found[0]['message'])

    def test_a_recent_good_run_is_not_overdue_even_if_the_database_looks_old(self):
        self.assertEqual(self.run_rules(freshness_basis='last good run', good_run_age_hours=0.5, data_age_hours=300), [])

    def test_the_grace_period_is_the_same(self):
        self.assertEqual(self.run_rules(freshness_basis='last good run', good_run_age_hours=1.5), [])          # 90 minutes: inside 1.5 x 60 + 10
        self.assertEqual([a['key'] for a in self.run_rules(freshness_basis='last good run', good_run_age_hours=1.7)], ['ncentral_inventory:overdue'])

    def test_with_no_good_run_the_stored_timestamp_is_used_as_before(self):
        self.assertEqual([a['key'] for a in self.run_rules(freshness_basis='stored data', data_age_hours=5)], ['ncentral_inventory:overdue'])
        self.assertEqual(self.run_rules(freshness_basis='stored data', data_age_hours=0.2), [])

    def test_nothing_to_go_on_is_a_warning(self):
        found = self.run_rules(freshness_basis='none', data_age_hours=None, good_run_age_hours=None)
        self.assertEqual([a['key'] for a in found], ['ncentral_inventory:nodata'])
        self.assertIn('no successful run', found[0]['message'])


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeProcess:
    def __init__(self):
        self.running = True

    def poll(self):
        return None if self.running else 0


class SchedulerRules(SimpleTestCase):
    def setUp(self):
        from api.management.commands.run_scheduler import Scheduler
        self.clock, self.launched, self.alert_runs = FakeClock(), [], []
        self.fail_alerts = False

        def launch():
            self.launched.append(FakeProcess())
            return self.launched[-1]

        def alerts():
            self.alert_runs.append(self.clock.now)
            if self.fail_alerts:
                raise RuntimeError('mail down')
        self.scheduler = Scheduler(launch, alerts, worker_every=60, alerts_every=300, clock=self.clock)

    def advance(self, seconds):
        self.clock.now += seconds
        return self.scheduler.step()

    def test_both_run_straight_away(self):
        self.scheduler.step()
        self.assertEqual((len(self.launched), len(self.alert_runs)), (1, 1))

    def test_a_worker_tick_every_minute_once_the_last_one_has_finished(self):
        self.scheduler.step()
        self.launched[-1].running = False
        self.advance(30)
        self.assertEqual(len(self.launched), 1)          # not yet due
        self.advance(30)
        self.assertEqual(len(self.launched), 2)

    def test_a_long_batch_is_not_given_a_second_worker(self):
        self.scheduler.step()                            # the first worker is still running (a long batch)
        for _ in range(5):
            self.advance(60)
        self.assertEqual(len(self.launched), 1)
        self.launched[0].running = False
        self.advance(60)
        self.assertEqual(len(self.launched), 2)

    def test_alerts_keep_running_while_a_batch_runs(self):
        self.scheduler.step()                            # worker busy for the whole time
        for _ in range(10):
            self.advance(60)
        self.assertEqual(len(self.alert_runs), 3)        # at 0, 300 and 600 seconds

    def test_an_alert_failure_is_reported_and_does_not_stop_the_scheduler(self):
        self.fail_alerts = True
        messages = self.scheduler.step()
        self.assertTrue(any('Alert check failed: RuntimeError: mail down' in m for m in messages))
        self.fail_alerts = False
        self.advance(300)
        self.assertEqual(len(self.alert_runs), 2)


class WorkerRunsJobs(SimpleTestCase):
    def setUp(self):
        self.worker = Worker()
        self.beats = []
        self.worker.beat = lambda base, state: self.beats.append(state)

    def test_the_heartbeat_continues_while_a_long_job_runs(self):
        import sys
        done = self.worker.run_job('.', [sys.executable, '-c', 'import time; time.sleep(0.5); print("finished")'], timeout=10, beat_every=0.1)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, 'finished'))
        self.assertTrue(len(self.beats) >= 2, self.beats)

    def test_a_job_that_runs_too_long_is_killed(self):
        import subprocess
        import sys
        import time
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.worker.run_job('.', [sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.3, beat_every=0.1)
        self.assertLess(time.monotonic() - started, 5)

    def test_output_larger_than_a_pipe_buffer_does_not_stall_the_job(self):
        import sys
        done = self.worker.run_job('.', [sys.executable, '-c', 'print("x" * 500000)'], timeout=10, beat_every=0.1)
        self.assertEqual(len(done.stdout.strip()), 500000)
