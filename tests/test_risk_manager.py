import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import risk_manager
from trade_lifecycle import TradeStatus


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _insert_signal(self, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute",
                        result=None, closed_at=None, profit_loss=None, broker_account_id=None):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"},
            broker_account_id=broker_account_id,
        )
        if result:
            database.update_trade_status(
                trade_id, TradeStatus.TRADE_CLOSED,
                result=result, closed_at=closed_at or datetime.now().isoformat(),
                profit_loss=profit_loss,
            )
        return trade_id

    def _insert_pending_signal(self, trade_amount, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute"):
        # Mirrors execution/pocket_executor.py's real sequence: a stake is
        # locked in (trade_amount set) at TRADE_CLICKED, well before the
        # trade's expiry resolves it to a result.
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"},
        )
        database.update_trade_status(trade_id, TradeStatus.TRADE_CLICKED, trade_amount=trade_amount)
        return trade_id

    def test_duplicate_signal_detected_within_window(self):
        self._insert_signal()
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_duplicate_signal("EUR/USD OTC", "BUY", "1 Minute")
        self.assertEqual(ctx.exception.rule, "duplicate_signal")

    def test_duplicate_signal_excludes_self(self):
        trade_id = self._insert_signal()
        risk_manager.check_duplicate_signal("EUR/USD OTC", "BUY", "1 Minute", exclude_id=trade_id)

    def test_duplicate_signal_different_direction_not_flagged(self):
        self._insert_signal(direction="BUY")
        risk_manager.check_duplicate_signal("EUR/USD OTC", "SELL", "1 Minute")

    def test_max_trade_amount_over_limit(self):
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_trade_amount(1000)
        self.assertEqual(ctx.exception.rule, "max_trade_amount")

    def test_max_trade_amount_within_limit(self):
        risk_manager.check_max_trade_amount(1)

    def test_max_trades_per_hour(self):
        for _ in range(risk_manager.MAX_TRADES_PER_HOUR):
            self._insert_signal()
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_trades_per_hour()
        self.assertEqual(ctx.exception.rule, "max_trades_per_hour")

    def test_max_trades_per_hour_is_scoped_per_broker_account(self):
        # A busy account must not exhaust the quota for a DIFFERENT
        # account sharing the same configured limit - each gets its own
        # independent count.
        account_a = database.create_broker_account("Account A")
        account_b = database.create_broker_account("Account B")
        for _ in range(risk_manager.MAX_TRADES_PER_HOUR):
            self._insert_signal(broker_account_id=account_a)
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_trades_per_hour(account_a)
        risk_manager.check_max_trades_per_hour(account_b)  # must not raise - a separate quota

    def test_max_trades_per_hour_with_no_account_id_still_counts_globally(self):
        # The legacy session_id=None path (no Fund/account attached) has
        # no account to scope to - unchanged, still counts every trade.
        for _ in range(risk_manager.MAX_TRADES_PER_HOUR):
            self._insert_signal()
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_trades_per_hour()

    def test_max_consecutive_losses(self):
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_signal(result="loss")
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_consecutive_losses()
        self.assertEqual(ctx.exception.rule, "max_consecutive_losses")

    def test_consecutive_losses_broken_by_win(self):
        self._insert_signal(result="loss")
        self._insert_signal(result="win")
        self._insert_signal(result="loss")
        risk_manager.check_max_consecutive_losses()

    def test_consecutive_losses_pending_trades_count_pessimistically(self):
        # A burst of signals arriving within one expiry window would
        # otherwise all read the same closed-only recent-results and all
        # pass this check before any of them resolve - execution/
        # pocket_executor.py's track_outcome docstring documents this as
        # a deliberate throughput trade-off (MAX_CONCURRENT_WORKERS bounds
        # placements, not open positions). One real loss short of the
        # limit, plus one still-open trade, must already block.
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES - 1):
            self._insert_signal(result="loss")
        self._insert_pending_signal(trade_amount=10)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_consecutive_losses()
        self.assertEqual(ctx.exception.rule, "max_consecutive_losses")

    def test_consecutive_losses_enough_pending_trades_alone_blocks(self):
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_pending_signal(trade_amount=10)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_consecutive_losses()
        self.assertEqual(ctx.exception.rule, "max_consecutive_losses")

    def test_consecutive_losses_a_real_win_still_breaks_the_pessimistic_streak(self):
        # MAX_CONSECUTIVE_LOSSES=3: 2 real losses, then a real win (breaks
        # the streak), then 1 pending trade. remaining=3-1=2 most recent
        # closed results are [win, loss] - not all losses, must not raise.
        self._insert_signal(result="loss")
        self._insert_signal(result="loss")
        self._insert_signal(result="win")
        self._insert_pending_signal(trade_amount=10)
        risk_manager.check_max_consecutive_losses()

    def test_reset_consecutive_loss_lock_clears_a_real_lock(self):
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_signal(result="loss")
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_consecutive_losses()

        risk_manager.reset_consecutive_loss_lock(reset_by="owner@axim.local")
        risk_manager.check_max_consecutive_losses()  # must not raise anymore

    def test_reset_does_not_touch_the_configured_limit(self):
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_signal(result="loss")
        risk_manager.reset_consecutive_loss_lock(reset_by="owner@axim.local")
        self.assertEqual(database.get_setting("max_consecutive_losses", default=risk_manager.MAX_CONSECUTIVE_LOSSES),
                          risk_manager.MAX_CONSECUTIVE_LOSSES)

    def test_reset_does_not_erase_the_real_losses_a_new_streak_after_it_still_locks(self):
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_signal(result="loss")
        risk_manager.reset_consecutive_loss_lock(reset_by="owner@axim.local")
        for _ in range(risk_manager.MAX_CONSECUTIVE_LOSSES):
            self._insert_signal(result="loss")
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_consecutive_losses()

    def test_reset_requires_an_attributable_actor(self):
        with self.assertRaises(ValueError):
            risk_manager.reset_consecutive_loss_lock(reset_by=None)

    def test_cooldown_after_loss_blocks(self):
        if risk_manager.COOLDOWN_AFTER_LOSS_SECONDS <= 0:
            self.skipTest("COOLDOWN_AFTER_LOSS_SECONDS is 0 - cooldown intentionally disabled")
        self._insert_signal(result="loss", closed_at=datetime.now().isoformat())
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_cooldown_after_loss()
        self.assertEqual(ctx.exception.rule, "cooldown_after_loss")

    def test_cooldown_after_loss_expired(self):
        old_time = (datetime.now() - timedelta(
            seconds=risk_manager.COOLDOWN_AFTER_LOSS_SECONDS + 10
        )).isoformat()
        self._insert_signal(result="loss", closed_at=old_time)
        risk_manager.check_cooldown_after_loss()

    def test_demo_only_passes_when_demo(self):
        risk_manager.check_demo_only()

    def test_not_stopped_passes_by_default(self):
        risk_manager.check_not_stopped()

    def test_not_stopped_raises_on_emergency_stop(self):
        database.set_control_state(emergency_stop=True)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_not_stopped()
        self.assertEqual(ctx.exception.rule, "emergency_stop")

    def test_not_stopped_raises_on_paused(self):
        database.set_control_state(paused=True)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_not_stopped()
        self.assertEqual(ctx.exception.rule, "paused")

    def test_not_stopped_passes_again_after_clearing(self):
        database.set_control_state(emergency_stop=True)
        database.set_control_state(emergency_stop=False)
        risk_manager.check_not_stopped()

    def test_not_stopped_passes_when_no_broker_account_id_given(self):
        account_id = database.create_broker_account("Acc1")
        database.update_broker_account(account_id, emergency_stopped=True)
        risk_manager.check_not_stopped()  # no account_id - this account's stop doesn't apply

    def test_not_stopped_raises_on_a_stopped_broker_account(self):
        account_id = database.create_broker_account("Acc1")
        database.update_broker_account(account_id, emergency_stopped=True)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_not_stopped(account_id)
        self.assertEqual(ctx.exception.rule, "account_emergency_stop")

    def test_not_stopped_passes_for_a_different_unaffected_account(self):
        stopped_id = database.create_broker_account("Stopped Acc")
        other_id = database.create_broker_account("Other Acc")
        database.update_broker_account(stopped_id, emergency_stopped=True)
        risk_manager.check_not_stopped(other_id)  # must not raise - a different account's stop

    def test_not_stopped_passes_again_after_clearing_a_broker_account_stop(self):
        account_id = database.create_broker_account("Acc1")
        database.update_broker_account(account_id, emergency_stopped=True)
        database.update_broker_account(account_id, emergency_stopped=False)
        risk_manager.check_not_stopped(account_id)

    def test_minimum_payout_below_threshold(self):
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_minimum_payout(risk_manager.MINIMUM_PAYOUT - 1)
        self.assertEqual(ctx.exception.rule, "minimum_payout")

    def test_minimum_payout_at_threshold(self):
        risk_manager.check_minimum_payout(risk_manager.MINIMUM_PAYOUT)

    def test_minimum_payout_above_threshold(self):
        risk_manager.check_minimum_payout(risk_manager.MINIMUM_PAYOUT + 10)

    def test_minimum_payout_none_fails_closed(self):
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_minimum_payout(None)
        self.assertEqual(ctx.exception.rule, "minimum_payout")

    def test_max_daily_loss_disabled_when_zero(self):
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 0
        try:
            self._insert_signal(result="loss", profit_loss=-1000)
            risk_manager.check_max_daily_loss()  # must not raise
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_max_daily_loss_passes_within_threshold(self):
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 10
        try:
            self._insert_signal(result="loss", profit_loss=-1)
            self._insert_signal(result="win", profit_loss=2)
            risk_manager.check_max_daily_loss()  # net +1, must not raise
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_max_daily_loss_trips_on_alternating_win_loss_pattern(self):
        """The whole point of this rule: MAX_CONSECUTIVE_LOSSES never trips
        on an alternating win/loss pattern (no unbroken streak), but a
        real no-edge payout structure (win pays back less than 100%)
        bleeds out money through exactly that pattern. This must be caught
        by realized P/L, not streak length."""
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 5
        try:
            # Alternating win/loss, net -6: win +0.5, loss -1, x12 (12 * -0.5 = -6).
            for _ in range(12):
                self._insert_signal(result="win", profit_loss=0.5)
                self._insert_signal(result="loss", profit_loss=-1)
            with self.assertRaises(risk_manager.RiskViolation) as ctx:
                risk_manager.check_max_daily_loss()
            self.assertEqual(ctx.exception.rule, "max_daily_loss")
            # Confirm the premise: consecutive-losses would NOT have caught this.
            risk_manager.check_max_consecutive_losses()  # must not raise
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_max_daily_loss_counts_pending_stake_as_worst_case(self):
        # Realized P/L alone (-1) is well within the -5 limit, but a
        # pending trade risking $10 would breach it if it loses - same
        # burst-race reasoning as check_max_consecutive_losses above.
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 5
        try:
            self._insert_signal(result="loss", profit_loss=-1)
            self._insert_pending_signal(trade_amount=10)
            with self.assertRaises(risk_manager.RiskViolation) as ctx:
                risk_manager.check_max_daily_loss()
            self.assertEqual(ctx.exception.rule, "max_daily_loss")
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_max_daily_loss_pending_stake_within_threshold_does_not_block(self):
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 50
        try:
            self._insert_signal(result="win", profit_loss=2)
            self._insert_pending_signal(trade_amount=10)
            risk_manager.check_max_daily_loss()  # worst case -8, within -50
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_max_daily_loss_ignores_prior_days(self):
        original = risk_manager.MAX_DAILY_LOSS
        risk_manager.MAX_DAILY_LOSS = 5
        try:
            yesterday = (datetime.now() - timedelta(days=1)).isoformat()
            self._insert_signal(result="loss", profit_loss=-1000, closed_at=yesterday)
            risk_manager.check_max_daily_loss()  # must not raise - loss was yesterday
        finally:
            risk_manager.MAX_DAILY_LOSS = original

    def test_evaluate_all_passes_clean_signal(self):
        risk_manager.evaluate_all("GBP/USD OTC", "SELL", "5 Minute", 1)

    # -- Phase 2 UI settings: dynamic override --------------------------

    def test_max_trade_amount_reads_ui_setting_override(self):
        database.set_setting("max_trade_amount", 5)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_trade_amount(10)
        self.assertEqual(ctx.exception.rule, "max_trade_amount")
        risk_manager.check_max_trade_amount(4)  # must not raise

    def test_max_trade_amount_falls_back_to_static_default_when_unset(self):
        risk_manager.check_max_trade_amount(risk_manager.MAX_TRADE_AMOUNT - 1)  # must not raise
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_trade_amount(risk_manager.MAX_TRADE_AMOUNT + 1)

    # -- max_trades_per_day (new) ----------------------------------------

    def test_max_trades_per_day_disabled_by_default(self):
        for _ in range(20):
            self._insert_signal()
        risk_manager.check_max_trades_per_day()  # must not raise - disabled (0)

    def test_max_trades_per_day_trips_once_configured(self):
        database.set_setting("max_trades_per_day", 3)
        for _ in range(3):
            self._insert_signal()
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_max_trades_per_day()
        self.assertEqual(ctx.exception.rule, "max_trades_per_day")

    def test_max_trades_per_day_is_scoped_per_broker_account(self):
        database.set_setting("max_trades_per_day", 3)
        account_a = database.create_broker_account("Account A")
        account_b = database.create_broker_account("Account B")
        for _ in range(3):
            self._insert_signal(broker_account_id=account_a)
        with self.assertRaises(risk_manager.RiskViolation):
            risk_manager.check_max_trades_per_day(account_a)
        risk_manager.check_max_trades_per_day(account_b)  # must not raise - a separate quota

    # -- daily_profit_target (new) ----------------------------------------

    def test_daily_profit_target_disabled_by_default(self):
        self._insert_signal(result="win", profit_loss=1000)
        risk_manager.check_daily_profit_target()  # must not raise - disabled (0)

    def test_daily_profit_target_trips_once_reached(self):
        database.set_setting("daily_profit_target", 10)
        self._insert_signal(result="win", profit_loss=6)
        self._insert_signal(result="win", profit_loss=5)
        with self.assertRaises(risk_manager.RiskViolation) as ctx:
            risk_manager.check_daily_profit_target()
        self.assertEqual(ctx.exception.rule, "daily_profit_target")

    def test_daily_profit_target_not_reached_yet(self):
        database.set_setting("daily_profit_target", 100)
        self._insert_signal(result="win", profit_loss=6)
        risk_manager.check_daily_profit_target()  # must not raise

    # -- compute_trade_amount (new position sizing) -----------------------

    def test_compute_trade_amount_fixed_mode_default(self):
        self.assertEqual(risk_manager.compute_trade_amount(7), 7)

    def test_compute_trade_amount_percent_mode(self):
        database.set_setting("trade_sizing_mode", "percent")
        database.set_setting("starting_bankroll", 1000)
        database.set_setting("trade_sizing_percent", 2)
        # No trades yet - lifetime P/L is 0, bankroll = 1000 -> 2% = 20.
        self.assertEqual(risk_manager.compute_trade_amount(7), 20.0)

    def test_compute_trade_amount_percent_mode_accounts_for_realized_pnl(self):
        database.set_setting("trade_sizing_mode", "percent")
        database.set_setting("starting_bankroll", 1000)
        database.set_setting("trade_sizing_percent", 10)
        self._insert_signal(result="loss", profit_loss=-500)
        # bankroll = 1000 - 500 = 500 -> 10% = 50.
        self.assertEqual(risk_manager.compute_trade_amount(7), 50.0)

    def test_compute_trade_amount_percent_mode_falls_back_when_bankroll_not_positive(self):
        database.set_setting("trade_sizing_mode", "percent")
        database.set_setting("starting_bankroll", 0)
        database.set_setting("trade_sizing_percent", 5)
        self.assertEqual(risk_manager.compute_trade_amount(7), 7)


