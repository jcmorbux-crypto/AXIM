import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import money_studio


class StrategyCardTests(unittest.TestCase):
    def test_every_official_strategy_produces_a_card(self):
        cards = [money_studio.strategy_card(s) for s in money_studio.STRATEGIES]
        self.assertEqual(len(cards), 5)
        keys = {c["key"] for c in cards}
        self.assertEqual(keys, {
            "capital_preservation", "growth_accelerator", "alternating_compound",
            "recovery_ladder", "daily_compounding",
        })

    def test_card_never_leaks_the_internal_function_fields(self):
        card = money_studio.strategy_card(money_studio.CAPITAL_PRESERVATION)
        self.assertNotIn("_worked_example_fn", card)
        self.assertNotIn("_timeline_fn", card)


class StrategyDetailTests(unittest.TestCase):
    def test_unknown_key_returns_none(self):
        self.assertIsNone(money_studio.strategy_detail("not_a_real_strategy"))

    def test_detail_includes_worked_example_and_growth_timeline(self):
        detail = money_studio.strategy_detail("capital_preservation")
        self.assertIn("worked_example", detail)
        self.assertIn("growth_timeline", detail)
        self.assertNotIn("_worked_example_fn", detail)


class CapitalPreservationMathTests(unittest.TestCase):
    def test_stake_is_1_percent_of_bankroll(self):
        example = money_studio._capital_preservation_worked_example(1000.0)
        self.assertEqual(example["stake"], 10.0)

    def test_a_win_vaults_25_percent_of_the_profit(self):
        example = money_studio._capital_preservation_worked_example(1000.0)
        profit = example["win"]["profit"]
        self.assertAlmostEqual(example["win"]["vault_amount"], round(profit * 0.25, 2))
        self.assertAlmostEqual(example["win"]["active_gain"], round(profit * 0.75, 2))

    def test_growth_checkpoint_fires_at_plus_125_percent(self):
        timeline = money_studio._capital_preservation_timeline(1000.0)
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["baseline_after"], 2250.0)
        self.assertEqual(timeline[0]["new_stake"], 22.5)  # 1% of the new $2,250 baseline


class GrowthAcceleratorMathTests(unittest.TestCase):
    def test_stake_is_5_percent_of_bankroll(self):
        example = money_studio._growth_accelerator_worked_example(1000.0)
        self.assertEqual(example["stake"], 50.0)

    def test_first_checkpoint_recalculates_without_vaulting(self):
        timeline = money_studio._growth_accelerator_timeline(1000.0)
        first = timeline[0]
        self.assertEqual(first["baseline_after"], 2250.0)  # +125%
        self.assertEqual(first["vaulted_this_step"], 0.0)

    def test_second_checkpoint_vaults_25_percent_of_the_new_leg(self):
        timeline = money_studio._growth_accelerator_timeline(1000.0)
        second = timeline[1]
        # b1=2250 -> doubling to 4500 before vault; profit_since_b1=2250; vault=25% of that = 562.5
        self.assertEqual(second["vaulted_this_step"], 562.5)
        self.assertEqual(second["baseline_after"], 4500.0 - 562.5)


class AlternatingCompoundMathTests(unittest.TestCase):
    def test_cycle_is_the_fixed_four_trade_pattern(self):
        cycle = money_studio._alternating_compound_cycle(1000.0)
        percents = [c["risk_percent"] for c in cycle]
        self.assertEqual(percents, [2.5, 5.0, 2.5, 5.0])

    def test_cycle_is_never_affected_by_trade_outcome(self):
        # The cycle function takes only a bankroll - there is no "outcome" input at all,
        # which is itself the guarantee that it can never be martingale-like.
        cycle_a = money_studio._alternating_compound_cycle(1000.0)
        cycle_b = money_studio._alternating_compound_cycle(1000.0)
        self.assertEqual(cycle_a, cycle_b)

    def test_growth_checkpoint_fires_at_plus_50_percent(self):
        timeline = money_studio._alternating_compound_timeline(1000.0)
        self.assertEqual(timeline[0]["baseline_after"], 1500.0)


