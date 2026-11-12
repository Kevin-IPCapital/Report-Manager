import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import ncentral_inventory as inv


class InventoryTests(unittest.TestCase):
    def api(self, devices=None):
        return SimpleNamespace(
            get_customers=Mock(return_value=[{"orgUnitId": 12, "orgUnitName": "Example"}]),
            get_devices_for_org_unit=Mock(return_value=devices if devices is not None else [
                {"deviceId": 42, "longName": "Host", "supportedOs": "Windows"},
            ]),
        )

    def test_dry_run_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = Mock()
            result = inv.run(dry_run=True, log_file=Path(tmp)/"nested"/"inventory.log",
                             api=self.api(), connect=lambda: connection, delay_seconds=0)
            self.assertEqual(result["devices"], 1)
            self.assertEqual(result["written"], 0)
            connection.assert_not_called()
            self.assertIn("SUCCESS mode=DRY_RUN", (Path(tmp)/"nested"/"inventory.log").read_text())

    def test_appends_and_closes_windows_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"test.log"
            inv.run(dry_run=True, api=self.api(), log_file=path, delay_seconds=0)
            inv.run(dry_run=True, api=self.api(), log_file=path, delay_seconds=0)
            self.assertEqual(path.read_text().count("SUCCESS mode=DRY_RUN"), 2)
            path.unlink()  # Windows will fail if handler was left open.

    def test_empty_customer_response_fails_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"log.txt"
            api = self.api()
            api.get_customers.return_value = []
            with self.assertRaises(RuntimeError):
                inv.run(dry_run=True, api=api, log_file=path, delay_seconds=0)
            self.assertIn("FAILED mode=DRY_RUN", path.read_text())

    def test_device_id_missing_fails_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                inv.run(dry_run=True, api=self.api([{"longName": "broken"}]),
                        log_file=Path(tmp)/"log.txt", delay_seconds=0)

    def test_live_persists_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = Mock()
            with patch.object(inv, "persist") as persist:
                result = inv.run(dry_run=False, api=self.api(), connect=lambda: conn,
                                 log_file=Path(tmp)/"log.txt", delay_seconds=0)
            self.assertEqual(result["written"], 1)
            persist.assert_called_once()
            conn.close.assert_called_once()

    def test_persist_upserts_and_commits(self):
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        row = (42, "Host", "Server", "Windows", 5, "HQ", "Example", 12, None, None)
        execute_values = Mock()
        with patch.dict(sys.modules, {"psycopg2": Mock(), "psycopg2.extras": SimpleNamespace(execute_values=execute_values)}):
            inv.persist(conn, 12, "Example", [row, row])
        execute_values.assert_called_once()
        self.assertEqual(len(execute_values.call_args.args[2]), 1)
        self.assertIn("ON CONFLICT (device_id)", execute_values.call_args.args[1])
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_persist_failure_rolls_back(self):
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        cursor.execute.side_effect = ValueError("database failure")
        with patch.dict(sys.modules, {"psycopg2": Mock(), "psycopg2.extras": SimpleNamespace(execute_values=Mock())}):
            with self.assertRaises(ValueError):
                inv.persist(conn, 12, "Example", [])
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_rate_limit_aborts_before_second_customer(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = self.api()
            api.get_customers.return_value.append({"orgUnitId": 13, "orgUnitName": "Other"})
            api.get_devices_for_org_unit.side_effect = RuntimeError("429 Too Many Requests")
            with self.assertRaises(RuntimeError):
                inv.run(dry_run=True, api=api, log_file=Path(tmp)/"log.txt", delay_seconds=0)
            self.assertEqual(api.get_devices_for_org_unit.call_count, 1)

    def test_customer_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = self.api()
            api.get_customers.return_value.insert(0, {"orgUnitId": 11, "orgUnitName": "Ignore"})
            inv.run(dry_run=True, api=api, customer_id=12, log_file=Path(tmp)/"log.txt", delay_seconds=0)
            api.get_devices_for_org_unit.assert_called_once_with(12)

    def test_bad_customer_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                inv.run(dry_run=True, api=self.api(), customer_id=99,
                        log_file=Path(tmp)/"log.txt", delay_seconds=0)


if __name__ == "__main__":
    unittest.main()
