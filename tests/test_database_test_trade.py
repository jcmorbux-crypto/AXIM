import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class TestTradeQueueTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_defaults_to_none(self):
        pending = database.get_pending_test_trade()
        self.assertEqual(pending["status"], "none")
        self.assertIsNone(pending["result"])

    def test_request_sets_pending(self):
        database.request_test_trade("owner@example.com")
        pending = database.get_pending_test_trade()
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["requested_by"], "owner@example.com")

    def test_cannot_request_while_already_pending(self):
        database.request_test_trade("owner@example.com")
        with self.assertRaises(ValueError):
            database.request_test_trade("owner@example.com")

    def test_complete_stores_result(self):
        database.request_test_trade("owner@example.com")
        database.complete_test_trade({"status": "clicked", "trade_id": 42})
        pending = database.get_pending_test_trade()
        self.assertEqual(pending["status"], "completed")
        self.assertEqual(pending["result"]["trade_id"], 42)

    def test_fail_stores_error(self):
        database.request_test_trade("owner@example.com")
        database.fail_test_trade("ACCOUNT is not DEMO")
        pending = database.get_pending_test_trade()
        self.assertEqual(pending["status"], "error")
        self.assertIn("ACCOUNT", pending["result"]["error"])

    def test_can_request_again_after_completion(self):
        database.request_test_trade("owner@example.com")
        database.complete_test_trade({"status": "clicked"})
        database.request_test_trade("owner@example.com")  # must not raise
        self.assertEqual(database.get_pending_test_trade()["status"], "pending")


if __name__ == "__main__":
    unittest.main()
