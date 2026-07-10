import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import ai_analysis
import database
import trade_lifecycle


def _fixed_profile(name, fixed_amount=10, max_session_loss=0, profit_target=0, max_trades=0):
    return {
        "name": name, "sizing_mode": "fixed", "bankroll": 0, "fixed_amount": fixed_amount,
        "percent_of_bankroll": 0, "kelly_win_rate_estimate": None, "kelly_payout_estimate": None,
        "kelly_fraction_multiplier": 0.5, "max_trade_amount": 0, "profit_target": profit_target,
        "max_session_loss": max_session_loss, "max_trades": max_trades,
        "martingale": {"enabled": False, "max_steps": 0, "multiplier": 2.0, "custom_ladder_json": None,
                       "reset_after_win": True, "reset_after_session": True, "max_total_exposure": 0},
        "compounding": {"mode": "disabled", "steps_json": None, "drawdown_reset_percent": 0,
                        "max_risk_percent": 0, "min_risk_percent": 0},
        "profit_vault": {"enabled": False, "vault_percent": 0, "trigger_event": "every_winning_session",
                          "milestone_amount": 0},
    }


def _record_real_signal(channel, result, received_at, payout=85):
    signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
    trade_id = database.record_signal_received(signal, source=channel)
    status = {"win": trade_lifecycle.TradeStatus.RESULT_WIN, "loss": trade_lifecycle.TradeStatus.RESULT_LOSS,
              "draw": trade_lifecycle.TradeStatus.RESULT_DRAW}[result]
    database.update_trade_status(trade_id, status, result=result, payout=payout)
    conn = database.get_connection()
    conn.execute("UPDATE signals SET received_at = ? WHERE id = ?", (received_at, trade_id))
    conn.commit()
    conn.close()
    return trade_id


def _metrics(**overrides):
    base = {
        "final_bankroll": 1200.0, "total_profit_loss": 200.0, "roi_percent": 20.0,
        "win_rate": 0.6, "loss_rate": 0.4, "max_drawdown_percent": 8.0,
        "max_drawdown_amount": 80.0, "best_day_pnl": 50.0, "worst_day_pnl": -20.0,
        "longest_win_streak": 4, "longest_loss_streak": 2, "max_martingale_step_used": 0,
        "sessions_completed": 8, "sessions_stopped_by_target": 0, "sessions_stopped_by_loss_limit": 0,
        "avg_trade_size": 10.0, "largest_trade_size": 10.0, "total_protected_profit": 0.0,
        "risk_score": "Low", "best_for_label": "Capital Preservation",
        "sharpe_like_score": 1.2, "profit_factor": 1.8, "consistency_percent": 75.0,
        "recovery_factor": 2.5, "volatility": 15.0,
    }
    base.update(overrides)
    return base


def _strategy(id, label, **metric_overrides):
    return {"id": id, "label": label, "profile_snapshot": {"compounding": {"mode": "disabled"}},
            "metrics": _metrics(**metric_overrides)}


class StrategyNarrativeTests(unittest.TestCase):
    def test_narrative_cites_real_numbers(self):
        text = ai_analysis.generate_strategy_narrative("Balanced Builder", _metrics())
        self.assertIn("Balanced Builder", text)
        self.assertIn("20.0%", text)
        self.assertIn("8.0%", text)

    def test_narrative_mentions_martingale_when_used(self):
        text = ai_analysis.generate_strategy_narrative("Aggressive", _metrics(max_martingale_step_used=4))
        self.assertIn("martingale", text.lower())

    def test_narrative_handles_no_results(self):
        text = ai_analysis.generate_strategy_narrative("Empty", None)
        self.assertIn("no results yet", text)

    def test_narrative_handles_losing_strategy(self):
        text = ai_analysis.generate_strategy_narrative("Loser", _metrics(total_profit_loss=-50.0, roi_percent=-5.0))
        self.assertIn("lost", text)


