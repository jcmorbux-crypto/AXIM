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


if __name__ == "__main__":
    unittest.main()