_DEFAULT_EFFECTIVE_SETTINGS = {
    "starting_bankroll": 0,
    "trade_sizing_mode": "fixed",
    "fixed_trade_amount": 5,
    "trade_sizing_percent": 1.0,
    "max_trade_amount": 50,
    "max_daily_loss": 100,
    "daily_profit_target": 0,
    "max_trades_per_hour": 10,
    "max_trades_per_day": 0,
    "max_consecutive_losses": 3,
    "cooldown_after_loss_seconds": 60,
    "minimum_payout": 90,
    "duplicate_signal_window_seconds": 30,
}


class DiagnoseSettingsTests(unittest.TestCase):
    """diagnose_settings is a pure function traced against the exact
    enforcement semantics in this file - no DB needed."""

    def _findings_by_key(self, effective):
        overrides = dict(_DEFAULT_EFFECTIVE_SETTINGS)
        overrides.update(effective)
        return {f["key"]: f for f in risk_manager.diagnose_settings(overrides)}

    def test_sane_defaults_produce_no_critical_findings(self):
        findings = risk_manager.diagnose_settings(_DEFAULT_EFFECTIVE_SETTINGS)
        self.assertFalse(any(f["severity"] == "critical" for f in findings))

    def test_fixed_mode_reports_percent_fields_as_ignored_info(self):
        findings = self._findings_by_key({"trade_sizing_mode": "fixed"})
        self.assertEqual(findings["trade_sizing_percent"]["severity"], "info")

    def test_percent_mode_reports_fixed_amount_as_fallback_info(self):
        findings = self._findings_by_key({"trade_sizing_mode": "percent"})
        self.assertEqual(findings["fixed_trade_amount"]["severity"], "info")

    def test_percent_mode_zero_percent_is_critical(self):
        findings = self._findings_by_key({"trade_sizing_mode": "percent", "trade_sizing_percent": 0})
        self.assertEqual(findings["trade_sizing_percent"]["severity"], "critical")

    def test_fixed_amount_exceeding_max_trade_amount_is_critical(self):
        findings = self._findings_by_key({"fixed_trade_amount": 100, "max_trade_amount": 50})
        self.assertEqual(findings["fixed_trade_amount"]["severity"], "critical")

    def test_zero_max_trade_amount_is_critical(self):
        findings = self._findings_by_key({"max_trade_amount": 0})
        self.assertEqual(findings["max_trade_amount"]["severity"], "critical")

    def test_zero_max_trades_per_hour_is_critical(self):
        findings = self._findings_by_key({"max_trades_per_hour": 0})
        self.assertEqual(findings["max_trades_per_hour"]["severity"], "critical")

    def test_zero_max_consecutive_losses_is_critical(self):
        findings = self._findings_by_key({"max_consecutive_losses": 0})
        self.assertEqual(findings["max_consecutive_losses"]["severity"], "critical")

    def test_minimum_payout_over_100_is_critical(self):
        findings = self._findings_by_key({"minimum_payout": 150})
        self.assertEqual(findings["minimum_payout"]["severity"], "critical")

    def test_minimum_payout_negative_is_warning(self):
        findings = self._findings_by_key({"minimum_payout": -5})
        self.assertEqual(findings["minimum_payout"]["severity"], "warning")

    def test_negative_cooldown_is_warning(self):
        findings = self._findings_by_key({"cooldown_after_loss_seconds": -10})
        self.assertEqual(findings["cooldown_after_loss_seconds"]["severity"], "warning")

    def test_negative_duplicate_window_is_warning(self):
        findings = self._findings_by_key({"duplicate_signal_window_seconds": -10})
        self.assertEqual(findings["duplicate_signal_window_seconds"]["severity"], "warning")


if __name__ == "__main__":
    unittest.main()
