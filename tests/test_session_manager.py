import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import session_manager


def _run(coro):
    return asyncio.run(coro)


class SessionManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_check_session_limits_noop_when_session_id_none(self):
        session_manager.check_session_limits(None)  # must not raise

    def test_check_session_limits_passes_under_all_thresholds(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=50, loss_limit=20, max_trades=5)
        session_manager.check_session_limits(session_id)  # must not raise
        self.assertEqual(database.get_trading_session(session_id)["status"], "active")

    def test_profit_target_reached_stops_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=50)
        database.update_session_pnl(session_id, 50)
        with self.assertRaises(session_manager.SessionLimitReached) as ctx:
            session_manager.check_session_limits(session_id)
        self.assertEqual(ctx.exception.rule, "session_profit_target")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_target")

    def test_loss_limit_breached_stops_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", loss_limit=20)
        database.update_session_pnl(session_id, -20)
        with self.assertRaises(session_manager.SessionLimitReached) as ctx:
            session_manager.check_session_limits(session_id)
        self.assertEqual(ctx.exception.rule, "session_loss_limit")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_loss_limit")

    def test_max_trades_reached_stops_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", max_trades=2)
        database.record_session_trade(session_id)
        database.record_session_trade(session_id)
        with self.assertRaises(session_manager.SessionLimitReached) as ctx:
            session_manager.check_session_limits(session_id)
        self.assertEqual(ctx.exception.rule, "session_max_trades")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_max_trades")

    def test_zero_thresholds_mean_disabled(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=0, loss_limit=0, max_trades=0)
        database.update_session_pnl(session_id, 100000)
        session_manager.check_session_limits(session_id)  # must not raise
        self.assertEqual(database.get_trading_session(session_id)["status"], "active")

    def test_channel_in_session_true_for_member_channel(self):
        session = {"channel_ids": [1, 2, 3]}
        self.assertTrue(session_manager.channel_in_session(session, {"id": 2}))
        self.assertFalse(session_manager.channel_in_session(session, {"id": 99}))

    def test_channel_in_session_false_when_session_or_channel_none(self):
        self.assertFalse(session_manager.channel_in_session(None, {"id": 1}))
        self.assertFalse(session_manager.channel_in_session({"channel_ids": [1]}, None))

    def test_record_trade_started_noop_when_session_id_none(self):
        session_manager.record_trade_started(None)  # must not raise

    def test_on_trade_closed_updates_pnl_and_reevaluates_limits(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=10)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)

        _run(session_manager._on_trade_closed({"trade_id": trade_id, "profit_loss": 12.0}))

        session = database.get_trading_session(session_id)
        self.assertEqual(session["realized_pnl"], 12.0)
        self.assertEqual(session["status"], "stopped_target")

    def test_on_trade_closed_ignores_trade_with_no_session(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal)  # no session_id
        _run(session_manager._on_trade_closed({"trade_id": trade_id, "profit_loss": 5.0}))  # must not raise

    def test_on_trade_closed_ignores_missing_profit_loss(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        _run(session_manager._on_trade_closed({"trade_id": trade_id, "profit_loss": None}))
        self.assertEqual(database.get_trading_session(session_id)["realized_pnl"], 0)


if __name__ == "__main__":
    unittest.main()
