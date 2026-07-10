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

    def test_record_trade_started_raises_once_cap_reached(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", max_trades=1)
        session_manager.record_trade_started(session_id)  # consumes the only slot
        with self.assertRaises(session_manager.SessionLimitReached) as ctx:
            session_manager.record_trade_started(session_id)
        self.assertEqual(ctx.exception.rule, "session_max_trades")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_max_trades")
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 1)

    def test_record_trade_started_unlimited_when_max_trades_zero(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", max_trades=0)
        for _ in range(5):
            session_manager.record_trade_started(session_id)  # must never raise
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 5)

    def test_concurrent_record_trade_started_never_exceeds_max_trades(self):
        """check_session_limits() is checked once, well before this - and
        when require_confirmation makes trade_coordinator.py wait on a
        human in between, that gap can be wide enough for several signals
        to all pass the earlier check before any of them reaches this
        call. Proves record_trade_started's atomic increment is the real
        enforcement point: even with 10 threads racing a session capped
        at 3 trades, trades_count never exceeds 3."""
        import threading
        session_id = database.start_trading_session("Test", [1], "DEMO", max_trades=3)
        results = []

        def attempt():
            try:
                session_manager.record_trade_started(session_id)
                results.append("ok")
            except session_manager.SessionLimitReached:
                results.append("rejected")

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count("ok"), 3)
        self.assertEqual(results.count("rejected"), 7)
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 3)

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

    def test_get_active_session_for_channel_routes_to_the_right_session(self):
        account_a = database.create_broker_account("Acct A")
        account_b = database.create_broker_account("Acct B")
        session_a = database.start_trading_session("SA", [1, 2], "DEMO", broker_account_id=account_a)
        session_b = database.start_trading_session("SB", [3], "DEMO", broker_account_id=account_b)
        self.assertEqual(session_manager.get_active_session_for_channel({"id": 2})["id"], session_a)
        self.assertEqual(session_manager.get_active_session_for_channel({"id": 3})["id"], session_b)
        self.assertIsNone(session_manager.get_active_session_for_channel({"id": 999}))

    def test_get_active_session_for_channel_none_when_channel_row_none(self):
        database.start_trading_session("Test", [1], "DEMO")
        self.assertIsNone(session_manager.get_active_session_for_channel(None))

    def test_end_session_deletes_its_own_session_scoped_rules(self):
        fund_id = database.create_fund("F1")
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        rule_id = database.create_rule(
            "Temp override", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {},
            fund_id=fund_id, scope="session", session_id=session_id,
        )
        fund_wide_rule_id = database.create_rule(
            "Permanent", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {},
            fund_id=fund_id, scope="fund",
        )
        session_manager.end_session(session_id, "stopped_manual")
        self.assertIsNone(database.get_rule(rule_id))
        self.assertIsNotNone(database.get_rule(fund_wide_rule_id))

    def test_end_all_active_sessions_stops_every_fund_not_just_one(self):
        """Emergency Stop must mark EVERY currently active session
        stopped, not just one Fund's - api/main.py's global
        POST /api/control/emergency-stop (the route Mission Control's
        button actually calls) used to only flip control-state flags,
        leaving every active session stuck showing "active" in the DB."""
        account_a = database.create_broker_account("Acct A")
        account_b = database.create_broker_account("Acct B")
        session_a = database.start_trading_session("A", [1], "DEMO", broker_account_id=account_a)
        session_b = database.start_trading_session("B", [2], "DEMO", broker_account_id=account_b)

        session_manager.end_all_active_sessions("stopped_emergency", "test")

        self.assertEqual(database.get_trading_session(session_a)["status"], "stopped_emergency")
        self.assertEqual(database.get_trading_session(session_b)["status"], "stopped_emergency")
        self.assertEqual(len(database.list_active_trading_sessions()), 0)

    def test_end_all_active_sessions_noop_when_none_active(self):
        session_manager.end_all_active_sessions("stopped_emergency", "test")  # must not raise

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

    def test_on_trade_closed_also_checks_the_sessions_fund_limits(self):
        import trade_lifecycle
        fund_id = database.create_fund("F", loss_limit=10)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        # get_fund_performance (which check_fund_limits reads) only counts
        # trades with a real result - matching production, where the
        # trade's outcome is recorded before the "trade.closed" event
        # this handler responds to is ever published.
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.RESULT_LOSS,
                                      result="loss", profit_loss=-15.0)

        _run(session_manager._on_trade_closed({"trade_id": trade_id, "profit_loss": -15.0}))

        self.assertEqual(database.get_fund(fund_id)["status"], "paused")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_fund_loss_limit")

    def test_on_trade_closed_skips_fund_check_when_session_has_no_fund(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        _run(session_manager._on_trade_closed({"trade_id": trade_id, "profit_loss": -1000.0}))  # must not raise

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
