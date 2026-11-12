"""How a collector run's output is classified. A non-zero exit is a failure, except one very specific case: an N-central patch run whose ONLY
problems are devices that N-central cannot find (404 DeviceNotFoundException). That is PARTIAL on the Data Collection page (NC-01, 12 Nov 2026): the
devices count as disconnected, which Server Health shows per device, and the run still collected every other device. Anything the classifier does not
recognise is never promoted to PARTIAL."""
import unittest

from api.management.commands.collection_outcomes import classify_outcome

WARN = "warn: device {n} (SRV-{n}): Request failed (404) on /api/devices/{n}/assets: {{\"message\":\"DeviceNotFoundException: Device was not found!\"}}"
OK = "ok   Acme Logistics   12 devices   340 entries   20 missing"
SUMMARY = "{devices} devices, {entries} entries, {missing} missing, {unavailable} unavailable"


def output(*lines):
    return "\n".join(lines)


def patch_run(unavailable=2, devices=58, extra=()):
    warnings = [WARN.format(n=100 + i) for i in range(unavailable)]
    return output(OK, *warnings, *extra, SUMMARY.format(devices=devices, entries=1074, missing=300, unavailable=unavailable))


class Classification(unittest.TestCase):
    def test_exit_zero_is_success(self):
        outcome = classify_outcome('ncentral_patches', 0, patch_run())
        self.assertEqual(outcome.status, 'SUCCESS')

    def test_only_device_not_found_warnings_make_a_partial_run_with_the_count(self):
        outcome = classify_outcome('ncentral_patches', 1, patch_run(unavailable=6))
        self.assertEqual((outcome.status, outcome.unavailable, outcome.devices, outcome.entries), ('PARTIAL', 6, 58, 1074))
        self.assertIn('device-asset 404s only', outcome.reason)

    def test_a_traceback_alongside_the_warnings_is_a_failure_not_a_partial_run(self):
        outcome = classify_outcome('ncentral_patches', 1, patch_run(extra=['Traceback (most recent call last):']))
        self.assertEqual(outcome.status, 'FAILED')

    def test_a_connection_error_is_a_failure(self):
        self.assertEqual(classify_outcome('ncentral_patches', 1, output(OK, 'ConnectTimeoutError: connect timeout=15')).status, 'FAILED')

    def test_warnings_that_do_not_add_up_to_the_unavailable_count_are_a_failure(self):
        text = output(OK, WARN.format(n=1), SUMMARY.format(devices=58, entries=1074, missing=300, unavailable=3))
        self.assertEqual(classify_outcome('ncentral_patches', 1, text).status, 'FAILED')

    def test_no_summary_or_no_warning_is_a_failure(self):
        self.assertEqual(classify_outcome('ncentral_patches', 1, output(OK, WARN.format(n=1))).status, 'FAILED')
        self.assertEqual(classify_outcome('ncentral_patches', 1, output(OK, SUMMARY.format(devices=58, entries=1, missing=0, unavailable=0))).status, 'FAILED')

    def test_two_summaries_are_a_failure(self):
        text = patch_run() + "\n" + SUMMARY.format(devices=1, entries=1, missing=0, unavailable=2)
        self.assertEqual(classify_outcome('ncentral_patches', 1, text).status, 'FAILED')

    def test_no_devices_at_all_is_a_failure(self):
        self.assertEqual(classify_outcome('ncentral_patches', 1, patch_run(devices=0)).status, 'FAILED')

    def test_only_the_patch_job_can_be_partial_and_only_with_exit_code_one(self):
        for job in ('ncentral_inventory', 'ncentral_monitoring', 'autotask_tickets', 'cove_backup', 'sentinelone_monthly'):
            self.assertEqual(classify_outcome(job, 1, patch_run()).status, 'FAILED', job)
        for code in (2, 3, -1, 137):
            self.assertEqual(classify_outcome('ncentral_patches', code, patch_run()).status, 'FAILED', code)

    def test_blank_lines_are_ignored_and_the_warning_text_is_matched_exactly(self):
        text = "\n\n" + patch_run() + "\n\n"
        self.assertEqual(classify_outcome('ncentral_patches', 1, text).status, 'PARTIAL')
        other = output(OK, "warn: device 1 (X): Request failed (404) on /api/devices/1/assets: SomethingElse: not found", SUMMARY.format(devices=5, entries=1, missing=0, unavailable=1))
        self.assertEqual(classify_outcome('ncentral_patches', 1, other).status, 'FAILED')