class RunNarrativeTests(unittest.TestCase):
    def test_single_strategy_falls_back_to_strategy_narrative(self):
        report = {"strategies": [_strategy(1, "Only One")]}
        text = ai_analysis.generate_run_narrative(report)
        self.assertIn("Only One", text)

    def test_no_strategies_reports_honestly(self):
        report = {"strategies": []}
        text = ai_analysis.generate_run_narrative(report)
        self.assertIn("No strategies", text)

    def test_recommends_the_rank_overall_winner(self):
        s1 = _strategy(1, "High Growth High Risk", roi_percent=80.0, max_drawdown_percent=35.0)
        s1["metrics"]["rank_overall"] = 2
        s2 = _strategy(2, "Balanced", roi_percent=40.0, max_drawdown_percent=10.0)
        s2["metrics"]["rank_overall"] = 1
        report = {"strategies": [s1, s2]}
        text = ai_analysis.generate_run_narrative(report)
        self.assertIn("Balanced", text)
        self.assertIn("recommended", text.lower())

    def test_explains_tradeoff_when_recommended_is_not_highest_growth(self):
        s1 = _strategy(1, "Wild", roi_percent=90.0, max_drawdown_percent=40.0)
        s1["metrics"]["rank_overall"] = 2
        s2 = _strategy(2, "Steady", roi_percent=30.0, max_drawdown_percent=6.0)
        s2["metrics"]["rank_overall"] = 1
        report = {"strategies": [s1, s2]}
        text = ai_analysis.generate_run_narrative(report)
        self.assertIn("Wild", text)  # names the higher-growth alternative
        self.assertIn("more ROI", text)


class AnswerQuestionsTests(unittest.TestCase):
    def test_answers_every_question_with_a_real_winner(self):
        s1 = _strategy(1, "A", total_profit_loss=300.0, max_drawdown_percent=5.0, recovery_factor=4.0)
        s2 = _strategy(2, "B", total_profit_loss=100.0, max_drawdown_percent=20.0, recovery_factor=1.0)
        report = {"strategies": [s1, s2]}
        answers = ai_analysis.answer_strategy_questions(report)
        self.assertEqual(answers["which_made_the_most_money"]["answer"], "A")
        self.assertEqual(answers["which_had_lowest_drawdown"]["answer"], "A")
        self.assertEqual(answers["which_recovered_best"]["answer"], "A")

    def test_empty_report_returns_empty_dict(self):
        self.assertEqual(ai_analysis.answer_strategy_questions({"strategies": []}), {})

    def test_recovered_best_requires_actual_net_profit(self):
        # Both strategies finish at a loss - the least-negative recovery
        # factor is not a real recovery and must not be presented as one.
        s1 = _strategy(1, "A", total_profit_loss=-50.0, recovery_factor=-0.5)
        s2 = _strategy(2, "B", total_profit_loss=-200.0, recovery_factor=-2.0)
        report = {"strategies": [s1, s2]}
        answers = ai_analysis.answer_strategy_questions(report)
        self.assertIsNone(answers["which_recovered_best"]["answer"])

    def test_recovered_best_picks_among_actually_profitable_strategies(self):
        s1 = _strategy(1, "Profitable", total_profit_loss=100.0, recovery_factor=2.0)
        s2 = _strategy(2, "Losing", total_profit_loss=-50.0, recovery_factor=-0.5)
        report = {"strategies": [s1, s2]}
        answers = ai_analysis.answer_strategy_questions(report)
        self.assertEqual(answers["which_recovered_best"]["answer"], "Profitable")

    def test_most_aggressive_uses_volatility_not_roi_direction(self):
        # Both strategies are losing, but A has higher volatility (wider
        # swings) - it should still be flagged "most aggressive" even
        # though its ROI is the more-negative (worse) of the two.
        s1 = _strategy(1, "Wild", roi_percent=-50.0, volatility=40.0)
        s2 = _strategy(2, "Mild", roi_percent=-5.0, volatility=5.0)
        report = {"strategies": [s1, s2]}
        answers = ai_analysis.answer_strategy_questions(report)
        self.assertEqual(answers["which_is_most_aggressive"]["answer"], "Wild")

    def test_none_metrics_excluded_gracefully(self):
        s1 = _strategy(1, "A", sharpe_like_score=None)
        report = {"strategies": [s1]}
        answers = ai_analysis.answer_strategy_questions(report)
        self.assertIsNone(answers["which_has_the_best_risk_adjusted_return"]["answer"])


