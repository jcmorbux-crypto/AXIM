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
import funds_routes as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class ArchiveFundTestCase(unittest.TestCase):
    """Regression coverage for a real production finding (2026-07-16):
    archiving a Fund left a forgotten active session silently occupying
    its broker account's exclusivity slot for 4 days, since nothing
    stopped the session when the Fund was archived."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.broker_account_id = database.create_broker_account("Pocket Option Demo", mode="demo")
        self.fund_id = database.create_fund("Test Fund", starting_balance=1000.0, live_enabled=True)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_archiving_a_fund_with_no_active_session_just_archives_it(self):
        result = routes.archive_fund(self.fund_id, user=_FAKE_ADMIN)
        self.assertEqual(result["fund"]["status"], "archived")

    def test_archiving_a_fund_stops_its_dangling_active_session(self):
        session_id = database.start_trading_session(
            "Test Fund - Live Signals", [1], "DEMO", fund_id=self.fund_id,
            broker_account_id=self.broker_account_id,
        )

        routes.archive_fund(self.fund_id, user=_FAKE_ADMIN)

        self.assertIsNone(database.get_active_trading_session_for_fund(self.fund_id))
        self.assertIsNone(database.get_active_trading_session_for_broker_account(self.broker_account_id))
        stopped = database.get_trading_session(session_id)
        self.assertEqual(stopped["status"], "stopped_manual")
        self.assertIsNotNone(stopped["ended_at"])

    def test_archiving_a_fund_clears_the_live_enabled_flag(self):
        routes.archive_fund(self.fund_id, user=_FAKE_ADMIN)
        fund = database.get_fund(self.fund_id)
        self.assertEqual(fund["live_enabled"], 0)

    def test_archiving_does_not_touch_an_unrelated_funds_active_session(self):
        other_broker = database.create_broker_account("Other Broker", mode="demo")
        other_fund_id = database.create_fund("Other Fund", starting_balance=500.0)
        other_session_id = database.start_trading_session(
            "Other Fund - Live Signals", [2], "DEMO", fund_id=other_fund_id, broker_account_id=other_broker,
        )

        routes.archive_fund(self.fund_id, user=_FAKE_ADMIN)

        self.assertIsNotNone(database.get_active_trading_session_for_fund(other_fund_id))
        still_active = database.get_trading_session(other_session_id)
        self.assertEqual(still_active["status"], "active")


if __name__ == "__main__":
    unittest.main()
