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
              roi_percent=20.0, win_rate=0.55, max_drawdown_percent=15.0, strategy_key="capital_preservation"):
    return {
        "id": strategy_id, "label": label,
        "profile_snapshot": {"strategy_key": strategy_key},
        "metrics": {
            "rank_overall": rank_overall, "max_drawdown_amount": max_drawdown_amount,
            "avg_trade_size": avg_trade_size, "roi_percent": roi_percent, "win_rate": win_rate,
            "max_drawdown_percent": max_drawdown_percent,
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


class ComputeRecommendationTests(unittest.TestCase):
    def test_full_recommendation_shape(self):
        report = {
            "run": {"id": 42},
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

    def test_returns_none_when_no_strategy_is_ranked(self):
        report = {"run": {"id": 1}, "strategies": []}
        self.assertIsNone(rec.compute_recommendation("X", report, trades_backtested=0))


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