class RecoveryLadderMathTests(unittest.TestCase):
    def test_ladder_steps_compound_by_the_multiplier(self):
        ladder = money_studio._recovery_ladder_table(1000.0, multiplier=2.0, max_steps=3)
        stakes = [step["stake"] for step in ladder]
        self.assertEqual(stakes, [10.0, 20.0, 40.0, 80.0])

    def test_last_step_is_flagged_as_the_max(self):
        ladder = money_studio._recovery_ladder_table(1000.0, multiplier=2.0, max_steps=3)
        self.assertFalse(ladder[0]["is_max"])
        self.assertTrue(ladder[3]["is_max"])

    def test_growth_checkpoint_fires_at_plus_100_percent(self):
        timeline = money_studio._recovery_ladder_timeline(1000.0)
        self.assertEqual(timeline[0]["baseline_after"], 2000.0)
        self.assertEqual(timeline[0]["new_stake"], 20.0)  # 1% of the new $2,000 baseline


class RiskProfileFieldsForTests(unittest.TestCase):
    def test_unknown_strategy_returns_all_none(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("not_real", "x", 1000.0)
        self.assertIsNone(create)
        self.assertIsNone(martingale)
        self.assertIsNone(vault)
        self.assertIsNone(compounding)
        self.assertIsNone(daily)

    def test_capital_preservation_maps_to_1_percent_and_a_vault_no_martingale(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("capital_preservation", "My Fund", 1000.0)
        self.assertEqual(create["percent_of_bankroll"], 1.0)
        self.assertEqual(create["strategy_key"], "capital_preservation")
        self.assertIsNone(martingale)
        self.assertEqual(vault, {"enabled": True, "vault_percent": 25, "trigger_event": "per_trade"})
        self.assertIsNone(compounding)
        self.assertIsNone(daily)

    def test_growth_accelerator_maps_to_5_percent_and_a_vault_no_martingale(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("growth_accelerator", "My Fund", 1000.0)
        self.assertEqual(create["percent_of_bankroll"], 5.0)
        self.assertIsNone(martingale)
        self.assertIsNotNone(vault)
        self.assertIsNone(compounding)
        self.assertIsNone(daily)

    def test_alternating_compound_maps_to_a_real_alternating_cycle_no_vault_no_martingale(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("alternating_compound", "My Fund", 1000.0)
        self.assertEqual(create["percent_of_bankroll"], 2.5)  # trade 1's percent, the cycle's own first step
        self.assertIsNone(martingale)
        self.assertIsNone(vault)
        self.assertEqual(compounding["mode"], "alternating_cycle")
        self.assertEqual(json.loads(compounding["steps_json"]), [2.5, 5.0, 2.5, 5.0])
        self.assertIsNone(daily)

    def test_recovery_ladder_maps_to_1_percent_with_real_martingale_no_vault(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("recovery_ladder", "My Fund", 1000.0)
        self.assertEqual(create["percent_of_bankroll"], 1.0)
        self.assertEqual(martingale, {
            "enabled": True, "max_steps": money_studio.DEFAULT_RECOVERY_MAX_STEPS,
            "multiplier": money_studio.DEFAULT_RECOVERY_MULTIPLIER, "reset_after_win": True,
        })
        self.assertIsNone(vault)
        self.assertIsNone(compounding)
        self.assertIsNone(daily)

    def test_daily_compounding_maps_to_the_daily_compounding_sizing_mode(self):
        create, martingale, vault, compounding, daily = money_studio.risk_profile_fields_for("daily_compounding", "My Fund", 1000.0)
        self.assertEqual(create["sizing_mode"], "daily_compounding")
        self.assertEqual(create["strategy_key"], "daily_compounding")
        self.assertIsNone(martingale)
        self.assertIsNone(vault)
        self.assertIsNone(compounding)
        self.assertEqual(daily, {
            "enabled": True, "risk_percent": money_studio.DAILY_COMPOUNDING_RISK_PERCENT,
            "profit_target_percent": money_studio.DAILY_COMPOUNDING_PROFIT_TARGET_PERCENT,
            "loss_limit_percent": money_studio.DAILY_COMPOUNDING_LOSS_LIMIT_PERCENT,
            "timezone": "UTC", "stop_after_target": True, "stop_after_loss_limit": True,
        })


class DailyCompoundingMathTests(unittest.TestCase):
    def test_stake_is_1_percent_of_bankroll(self):
        example = money_studio._daily_compounding_worked_example(1000.0)
        self.assertEqual(example["stake"], 10.0)

    def test_daily_profit_target_is_50_percent(self):
        example = money_studio._daily_compounding_worked_example(1000.0)
        self.assertEqual(example["daily_profit_target"], 500.0)

    def test_daily_loss_limit_is_25_percent(self):
        example = money_studio._daily_compounding_worked_example(1000.0)
        self.assertEqual(example["daily_loss_limit"], 250.0)

    def test_timeline_describes_a_calendar_reset_not_a_growth_checkpoint(self):
        timeline = money_studio._daily_compounding_timeline(1000.0)
        self.assertEqual(len(timeline), 1)
        self.assertIn("trading day", timeline[0]["trigger"])


class BuildVirtualProfileTests(unittest.TestCase):
    """2026-07-19 architecture: the 5 canonical plans are code-only -
    build_virtual_profile is the zero-DB-footprint stand-in for what a
    real risk_profiles row used to be. These tests guard the contract
    every consumer (risk_engine.py, risk_control_center.py,
    backtest_engine.py) relies on: the same shape database.
    get_risk_profile() returns, with no real DB row ever created."""

    def test_returns_none_for_an_unknown_key(self):
        self.assertIsNone(money_studio.build_virtual_profile("not_a_real_strategy"))

    def test_shape_matches_a_real_risk_profile_dict(self):
        profile = money_studio.build_virtual_profile("capital_preservation")
        expected_top_level = {
            "id", "name", "description", "is_template", "bankroll", "sizing_mode",
            "fixed_amount", "percent_of_bankroll", "kelly_win_rate_estimate",
            "kelly_payout_estimate", "kelly_fraction_multiplier", "max_trade_amount",
            "max_daily_loss", "max_session_loss", "profit_target", "max_trades",
            "live_allowed", "created_at", "updated_at", "strategy_key", "archived_at",
            "martingale", "momentum", "compounding", "profit_vault", "apex_ascension",
            "drawdown_protection", "cashflow", "strike", "fortress", "empire",
            "daily_compounding", "is_virtual",
        }
        self.assertEqual(set(profile.keys()), expected_top_level)
        for sub in ("martingale", "momentum", "compounding", "profit_vault", "apex_ascension",
                    "drawdown_protection", "cashflow", "strike", "fortress", "empire",
                    "daily_compounding"):
            self.assertIn("enabled" if sub != "compounding" else "mode", profile[sub])
            self.assertIn("id", profile[sub])
            self.assertIn("risk_profile_id", profile[sub])

    def test_has_no_real_id_and_is_flagged_virtual(self):
        profile = money_studio.build_virtual_profile("growth_accelerator")
        self.assertIsNone(profile["id"])
        self.assertTrue(profile["is_virtual"])
        self.assertEqual(profile["strategy_key"], "growth_accelerator")

    def test_bankroll_defaults_and_can_be_overridden(self):
        default = money_studio.build_virtual_profile("recovery_ladder")
        self.assertEqual(default["bankroll"], money_studio.STARTING_BANKROLL)
        overridden = money_studio.build_virtual_profile("recovery_ladder", bankroll=5000.0)
        self.assertEqual(overridden["bankroll"], 5000.0)

    def test_every_canonical_strategy_builds_a_valid_virtual_profile(self):
        for strategy in money_studio.STRATEGIES:
            profile = money_studio.build_virtual_profile(strategy["key"])
            self.assertIsNotNone(profile, strategy["key"])
            self.assertEqual(profile["name"], strategy["name"])

    def test_recovery_ladders_martingale_is_wired_the_only_layer_enabled(self):
        profile = money_studio.build_virtual_profile("recovery_ladder")
        self.assertTrue(profile["martingale"]["enabled"])
        for sub in ("momentum", "profit_vault", "apex_ascension",
                    "drawdown_protection", "cashflow", "strike", "fortress", "empire"):
            self.assertFalse(profile[sub]["enabled"])
        self.assertEqual(profile["compounding"]["mode"], "disabled")

    def test_daily_compoundings_only_daily_layer_is_enabled(self):
        profile = money_studio.build_virtual_profile("daily_compounding")
        self.assertTrue(profile["daily_compounding"]["enabled"])
        self.assertEqual(profile["sizing_mode"], "daily_compounding")
        for sub in ("martingale", "momentum", "profit_vault", "apex_ascension",
                    "drawdown_protection", "cashflow", "strike", "fortress", "empire"):
            self.assertFalse(profile[sub]["enabled"])


if __name__ == "__main__":
    unittest.main()
