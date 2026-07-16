import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import capital_recommendation as rec


def _strategy(strategy_id, label, rank_overall, max_drawdown_amount=100.0, avg_trade_size=10.0,
              roi_percent=20.0, win_rate=0.55, max_drawdown_percent=15.0, strategy_key="capital_preservation",
              total_profit_loss=200.0, final_bankroll=1200.0, longest_loss_streak=4, consistency_percent=65.0,
              worst_day_pnl=-30.0):
    return {
        "id": strategy_id, "label": label,
        "profile_snapshot": {"strategy_key": strategy_key},
        "metrics": {
            "rank_overall": rank_overall, "max_drawdown_amount": max_drawdown_amount,
            "avg_trade_size": avg_trade_size, "roi_percent": roi_percent, "win_rate": win_rate,
            "max_drawdown_percent": max_drawdown_percent, "total_profit_loss": total_profit_loss,
            "final_bankroll": final_bankroll, "longest_loss_streak": longest_loss_streak,
            "consistency_percent": consistency_percent, "worst_day_pnl": worst_day_pnl,
        },
    }


class PickBestStrategyTests(unittest.TestCase):
    def test_picks_rank_1(self):
        strategies = [_strategy(1, "A", rank_overall=2), _strategy(2, "B", rank_overall=1), _strategy(3, "C", rank_overall=3)]
        best = rec.pick_best_strategy(strategies)
        self.assertEqual(best["id"], 2)

    def test_returns_none_for_empty_list(self):
        self.assertIsNone(rec.pick_best_strategy([]))

    def test_returns_none_when_nothing_ranked_yet(self):
        strategies = [{"id": 1, "label": "A", "metrics": {}}]
        self.assertIsNone(rec.pick_best_strategy(strategies))

    def test_excludes_an_implausible_unbounded_compounding_result(self):
        # Real finding: Martin Trader's live backtest picked "Alternating
        # Compound" as rank_overall=1 with an ROI of ~1.4e14% - an
        # unbounded percent-of-growing-bankroll compounding artifact, not
        # a real strategy result. Must never be selected just because it
        # trivially wins the composite rank.
        strategies = [
            _strategy(1, "Alternating Compound", rank_overall=1, roi_percent=1.4e14),
            _strategy(2, "Capital Preservation", rank_overall=2, roi_percent=12.0),
        ]
        best = rec.pick_best_strategy(strategies)
        self.assertEqual(best["id"], 2)

    def test_returns_none_when_every_ranked_strategy_is_implausible(self):
        strategies = [_strategy(1, "A", rank_overall=1, roi_percent=1e10)]
        self.assertIsNone(rec.pick_best_strategy(strategies))

    def test_roi_exactly_at_the_ceiling_is_still_excluded(self):
        strategies = [_strategy(1, "A", rank_overall=1, roi_percent=rec.MAX_PLAUSIBLE_ROI_PERCENT)]
        self.assertIsNone(rec.pick_best_strategy(strategies))


class ComputeAllocationTiersTests(unittest.TestCase):
    def test_tiers_are_ordered_and_multiples_of_drawdown(self):
        tiers = rec.compute_allocation_tiers(max_drawdown_amount=200.0, avg_trade_size=5.0)
        self.assertEqual(tiers["minimum_allocation"], 300.0)       # 200 * 1.5
        self.assertEqual(tiers["conservative_allocation"], 500.0)  # 200 * 2.5
        self.assertEqual(tiers["suggested_allocation"], 800.0)     # 200 * 4.0
        self.assertLess(tiers["minimum_allocation"], tiers["conservative_allocation"])
        self.assertLess(tiers["conservative_allocation"], tiers["suggested_allocation"])

    def test_absolute_floor_applies_for_a_tiny_drawdown(self):
        tiers = rec.compute_allocation_tiers(max_drawdown_amount=1.0, avg_trade_size=0)
        self.assertEqual(tiers["minimum_allocation"], rec.ABSOLUTE_FLOOR)
        self.assertEqual(tiers["conservative_allocation"], rec.ABSOLUTE_FLOOR)
        self.assertEqual(tiers["suggested_allocation"], rec.ABSOLUTE_FLOOR)

    def test_avg_trade_size_floor_can_exceed_absolute_floor(self):
        # avg_trade_size * 10 = 300, bigger than the $50 absolute floor and
        # bigger than a tiny 1.5x/2.5x/4x drawdown multiple.
        tiers = rec.compute_allocation_tiers(max_drawdown_amount=1.0, avg_trade_size=30.0)
        self.assertEqual(tiers["minimum_allocation"], 300.0)
        self.assertEqual(tiers["conservative_allocation"], 300.0)
        self.assertEqual(tiers["suggested_allocation"], 300.0)

    def test_none_drawdown_does_not_crash(self):
        tiers = rec.compute_allocation_tiers(max_drawdown_amount=None, avg_trade_size=None)
        self.assertEqual(tiers["minimum_allocation"], rec.ABSOLUTE_FLOOR)


