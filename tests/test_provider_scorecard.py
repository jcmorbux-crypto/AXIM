"""2026-08-01: Blackwater/Sniper's ONLY allowed input - see core/
provider_scorecard.py's own module docstring on why these numbers must
be real and measurable, never a fabricated confidence score."""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import provider_scorecard
from trade_lifecycle import TradeStatus


class ProviderScorecardTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _trade(self, channel_id, result, profit_loss, payout=90, session_id=None,
               received_at="2026-08-01T00:00:00", opened_at="2026-08-01T00:00:03"):
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
            channel_id=channel_id, session_id=session_id,
        )
        conn = database.get_connection()
        conn.execute("UPDATE signals SET received_at = ? WHERE id = ?", (received_at, trade_id))
        conn.commit()
        conn.close()
        database.update_trade_status(
            trade_id, TradeStatus.RESULT_WIN, result=result, profit_loss=profit_loss,
            payout=payout, opened_at=opened_at,
        )
        return trade_id

    def test_no_trades_yet_returns_zero_sample_with_none_stats(self):
        card = provider_scorecard.get_scorecard(999)
        self.assertEqual(card["sample_size"], 0)
        self.assertIsNone(card["win_rate"])
        self.assertIsNone(card["profit_factor"])
        self.assertEqual(card["current_streak"], 0)

    def test_win_rate_and_sample_size(self):
        for _ in range(3):
            self._trade(5, "win", 9.0)
        self._trade(5, "loss", -10.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["sample_size"], 4)
        self.assertEqual(card["win_rate"], 75.0)

    def test_profit_factor_is_gross_profit_over_gross_loss(self):
        self._trade(5, "win", 10.0)
        self._trade(5, "win", 10.0)
        self._trade(5, "loss", -5.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["profit_factor"], 4.0)  # 20 / 5

    def test_profit_factor_is_none_with_no_losses(self):
        self._trade(5, "win", 10.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertIsNone(card["profit_factor"])

    def test_expected_value_is_average_pl_per_trade(self):
        self._trade(5, "win", 10.0)
        self._trade(5, "loss", -5.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["expected_value"], 2.5)

    def test_current_streak_positive_for_wins(self):
        self._trade(5, "loss", -10.0)
        self._trade(5, "win", 9.0)
        self._trade(5, "win", 9.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["current_streak"], 2)

    def test_current_streak_negative_for_losses(self):
        self._trade(5, "win", 9.0)
        self._trade(5, "loss", -10.0)
        self._trade(5, "loss", -10.0)
        self._trade(5, "loss", -10.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["current_streak"], -3)

    def test_max_drawdown_percent_from_peak(self):
        self._trade(5, "win", 100.0)   # equity 100, peak 100
        self._trade(5, "loss", -50.0)  # equity 50, dd = 50%
        self._trade(5, "loss", -25.0)  # equity 25, dd = 75%
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["max_drawdown_percent"], 75.0)

    def test_avg_payout_percent(self):
        self._trade(5, "win", 9.0, payout=80)
        self._trade(5, "win", 9.0, payout=90)
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["avg_payout_percent"], 85.0)

    def test_avg_signal_age_seconds(self):
        self._trade(5, "win", 9.0, received_at="2026-08-01T00:00:00", opened_at="2026-08-01T00:00:04")
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["avg_signal_age_seconds"], 4.0)

    def test_channels_are_isolated_from_each_other(self):
        self._trade(5, "win", 9.0)
        self._trade(6, "loss", -10.0)
        card5 = provider_scorecard.get_scorecard(5)
        card6 = provider_scorecard.get_scorecard(6)
        self.assertEqual(card5["win_rate"], 100.0)
        self.assertEqual(card6["win_rate"], 0.0)

    def test_rolling_windows_scope_to_the_most_recent_n_trades(self):
        for _ in range(30):
            self._trade(5, "loss", -10.0)
        for _ in range(20):
            self._trade(5, "win", 9.0)
        card = provider_scorecard.get_scorecard(5)
        # last 25 trades: 20 wins + 5 losses (the tail end of the losing run)
        self.assertEqual(card["rolling"]["25"]["sample_size"], 25)
        self.assertEqual(card["rolling"]["25"]["win_rate"], 80.0)
        # lifetime: 20 wins / 50 total
        self.assertEqual(card["win_rate"], 40.0)

    def test_signal_rejection_rate(self):
        self._trade(5, "win", 9.0)
        rejected_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
            channel_id=5,
        )
        database.update_trade_status(rejected_id, TradeStatus.ERROR, result="rejected:max_trade_amount")
        card = provider_scorecard.get_scorecard(5)
        self.assertEqual(card["signal_rejection_rate"], 50.0)

    def test_session_scoped_stats_are_separate_from_lifetime(self):
        self._trade(5, "win", 9.0, session_id=1)
        self._trade(5, "loss", -10.0, session_id=2)
        card = provider_scorecard.get_scorecard(5, session_id=1)
        self.assertEqual(card["session"]["sample_size"], 1)
        self.assertEqual(card["session"]["win_rate"], 100.0)
        self.assertEqual(card["sample_size"], 2)  # lifetime unaffected

    def test_no_session_id_given_means_no_session_slice(self):
        self._trade(5, "win", 9.0)
        card = provider_scorecard.get_scorecard(5)
        self.assertIsNone(card["session"])


if __name__ == "__main__":
    unittest.main()
