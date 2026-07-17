import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class ConnectionTestQueueTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.account_id = database.create_broker_account("Test Account", mode="demo")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_defaults_to_none(self):
        self.assertIsNone(database.get_connection_test(self.account_id))

    def test_request_sets_pending(self):
        database.request_connection_test(self.account_id, "owner@example.com")
        pending = database.get_connection_test(self.account_id)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["requested_by"], "owner@example.com")

    def test_cannot_request_while_already_pending_for_the_same_account(self):
        database.request_connection_test(self.account_id, "owner@example.com")
        with self.assertRaises(ValueError):
            database.request_connection_test(self.account_id, "owner@example.com")

    def test_a_different_account_can_have_its_own_pending_test_at_the_same_time(self):
        other_account_id = database.create_broker_account("Other Account", mode="demo")
        database.request_connection_test(self.account_id, "owner@example.com")
        database.request_connection_test(other_account_id, "owner@example.com")  # must not raise
        self.assertEqual(database.get_connection_test(self.account_id)["status"], "pending")
        self.assertEqual(database.get_connection_test(other_account_id)["status"], "pending")

    def test_complete_stores_result(self):
        database.request_connection_test(self.account_id, "owner@example.com")
        database.complete_connection_test(self.account_id, {"balance": 1234.56})
        result = database.get_connection_test(self.account_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["balance"], 1234.56)

    def test_fail_stores_error(self):
        database.request_connection_test(self.account_id, "owner@example.com")
        database.fail_connection_test(self.account_id, "account is not connected")
        result = database.get_connection_test(self.account_id)
        self.assertEqual(result["status"], "error")
        self.assertIn("not connected", result["result"]["error"])

    def test_can_request_again_after_completion(self):
        database.request_connection_test(self.account_id, "owner@example.com")
        database.complete_connection_test(self.account_id, {"balance": 100.0})
        database.request_connection_test(self.account_id, "owner@example.com")  # must not raise
        self.assertEqual(database.get_connection_test(self.account_id)["status"], "pending")

    def test_list_pending_only_returns_pending_ones(self):
        other_account_id = database.create_broker_account("Other Account", mode="demo")
        database.request_connection_test(self.account_id, "owner@example.com")
        database.request_connection_test(other_account_id, "owner@example.com")
        database.complete_connection_test(other_account_id, {"balance": 50.0})

        pending = database.list_pending_connection_tests()
        pending_account_ids = {p["broker_account_id"] for p in pending}
        self.assertEqual(pending_account_ids, {self.account_id})


if __name__ == "__main__":
    unittest.main()
