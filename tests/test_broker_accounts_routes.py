import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import broker_accounts_routes as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class WithFundsActiveSessionTestCase(unittest.TestCase):
    """Regression coverage for the Broker Accounts UI polish (2026-07-16):
    the "Used by" column should indicate which assigned Fund actually has
    a session running right now, not just which Funds are assigned."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.account_id = database.create_broker_account("Test Account", mode="demo")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_fund_with_no_active_session_is_flagged_false(self):
        fund_id = database.create_fund("Idle Fund")
        database.assign_broker_account_to_fund(fund_id, self.account_id)
        result = routes.get_broker_account(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(len(result["funds"]), 1)
        self.assertFalse(result["funds"][0]["has_active_session"])

    def test_fund_with_an_active_session_is_flagged_true(self):
        fund_id = database.create_fund("Trading Fund")
        database.assign_broker_account_to_fund(fund_id, self.account_id)
        database.start_trading_session(
            "Trading Fund - Live Signals", [1], "DEMO", fund_id=fund_id, broker_account_id=self.account_id,
        )
        result = routes.get_broker_account(self.account_id, user=_FAKE_ADMIN)
        self.assertTrue(result["funds"][0]["has_active_session"])

    def test_account_with_no_funds_at_all_does_not_crash(self):
        result = routes.get_broker_account(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(result["funds"], [])


class TestConnectionRouteTestCase(unittest.TestCase):
    """Distinct from both /connect (the full login flow) and the Broker
    page's Test Trade (places a real Demo trade) - verifies an already-
    connected account's session is genuinely still responsive without
    ever submitting an order. Actual verification happens in
    core/telegram_listener.py's poll loop (a separate process); this
    covers the API layer's request/status surface."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.account_id = database.create_broker_account("Test Account", mode="demo")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_disconnected_account_is_rejected_with_a_clear_reason(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            routes.test_broker_account_connection(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("not connected", ctx.exception.detail)

    def test_connected_account_can_be_tested_and_polled(self):
        database.update_broker_account(self.account_id, connection_status="connected")
        result = routes.test_broker_account_connection(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(result["status"], "pending")

        status = routes.get_broker_account_connection_test(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(status["status"], "pending")

        database.complete_connection_test(self.account_id, {"balance": 500.0})
        status = routes.get_broker_account_connection_test(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["result"]["balance"], 500.0)

    def test_cannot_start_a_second_test_while_one_is_already_pending(self):
        from fastapi import HTTPException
        database.update_broker_account(self.account_id, connection_status="connected")
        routes.test_broker_account_connection(self.account_id, user=_FAKE_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            routes.test_broker_account_connection(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_no_test_ever_requested_reports_status_none(self):
        database.update_broker_account(self.account_id, connection_status="connected")
        status = routes.get_broker_account_connection_test(self.account_id, user=_FAKE_ADMIN)
        self.assertEqual(status["status"], "none")


if __name__ == "__main__":
    unittest.main()
