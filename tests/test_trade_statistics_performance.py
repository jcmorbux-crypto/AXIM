import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import trade_statistics


def _make_closed_trade(asset="EUR/USD OTC", channel="TestChannel", result="win", profit_loss=0.9,
                        trade_amount=1, payout=90, closed_at=None, session_id=None, fund_id=None):
    signal = {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
              "trade_amount": trade_amount}
    trade_id = database.record_signal_received(signal, source=channel, session_id=session_id, fund_id=fund_id)
    database.update_trade_status(
        trade_id, __import__("trade_lifecycle").TradeStatus.RESULT_WIN if result == "win"
        else __import__("trade_lifecycle").TradeStatus.RESULT_LOSS,
        result=result, profit_loss=profit_loss, payout=payout,
        closed_at=(closed_at or datetime.now()).isoformat(),
    )
    return trade_id


class PerformanceAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_profit_by_channel_groups_correctly(self):
        _make_closed_trade(channel="Alpha", result="win", profit_loss=1.0)
        _make_closed_trade(channel="Alpha", result="loss", profit_loss=-1.0)
        _make_closed_trade(channel="Beta", result="win", profit_loss=2.0)
        grouped = trade_statistics.profit_by_channel()
        self.assertEqual(grouped["Alpha"]["total_closed"], 2)
        self.assertAlmostEqual(grouped["Alpha"]["profit_loss"], 0.0)
        self.assertEqual(grouped["Beta"]["total_closed"], 1)
        self.assertAlmostEqual(grouped["Beta"]["profit_loss"], 2.0)

    def test_best_worst_filters_by_min_trades(self):
        _make_closed_trade(channel="OneOff", result="win", profit_loss=100.0)
        _make_closed_trade(channel="Steady", result="win", profit_loss=1.0)
        _make_closed_trade(channel="Steady", result="win", profit_loss=1.0)
        _make_closed_trade(channel="Steady", result="loss", profit_loss=-1.0)
        grouped = trade_statistics.profit_by_channel()
        result = trade_statistics.best_worst(grouped, min_trades=3)
        # "OneOff" has only 1 trade - excluded despite its huge profit
        self.assertEqual(result["best"]["name"], "Steady")
        self.assertEqual(result["worst"]["name"], "Steady")

    def test_best_worst_returns_none_when_nothing_eligible(self):
        _make_closed_trade(channel="Solo", result="win", profit_loss=1.0)
        grouped = trade_statistics.profit_by_channel()
        result = trade_statistics.best_worst(grouped, min_trades=5)
        self.assertIsNone(result["best"])
        self.assertIsNone(result["worst"])

    def test_max_drawdown_computes_peak_to_trough(self):
        # +10, +5 (peak=15), -20 (trough=-5, drawdown from peak=20), +3
        _make_closed_trade(result="win", profit_loss=10)
        _make_closed_trade(result="win", profit_loss=5)
        _make_closed_trade(result="loss", profit_loss=-20)
        _make_closed_trade(result="win", profit_loss=3)
        self.assertAlmostEqual(trade_statistics.max_drawdown(), 20.0)

    def test_max_drawdown_zero_when_always_profitable(self):
        _make_closed_trade(result="win", profit_loss=1)
        _make_closed_trade(result="win", profit_loss=2)
        self.assertEqual(trade_statistics.max_drawdown(), 0.0)

    def test_longest_streaks(self):
        for _ in range(3):
            _make_closed_trade(result="win", profit_loss=1)
        _make_closed_trade(result="loss", profit_loss=-1)
        for _ in range(5):
            _make_closed_trade(result="loss", profit_loss=-1)
        _make_closed_trade(result="win", profit_loss=1)
        streaks = trade_statistics.longest_streaks()
        self.assertEqual(streaks["longest_win_streak"], 3)
        self.assertEqual(streaks["longest_loss_streak"], 6)

    def test_session_performance_scopes_to_session_id(self):
        session_id = database.start_trading_session("Perf Test", [1], "DEMO")
        _make_closed_trade(result="win", profit_loss=5, session_id=session_id)
        _make_closed_trade(result="loss", profit_loss=-2, session_id=session_id)
        _make_closed_trade(result="win", profit_loss=99)  # not in this session
        results = trade_statistics.session_performance()
        this_session = [r for r in results if r["session_id"] == session_id][0]
        self.assertEqual(this_session["total_closed"], 2)
        self.assertEqual(this_session["wins"], 1)

    def test_martingale_and_compounding_performance_reports_attached_sessions(self):
        profile_id = database.create_risk_profile("MG Profile", sizing_mode="fixed", fixed_amount=5)
        database.update_martingale_settings(profile_id, enabled=True)
        session_id = database.start_trading_session("MG Session", [1], "DEMO", risk_profile_id=profile_id)
        database.advance_martingale_step(session_id)

        report = trade_statistics.martingale_and_compounding_performance()
        matching = [s for s in report["martingale_sessions"] if s["session_id"] == session_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["current_step"], 1)
        self.assertEqual(report["compounding_sessions"], [])  # compounding not enabled on this profile

    def test_performance_report_shape(self):
        _make_closed_trade(result="win", profit_loss=1)
        report = trade_statistics.performance_report()
        for key in ("daily", "weekly", "monthly", "yearly", "lifetime", "by_channel",
                    "by_asset", "best_time_of_day", "max_drawdown", "streaks", "sessions", "risk_engine"):
            self.assertIn(key, report)


class FundScopedStatsTests(unittest.TestCase):
    """daily_stats/consecutive_wins/consecutive_losses/lifetime_stats
    all accept an optional fund_id - Rule Builder rules are Fund-owned
    now, so a fund-scoped rule's "daily profit" must mean that fund's
    daily profit, not the whole account's."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_daily_stats_scoped_to_fund(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        _make_closed_trade(result="win", profit_loss=50, fund_id=fund_a)
        _make_closed_trade(result="win", profit_loss=999, fund_id=fund_b)
        self.assertEqual(trade_statistics.daily_stats(fund_id=fund_a)["profit_loss"], 50)
        self.assertEqual(trade_statistics.daily_stats(fund_id=fund_b)["profit_loss"], 999)
        self.assertEqual(trade_statistics.daily_stats()["profit_loss"], 1049)  # unscoped sees everything

    def test_consecutive_wins_scoped_to_fund(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        _make_closed_trade(result="loss", fund_id=fund_a)
        _make_closed_trade(result="win", fund_id=fund_b)
        _make_closed_trade(result="win", fund_id=fund_b)
        self.assertEqual(trade_statistics.consecutive_wins(fund_id=fund_a), 0)
        self.assertEqual(trade_statistics.consecutive_wins(fund_id=fund_b), 2)

    def test_lifetime_stats_scoped_to_fund(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        _make_closed_trade(result="win", profit_loss=10, fund_id=fund_a)
        _make_closed_trade(result="win", profit_loss=20, fund_id=fund_a)
        _make_closed_trade(result="win", profit_loss=5, fund_id=fund_b)
        self.assertEqual(trade_statistics.lifetime_stats(fund_id=fund_a)["profit_loss"], 30)
        self.assertEqual(trade_statistics.lifetime_stats(fund_id=fund_b)["profit_loss"], 5)


if __name__ == "__main__":
    unittest.main()
