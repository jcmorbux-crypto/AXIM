import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

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


if __name__ == "__main__":
    unittest.main()