class ExtendedRankingsTests(unittest.TestCase):
    def test_ranks_by_consistency_and_recovery(self):
        s1 = _strategy(1, "A", consistency_percent=90.0, recovery_factor=5.0)
        s2 = _strategy(2, "B", consistency_percent=50.0, recovery_factor=1.0)
        report = {"strategies": [s1, s2]}
        ranks = ai_analysis.generate_extended_rankings(report)
        self.assertEqual(ranks[1]["rank_most_consistent"], 1)
        self.assertEqual(ranks[2]["rank_most_consistent"], 2)
        self.assertEqual(ranks[1]["rank_best_recovery"], 1)

    def test_compounding_rank_only_among_compounding_strategies(self):
        s1 = _strategy(1, "Fixed", roi_percent=50.0)  # compounding disabled (default in _strategy)
        s2 = _strategy(2, "Compounder", roi_percent=30.0)
        s2["profile_snapshot"] = {"compounding": {"mode": "daily"}}
        report = {"strategies": [s1, s2]}
        ranks = ai_analysis.generate_extended_rankings(report)
        self.assertIsNone(ranks[1]["rank_best_compounding"])  # never participated
        self.assertEqual(ranks[2]["rank_best_compounding"], 1)

    def test_empty_report(self):
        self.assertEqual(ai_analysis.generate_extended_rankings({"strategies": []}), {})


class SignalProviderScorecardTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_no_history_returns_none(self):
        card = ai_analysis.generate_signal_provider_scorecard("Nonexistent Channel", [_fixed_profile("A")])
        self.assertIsNone(card)

    def test_scorecard_reflects_real_win_rate(self):
        for i in range(8):
            _record_real_signal("Go+ Trading", "win", f"2026-01-01T10:0{i}:00")
        for i in range(2):
            _record_real_signal("Go+ Trading", "loss", f"2026-01-02T10:0{i}:00")
        card = ai_analysis.generate_signal_provider_scorecard("Go+ Trading", [_fixed_profile("Fixed $10")])
        self.assertIsNotNone(card)
        self.assertEqual(card["graded_signal_count"], 10)
        self.assertEqual(card["historical_win_rate"], 0.8)
        self.assertEqual(card["recommended_strategy"], "Fixed $10")
        self.assertGreater(card["overall_score"], 0)
        self.assertLessEqual(card["overall_score"], 100)

    def test_scorecard_picks_best_of_multiple_candidates(self):
        for i in range(6):
            _record_real_signal("Channel A", "win", f"2026-01-01T10:{i:02d}:00")
        for i in range(4):
            _record_real_signal("Channel A", "loss", f"2026-01-02T10:{i:02d}:00")
        candidates = [_fixed_profile("Small", fixed_amount=5), _fixed_profile("Large", fixed_amount=200)]
        card = ai_analysis.generate_signal_provider_scorecard("Channel A", candidates)
        self.assertIn(card["recommended_strategy"], ["Small", "Large"])

    def test_confidence_scales_with_sample_size(self):
        for i in range(5):
            _record_real_signal("Small Sample", "win", f"2026-01-01T10:0{i}:00")
        card_small = ai_analysis.generate_signal_provider_scorecard("Small Sample", [_fixed_profile("A")])

        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim2.db"
        database.initialize_database()
        for i in range(60):
            _record_real_signal("Big Sample", "win", f"2026-01-{(i % 28) + 1:02d}T10:00:00")
        card_big = ai_analysis.generate_signal_provider_scorecard("Big Sample", [_fixed_profile("A")])

        self.assertLess(card_small["confidence_percent"], card_big["confidence_percent"])
        self.assertEqual(card_big["confidence_percent"], 100)

    def test_average_losing_streak_computed_from_sequence(self):
        # win, loss, loss, win, loss, win, win -> losing streaks of [2, 1] -> avg 1.5
        results = ["win", "loss", "loss", "win", "loss", "win", "win"]
        for i, r in enumerate(results):
            _record_real_signal("Streaky Channel", r, f"2026-01-01T10:{i:02d}:00")
        card = ai_analysis.generate_signal_provider_scorecard("Streaky Channel", [_fixed_profile("A")])
        self.assertEqual(card["average_losing_streak"], 1.5)


if __name__ == "__main__":
    unittest.main()
