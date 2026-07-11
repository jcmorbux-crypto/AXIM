import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import capital_strategies_catalog as catalog
import capital_strategies as engine


class CatalogTests(unittest.TestCase):
    def test_seventeen_strategies_total(self):
        # 4 Foundry + 3 Summit + 5 Alpha + 4 Legacy + 1 standalone Phoenix,
        # exactly per spec.
        c = catalog.get_catalog()
        total = sum(len(h["strategies"]) for h in c["houses"]) + len(c["standalone"])
        self.assertEqual(total, 17)

    def test_four_houses(self):
        c = catalog.get_catalog()
        self.assertEqual({h["key"] for h in c["houses"]}, {"foundry", "summit", "alpha", "legacy"})

    def test_phoenix_is_standalone_not_in_any_house(self):
        c = catalog.get_catalog()
        standalone_keys = {s["key"] for s in c["standalone"]}
        self.assertIn("phoenix", standalone_keys)
        for house in c["houses"]:
            self.assertNotIn("phoenix", [s["key"] for s in house["strategies"]])

    def test_get_strategy_returns_house_info(self):
        strategy = catalog.get_strategy("foundation")
        self.assertEqual(strategy["house_info"]["key"], "foundry")

    def test_get_strategy_unknown_returns_none(self):
        self.assertIsNone(catalog.get_strategy("not_a_real_strategy"))

    def test_phase1_strategies_marked_implemented(self):
        for key in ["foundation", "titan_allocation", "cashflow", "strike", "apex_ascension", "sentinel", "dominion"]:
            strategy = catalog.get_strategy(key)
            self.assertTrue(strategy["implemented"], f"{key} should be marked implemented (Phase 1)")

    def test_simulate_supported_flag_matches_engine_size_funcs(self):
        # Prevents exactly the bug found during UI verification this
        # session: a strategy marked simulate_supported=True in the
        # catalog but with no matching entry in capital_strategies.
        # _SIZE_FUNCS would show a working "Run Simulation" button that's
        # guaranteed to 400 - this keeps the two lists honest against
        # each other rather than letting them silently drift apart.
        c = catalog.get_catalog()
        all_strategies = [s for h in c["houses"] for s in h["strategies"]] + c["standalone"]
        catalog_supported = {s["key"] for s in all_strategies if s["simulate_supported"]}
        self.assertEqual(catalog_supported, engine.SIMULATABLE_STRATEGIES)

    def test_phase2_strategies_marked_implemented(self):
        # Momentum/Empire/Fortress are real now (wired into
        # core/risk_engine.py's compute_position_size/on_trade_closed),
        # not catalog-only. Empire is fully self-contained (its own
        # ladder, no external base_amount needed) so it DOES fit the
        # quick simulator; Momentum/Fortress are post-processing layers
        # that need a base_amount from a different sizing mode's
        # settings, which the single-strategy demo has no honest source
        # for - that's a real, documented gap, not a bug, and the UI
        # shows the "implemented elsewhere" banner for exactly this case.
        for key in ["momentum", "fortress"]:
            strategy = catalog.get_strategy(key)
            self.assertTrue(strategy["implemented"], f"{key} should be marked implemented (Phase 2)")
            self.assertFalse(strategy["simulate_supported"], f"{key} isn't in the quick simulator yet")
        empire = catalog.get_strategy("empire")
        self.assertTrue(empire["implemented"])
        self.assertTrue(empire["simulate_supported"], "empire is self-contained and should be quick-simulatable")

    def test_every_strategy_has_required_display_fields(self):
        c = catalog.get_catalog()
        all_strategies = [s for h in c["houses"] for s in h["strategies"]] + c["standalone"]
        for s in all_strategies:
            for field in ["name", "philosophy", "tagline", "risk_level", "key"]:
                self.assertIn(field, s)
                self.assertTrue(s[field], f"{s.get('key')}.{field} should not be empty")


if __name__ == "__main__":
    unittest.main()
