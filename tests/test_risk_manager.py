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
                        result=None, closed_at=None, profit_loss=None):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"},
        )
        if result:
            database.update_trade_status(
                trade_id, TradeStatus.TRADE_CLOSED,
                result=result, closed_at=closed_at or datetime.now().isoformat(),
                profit_loss=profit_loss,
            )
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


if __name__ == "__main__":
    unittest.main()
