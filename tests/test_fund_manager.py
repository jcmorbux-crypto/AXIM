import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import fund_manager
import trade_lifecycle


def _make_closed_trade(session_id, result="win", profit_loss=8.5, trade_amount=10):
    signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
              "trade_amount": trade_amount}
    trade_id = database.record_signal_received(signal, session_id=session_id)
    status = trade_lifecycle.TradeStatus.RESULT_WIN if result == "win" else trade_lifecycle.TradeStatus.RESULT_LOSS
    database.update_trade_status(trade_id, status, result=result, profit_loss=profit_loss,
                                  closed_at=datetime.now().isoformat())
    return trade_id


class FundManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class BalanceComputationTests(FundManagerTestCase):
    def test_no_sessions_returns_starting_balance_only(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["starting_balance"], 1000)
        self.assertEqual(balances["trading_balance"], 1000)
        self.assertEqual(balances["protected_balance"], 0)
        self.assertEqual(balances["total_account_value"], 1000)

    def test_missing_fund_returns_none(self):
        self.assertIsNone(fund_manager.get_fund_balances(999999))

    def test_realized_pnl_flows_into_trading_balance(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_id, 50)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1050)
        self.assertEqual(balances["total_account_value"], 1050)

    def test_vaulted_amount_moves_from_trading_to_protected(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_id, 100)
        database.add_to_vault(session_id, 20)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["protected_balance"], 20)
        self.assertEqual(balances["trading_balance"], 1080)  # 1000 + 100 - 20
        self.assertEqual(balances["total_account_value"], 1100)  # vaulting doesn't destroy value

    def test_multiple_sessions_accumulate(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        s1 = database.start_trading_session("S1", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(s1, 50)
        database.stop_trading_session(s1, "stopped_manual")
        s2 = database.start_trading_session("S2", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(s2, 30)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1080)

    def test_unrelated_sessions_not_counted(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        database.start_trading_session("No fund", [1], "DEMO")  # not attributed to any fund
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1000)


class PerformanceTests(FundManagerTestCase):
    def test_no_trades_returns_empty_summary(self):
        fund_id = database.create_fund("F")
        perf = fund_manager.get_fund_performance(fund_id)
        self.assertEqual(perf["total_closed"], 0)
        self.assertIsNone(perf["win_rate"])

    def test_performance_reflects_real_trades(self):
        fund_id = database.create_fund("F")
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=8.5)
        _make_closed_trade(session_id, result="loss", profit_loss=-10)
        perf = fund_manager.get_fund_performance(fund_id)
        self.assertEqual(perf["total_closed"], 2)
        self.assertEqual(perf["wins"], 1)
        self.assertEqual(perf["losses"], 1)
        self.assertAlmostEqual(perf["profit_loss"], -1.5)

    def test_trades_from_other_fund_excluded(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        session_a = database.start_trading_session("SA", [1], "DEMO", fund_id=fund_a)
        _make_closed_trade(session_a, result="win", profit_loss=8.5)
        perf_b = fund_manager.get_fund_performance(fund_b)
        self.assertEqual(perf_b["total_closed"], 0)


class FundReportTests(FundManagerTestCase):
    def test_full_report_shape(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        database.add_fund_source(fund_id, 1)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=8.5)

        report = fund_manager.get_fund_report(fund_id)
        self.assertEqual(report["fund"]["name"], "F")
        self.assertIn("balances", report)
        self.assertIn("performance", report)
        self.assertEqual(report["sources"], [1])
        self.assertEqual(len(report["recent_sessions"]), 1)

    def test_missing_fund_returns_none(self):
        self.assertIsNone(fund_manager.get_fund_report(999999))


class ListFundsWithBalancesTests(FundManagerTestCase):
    def test_lists_all_with_balances_attached(self):
        f1 = database.create_fund("F1", starting_balance=500)
        f2 = database.create_fund("F2", starting_balance=1000, status="paused")
        results = fund_manager.list_funds_with_balances()
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn("balances", r)

    def test_filters_by_status(self):
        database.create_fund("Active", status="active")
        database.create_fund("Paused", status="paused")
        results = fund_manager.list_funds_with_balances(status="active")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Active")


class CanTradeTests(FundManagerTestCase):
    """The safety gate api/sessions.py's start_session enforces - "Do not
    let a Fund trade unless it has a valid broker account attached" plus
    the two independent Live switches (Fund and Broker Account)."""

    def test_missing_fund(self):
        allowed, reason, can_go_live = fund_manager.can_trade(999999)
        self.assertFalse(allowed)
        self.assertFalse(can_go_live)

    def test_no_broker_account_attached(self):
        fund_id = database.create_fund("F1")
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("Broker account not connected", reason)
        self.assertFalse(can_go_live)

    def test_broker_account_attached_but_not_connected(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1")
        database.assign_broker_account_to_fund(fund_id, account_id)
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("not connected", reason)

    def test_broker_account_disabled(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1")
        database.update_broker_account(account_id, connection_status="connected", status="disabled")
        database.assign_broker_account_to_fund(fund_id, account_id)
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("disabled", reason)

    def test_connected_account_allows_trade_but_not_live_by_default(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1", mode="both")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertTrue(allowed)
        self.assertIsNone(reason)
        self.assertFalse(can_go_live)  # neither live_enabled flag set yet

    def test_live_requires_both_fund_and_account_flags(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1", mode="both")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)

        # Only the account flag set - not enough.
        database.update_broker_account(account_id, live_enabled=1)
        _, _, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(can_go_live)

        # Only the fund flag set - still not enough.
        database.update_broker_account(account_id, live_enabled=0)
        database.update_fund(fund_id, live_enabled=1)
        _, _, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(can_go_live)

        # Both set - now allowed.
        database.update_broker_account(account_id, live_enabled=1)
        _, _, can_go_live = fund_manager.can_trade(fund_id)
        self.assertTrue(can_go_live)

    def test_paused_fund_cannot_trade(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1", mode="both")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        database.update_fund(fund_id, status="paused")
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("paused", reason)
        self.assertFalse(can_go_live)

    def test_archived_fund_cannot_trade(self):
        fund_id = database.create_fund("F1")
        database.update_fund(fund_id, status="archived")
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("archived", reason)

    def test_live_requires_account_mode_supports_live(self):
        """A "demo"-only account can't go live even with both flags on -
        live_enabled is a permission switch, not a capability override."""
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected", live_enabled=1)
        database.assign_broker_account_to_fund(fund_id, account_id)
        database.update_fund(fund_id, live_enabled=1)
        _, _, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(can_go_live)

    def _connected_fund(self, loss_limit=0, max_trades=0):
        fund_id = database.create_fund("F1", loss_limit=loss_limit, max_trades=max_trades)
        account_id = database.create_broker_account("Acc1", mode="both")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        return fund_id

    def _pending_trade(self, fund_id, trade_amount, session_id=None):
        # Mirrors execution/pocket_executor.py's real sequence: a stake is
        # locked in (trade_amount set) at TRADE_CLICKED, well before the
        # trade's expiry resolves it to a result. Reuses a caller-given
        # session_id when placing more than one pending trade for the
        # same fund - start_trading_session enforces one active session
        # per broker account.
        if session_id is None:
            session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id, fund_id=fund_id)
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.TRADE_CLICKED, trade_amount=trade_amount)
        return trade_id, session_id

    def test_lifetime_loss_limit_has_no_proactive_check_when_no_pending_exposure(self):
        # Baseline: closed-only P/L within the limit and nothing pending
        # must still allow trading - confirms the new check doesn't
        # change behavior for the common case.
        fund_id = self._connected_fund(loss_limit=20)
        allowed, reason, _ = fund_manager.can_trade(fund_id)
        self.assertTrue(allowed)

    def test_lifetime_loss_limit_blocked_by_pending_exposure_alone(self):
        # check_fund_limits (reactive, on trade close) never ran yet -
        # there is no closed trade at all - but a burst of signals could
        # otherwise still get accepted purely because nothing proactively
        # checked the fund's own lifetime limit before this fix.
        fund_id = self._connected_fund(loss_limit=20)
        self._pending_trade(fund_id, trade_amount=25)
        allowed, reason, can_go_live = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("lifetime loss limit", reason)
        self.assertFalse(can_go_live)
        # Read-only: no mutation, unlike check_fund_limits' actual breach handling.
        self.assertEqual(database.get_fund(fund_id)["status"], "active")

    def test_lifetime_loss_limit_pending_exposure_within_threshold_allows_trade(self):
        fund_id = self._connected_fund(loss_limit=20)
        self._pending_trade(fund_id, trade_amount=5)
        allowed, _, _ = fund_manager.can_trade(fund_id)
        self.assertTrue(allowed)

    def test_lifetime_max_trades_counts_pending_trades(self):
        fund_id = self._connected_fund(max_trades=2)
        _, session_id = self._pending_trade(fund_id, trade_amount=5)
        self._pending_trade(fund_id, trade_amount=5, session_id=session_id)
        allowed, reason, _ = fund_manager.can_trade(fund_id)
        self.assertFalse(allowed)
        self.assertIn("lifetime max trades", reason)
        self.assertEqual(database.get_fund(fund_id)["status"], "active")

    def test_pending_exposure_from_a_different_fund_is_not_counted(self):
        fund_a = self._connected_fund(loss_limit=20)
        fund_b = self._connected_fund(loss_limit=20)
        self._pending_trade(fund_b, trade_amount=1000)  # someone else's exposure
        allowed, _, _ = fund_manager.can_trade(fund_a)
        self.assertTrue(allowed)


class CheckFundLimitsTests(FundManagerTestCase):
    """A Fund's own profit_target/loss_limit/max_trades are a lifetime
    circuit breaker on that Fund's bankroll - separate from, and
    measured differently than, any individual session's own (resettable)
    limits."""

    def test_no_limits_set_is_a_noop(self):
        fund_id = database.create_fund("F", profit_target=0, loss_limit=0, max_trades=0)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=100000)
        fund_manager.check_fund_limits(fund_id)  # must not raise
        self.assertEqual(database.get_fund(fund_id)["status"], "active")

    def test_loss_limit_breach_pauses_fund_and_stops_session(self):
        fund_id = database.create_fund("F", loss_limit=50)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="loss", profit_loss=-60)
        with self.assertRaises(fund_manager.FundLimitReached) as ctx:
            fund_manager.check_fund_limits(fund_id)
        self.assertEqual(ctx.exception.rule, "fund_loss_limit")
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_fund_loss_limit")

    def test_profit_target_breach_pauses_fund_and_stops_session(self):
        fund_id = database.create_fund("F", profit_target=50)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=60)
        with self.assertRaises(fund_manager.FundLimitReached) as ctx:
            fund_manager.check_fund_limits(fund_id)
        self.assertEqual(ctx.exception.rule, "fund_profit_target")
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_fund_target")

    def test_max_trades_breach_pauses_fund(self):
        fund_id = database.create_fund("F", max_trades=2)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=1)
        _make_closed_trade(session_id, result="loss", profit_loss=-1)
        with self.assertRaises(fund_manager.FundLimitReached) as ctx:
            fund_manager.check_fund_limits(fund_id)
        self.assertEqual(ctx.exception.rule, "fund_max_trades")
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")

    def test_limit_is_lifetime_across_multiple_sessions(self):
        """The whole point of a fund-level (vs session-level) limit -
        losses accumulated across two SEPARATE sessions must still trip
        it, even though neither session's own realized_pnl alone would."""
        fund_id = database.create_fund("F", loss_limit=50)
        s1 = database.start_trading_session("S1", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(s1, result="loss", profit_loss=-30)
        database.stop_trading_session(s1, "stopped_manual")
        fund_manager.check_fund_limits(fund_id)  # -30, not yet breached

        s2 = database.start_trading_session("S2", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(s2, result="loss", profit_loss=-30)
        with self.assertRaises(fund_manager.FundLimitReached):
            fund_manager.check_fund_limits(fund_id)
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")

    def test_no_active_session_still_pauses_fund(self):
        """Fund limits can trip even if checked with no currently active
        session (e.g. a rule/manual re-check) - the fund still gets
        paused, there's just no session to also stop."""
        fund_id = database.create_fund("F", loss_limit=50)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="loss", profit_loss=-60)
        database.stop_trading_session(session_id, "stopped_manual")
        with self.assertRaises(fund_manager.FundLimitReached):
            fund_manager.check_fund_limits(fund_id)
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")

    def test_missing_fund_is_a_noop(self):
        fund_manager.check_fund_limits(999999)  # must not raise

    def test_already_paused_fund_is_a_noop(self):
        fund_id = database.create_fund("F", loss_limit=1)
        database.update_fund(fund_id, status="paused")
        fund_manager.check_fund_limits(fund_id)  # must not raise or re-evaluate


if __name__ == "__main__":
    unittest.main()
