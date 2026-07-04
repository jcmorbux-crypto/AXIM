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
                        result=None, closed_at=None):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"},
        )
        if result:
            database.update_trade_status(
                trade_id, TradeStatus.TRADE_CLOSED,
                result=result, closed_at=closed_at or datetime.now().isoformat(),
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

    def test_evaluate_all_passes_clean_signal(self):
        risk_manager.evaluate_all("GBP/USD OTC", "SELL", "5 Minute", 1)


if __name__ == "__main__":
    unittest.main()
