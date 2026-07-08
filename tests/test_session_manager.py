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


class TradeConfirmationGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._original_timeout = session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = self._original_timeout

    def _make_signal_trade_id(self, session_id):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        return database.record_signal_received(signal, session_id=session_id)

    def test_noop_when_session_id_none(self):
        _run(session_manager.wait_for_trade_confirmation(1, None, "EUR/USD", "BUY", "1 Minute", 10))
        self.assertEqual(database.list_pending_trade_confirmations(), [])

    def test_noop_when_require_confirmation_false(self):
        session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=False)
        trade_id = self._make_signal_trade_id(session_id)
        _run(session_manager.wait_for_trade_confirmation(trade_id, session_id, "EUR/USD", "BUY", "1 Minute", 10))
        self.assertEqual(database.list_pending_trade_confirmations(), [])

    def test_noop_when_demo_mode_even_if_required(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", require_confirmation=True)
        trade_id = self._make_signal_trade_id(session_id)
        _run(session_manager.wait_for_trade_confirmation(trade_id, session_id, "EUR/USD", "BUY", "1 Minute", 10))
        self.assertEqual(database.list_pending_trade_confirmations(), [])

    def test_noop_when_session_missing(self):
        _run(session_manager.wait_for_trade_confirmation(1, 999999, "EUR/USD", "BUY", "1 Minute", 10))
        self.assertEqual(database.list_pending_trade_confirmations(), [])

    def test_gates_and_creates_pending_row_when_live_and_required(self):
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 5

        async def _confirm_after_delay(trade_id):
            await asyncio.sleep(0.1)
            confirmed = database.decide_trade_confirmation(trade_id, "confirmed", decided_by="tester@axim.local")
            self.assertTrue(confirmed)

        async def _scenario():
            session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
            trade_id = self._make_signal_trade_id(session_id)
            await asyncio.gather(
                session_manager.wait_for_trade_confirmation(trade_id, session_id, "EUR/USD", "BUY", "1 Minute", 10),
                _confirm_after_delay(trade_id),
            )
            return trade_id

        trade_id = _run(_scenario())
        row = database.get_pending_trade_confirmation(trade_id)
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["decided_by"], "tester@axim.local")

    def test_raises_on_explicit_reject(self):
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 5

        async def _reject_after_delay(trade_id):
            await asyncio.sleep(0.1)
            database.decide_trade_confirmation(trade_id, "rejected", decided_by="tester@axim.local")

        async def _scenario():
            session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
            trade_id = self._make_signal_trade_id(session_id)
            await asyncio.gather(
                session_manager.wait_for_trade_confirmation(trade_id, session_id, "EUR/USD", "BUY", "1 Minute", 10),
                _reject_after_delay(trade_id),
            )

        with self.assertRaises(session_manager.TradeNotConfirmed) as ctx:
            _run(_scenario())
        self.assertEqual(ctx.exception.rule, "trade_not_confirmed")
        self.assertIn("tester@axim.local", ctx.exception.reason)

    def test_fails_closed_on_timeout(self):
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 0.3  # never answered
        trade_ids = []

        async def _scenario():
            session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
            trade_id = self._make_signal_trade_id(session_id)
            trade_ids.append(trade_id)
            await session_manager.wait_for_trade_confirmation(trade_id, session_id, "EUR/USD", "BUY", "1 Minute", 10)

        with self.assertRaises(session_manager.TradeNotConfirmed) as ctx:
            _run(_scenario())
        self.assertEqual(ctx.exception.rule, "trade_not_confirmed")
        self.assertIn("no confirmation within", ctx.exception.reason)
        row = database.get_pending_trade_confirmation(trade_ids[0])
        self.assertEqual(row["status"], "expired")
        self.assertEqual(database.list_pending_trade_confirmations(), [])


if __name__ == "__main__":
    unittest.main()