class ComputeConfidenceTests(unittest.TestCase):
    def test_full_sample_and_consistency_gives_full_score(self):
        score = rec.compute_confidence(actual_trade_count=500, consistency_percent=100)
        self.assertEqual(score, 100.0)

    def test_small_sample_caps_the_sample_component(self):
        # 50/500 trades = 10% of the sample-size component (60 pts max) = 6 pts,
        # plus 100% consistency = full 40 pts -> 46.
        score = rec.compute_confidence(actual_trade_count=50, consistency_percent=100)
        self.assertEqual(score, 46.0)

    def test_more_than_saturation_trades_does_not_exceed_full_marks(self):
        score = rec.compute_confidence(actual_trade_count=5000, consistency_percent=100)
        self.assertEqual(score, 100.0)

    def test_none_inputs_do_not_crash(self):
        score = rec.compute_confidence(actual_trade_count=None, consistency_percent=None)
        self.assertEqual(score, 0.0)


class ComputeStarRatingTests(unittest.TestCase):
    def test_full_confidence_is_5_stars(self):
        self.assertEqual(rec.compute_star_rating(100), 5)

    def test_zero_confidence_is_still_1_star_minimum(self):
        self.assertEqual(rec.compute_star_rating(0), 1)

    def test_mid_confidence_rounds_to_nearest_star(self):
        self.assertEqual(rec.compute_star_rating(55), 3)  # 55/20 = 2.75 -> rounds to 3


class ComputeRecommendationTests(unittest.TestCase):
    def test_full_recommendation_shape(self):
        report = {
            "run": {"id": 42, "starting_bankroll": 1000.0},
            "strategies": [
                _strategy(1, "Capital Preservation", rank_overall=2, max_drawdown_amount=100),
                _strategy(2, "Recovery Ladder", rank_overall=1, max_drawdown_amount=80, strategy_key="recovery_ladder"),
            ],
        }
        result = rec.compute_recommendation("Martin Trader", report, trades_backtested=1630)
        self.assertEqual(result["source_label"], "Martin Trader")
        self.assertEqual(result["backtest_run_id"], 42)
        self.assertEqual(result["best_strategy_id"], 2)
        self.assertEqual(result["best_strategy_key"], "recovery_ladder")
        self.assertEqual(result["best_strategy_name"], "Recovery Ladder")
        self.assertEqual(result["trades_backtested"], 1630)
        self.assertIn("minimum_allocation", result)
        self.assertIn("conservative_allocation", result)
        self.assertIn("suggested_allocation", result)
        self.assertEqual(result["net_profit"], 200.0)
        self.assertEqual(result["ending_balance"], 1200.0)
        self.assertEqual(result["longest_losing_streak"], 4)
        self.assertIn("confidence_score", result)
        self.assertIn("star_rating", result)

    def test_returns_none_when_no_strategy_is_ranked(self):
        report = {"run": {"id": 1}, "strategies": []}
        self.assertIsNone(rec.compute_recommendation("X", report, trades_backtested=0))

    def test_avg_daily_trades_uses_actual_trade_count_when_given(self):
        report = {
            "run": {"id": 1, "starting_bankroll": 1000.0},
            "strategies": [_strategy(1, "Capital Preservation", rank_overall=1)],
        }
        result = rec.compute_recommendation("X", report, trades_backtested=1000, session_count=20, actual_trade_count=400)
        self.assertEqual(result["avg_daily_trades"], 20.0)

    def test_no_session_count_leaves_avg_daily_trades_none(self):
        report = {
            "run": {"id": 1, "starting_bankroll": 1000.0},
            "strategies": [_strategy(1, "Capital Preservation", rank_overall=1)],
        }
        result = rec.compute_recommendation("X", report, trades_backtested=1000)
        self.assertIsNone(result["avg_daily_trades"])

    def test_session_goal_scales_with_suggested_allocation(self):
        # starting_bankroll=1000, net_profit=200 over 20 sessions -> $10/day avg.
        # suggested_allocation is 4x max_drawdown_amount=100 = 400 -> scale 0.4x.
        report = {
            "run": {"id": 1, "starting_bankroll": 1000.0},
            "strategies": [_strategy(1, "Capital Preservation", rank_overall=1, max_drawdown_amount=100)],
        }
        result = rec.compute_recommendation("X", report, trades_backtested=1000, session_count=20, actual_trade_count=400)
        self.assertEqual(result["recommended_session_goal"], 4.0)  # 10 * 0.4

    def test_losing_average_day_gets_no_fabricated_session_goal(self):
        report = {
            "run": {"id": 1, "starting_bankroll": 1000.0},
            "strategies": [_strategy(1, "Capital Preservation", rank_overall=1, total_profit_loss=-200.0)],
        }
        result = rec.compute_recommendation("X", report, trades_backtested=1000, session_count=20, actual_trade_count=400)
        self.assertIsNone(result["recommended_session_goal"])

    def test_daily_stop_is_always_computed_even_for_a_losing_strategy(self):
        report = {
            "run": {"id": 1, "starting_bankroll": 1000.0},
            "strategies": [_strategy(1, "Capital Preservation", rank_overall=1, total_profit_loss=-200.0, worst_day_pnl=-50.0, max_drawdown_amount=100)],
        }
        result = rec.compute_recommendation("X", report, trades_backtested=1000, session_count=20, actual_trade_count=400)
        self.assertIsNotNone(result["recommended_daily_stop"])
        self.assertGreater(result["recommended_daily_stop"], 0)


