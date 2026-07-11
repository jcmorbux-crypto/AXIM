"""Trade Center's signals table stores fund_id/broker_account_id on every
signal but never surfaced the Fund/broker account name anywhere in the
API response - the AXIM Core directive explicitly lists Fund and broker
account as required Trade Center columns. get_recent_signals/
get_signal_detail now join in the human-readable names."""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class TradeCenterFundBrokerColumnsTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self.fund_id = database.create_fund("Test Fund", starting_balance=1000)
        self.broker_account_id = database.create_broker_account("Test Broker Account", mode="demo")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _signal(self):
        return {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}

    def test_get_recent_signals_includes_fund_and_broker_account_names(self):
        database.record_signal_received(
            self._signal(), fund_id=self.fund_id, broker_account_id=self.broker_account_id,
        )
        recent = database.get_recent_signals(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["fund_id"], self.fund_id)
        self.assertEqual(recent[0]["broker_account_id"], self.broker_account_id)
        self.assertEqual(recent[0]["fund_name"], "Test Fund")
        self.assertEqual(recent[0]["broker_account_name"], "Test Broker Account")

    def test_get_recent_signals_names_are_none_when_not_assigned(self):
        database.record_signal_received(self._signal())
        recent = database.get_recent_signals(limit=10)
        self.assertIsNone(recent[0]["fund_name"])
        self.assertIsNone(recent[0]["broker_account_name"])

    def test_get_signal_detail_includes_full_fund_and_broker_account_objects(self):
        trade_id = database.record_signal_received(
            self._signal(), fund_id=self.fund_id, broker_account_id=self.broker_account_id,
        )
        detail = database.get_signal_detail(trade_id)
        self.assertIsNotNone(detail["fund"])
        self.assertEqual(detail["fund"]["name"], "Test Fund")
        self.assertIsNotNone(detail["broker_account"])
        self.assertEqual(detail["broker_account"]["name"], "Test Broker Account")

    def test_get_signal_detail_fund_and_broker_account_are_none_when_not_assigned(self):
        trade_id = database.record_signal_received(self._signal())
        detail = database.get_signal_detail(trade_id)
        self.assertIsNone(detail["fund"])
        self.assertIsNone(detail["broker_account"])


if __name__ == "__main__":
    unittest.main()