class GenerateRecommendationForProviderTestCase(unittest.TestCase):
    """DB-backed - covers the real bug found on the first live run
    against production data: a stale recommendation from an earlier,
    now-implausible backtest must be deleted, not left behind looking
    current, when a re-generation finds nothing plausible to replace it
    with."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_run_with_strategies(self, strategies_metrics):
        run_id = database.create_backtest_run("Test Run", {"source": "imported"}, 1000.0)
        for i, metrics in enumerate(strategies_metrics):
            strategy_id = database.create_backtest_strategy(run_id, None, f"Strategy {i}", {"strategy_key": f"k{i}"})
            database.save_backtest_metrics(strategy_id, metrics)
        return run_id

    def test_saves_a_real_recommendation(self):
        run_id = self._make_run_with_strategies([
            {"rank_overall": 1, "roi_percent": 20.0, "max_drawdown_percent": 10.0,
             "max_drawdown_amount": 50.0, "avg_trade_size": 5.0, "win_rate": 0.55},
        ])
        rec_id = rec.generate_recommendation_for_provider("Test Provider", run_id, trades_backtested=100)
        self.assertIsNotNone(rec_id)
        saved = database.get_capital_recommendation("Test Provider")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["backtest_run_id"], run_id)

    def test_a_stale_recommendation_is_deleted_when_regeneration_finds_nothing_plausible(self):
        # First generation: a plausible strategy exists, gets saved.
        run_id_1 = self._make_run_with_strategies([
            {"rank_overall": 1, "roi_percent": 20.0, "max_drawdown_percent": 10.0,
             "max_drawdown_amount": 50.0, "avg_trade_size": 5.0, "win_rate": 0.55},
        ])
        rec.generate_recommendation_for_provider("Martin Trader", run_id_1, trades_backtested=100)
        self.assertIsNotNone(database.get_capital_recommendation("Martin Trader"))

        # Re-generation (e.g. re-backtest against more data): every
        # candidate strategy now blows up - must not leave the old,
        # now-stale recommendation looking current.
        run_id_2 = self._make_run_with_strategies([
            {"rank_overall": 1, "roi_percent": 1e14, "max_drawdown_percent": 5.0,
             "max_drawdown_amount": 1e12, "avg_trade_size": 5.0, "win_rate": 0.93},
        ])
        result = rec.generate_recommendation_for_provider("Martin Trader", run_id_2, trades_backtested=1630)
        self.assertIsNone(result)
        self.assertIsNone(database.get_capital_recommendation("Martin Trader"))


if __name__ == "__main__":
    unittest.main()
