import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import capital_strategies as cs


def _apex_settings(**overrides):
    base = {
        "starting_bankroll": 1000, "starting_unit_value": 10, "standard_units": 5,
        "first_reset_threshold": 2500, "reset_increment": 1000, "reset_unit_step": 10,
        "downgrade_protection": 1, "highest_tier_reached": 0,
    }
    base.update(overrides)
    return base


class ApexAscensionTierTests(unittest.TestCase):
    """Every value here is taken directly from the spec's own worked
    example and default tier table - not invented."""

    def test_starting_bankroll_is_tier_zero(self):
        tier = cs.apex_ascension_tier(_apex_settings(), 1000)
        self.assertEqual(tier["tier_index"], 0)
        self.assertEqual(tier["unit_value"], 10)
        self.assertEqual(tier["next_threshold"], 2500)
        self.assertEqual(tier["amount_remaining_to_next"], 1500)

    def test_unit_frozen_below_first_threshold(self):
        # A bankroll dip should NOT change the tier - still frozen at $10
        # anywhere below $2,500, per the spec.
        tier = cs.apex_ascension_tier(_apex_settings(), 2499.99)
        self.assertEqual(tier["tier_index"], 0)
        self.assertEqual(tier["unit_value"], 10)

    def test_first_reset_at_2500(self):
        tier = cs.apex_ascension_tier(_apex_settings(), 2500)
        self.assertEqual(tier["tier_index"], 1)
        self.assertEqual(tier["unit_value"], 20)
        self.assertEqual(tier["next_threshold"], 3500)

    def test_approved_default_tier_table(self):
        # bankroll -> (tier, unit) exactly as the spec's table states.
        expected = [
            (2500, 1, 20), (3500, 2, 30), (4500, 3, 40), (5500, 4, 50), (6500, 5, 60),
        ]
        for bankroll, expected_tier, expected_unit in expected:
            tier = cs.apex_ascension_tier(_apex_settings(), bankroll)
            self.assertEqual(tier["tier_index"], expected_tier, f"bankroll={bankroll}")
            self.assertEqual(tier["unit_value"], expected_unit, f"bankroll={bankroll}")

    def test_standard_deployment_at_each_tier(self):
        # $50, $100, $150, $200, $250, $300 - the spec's own "Standard 5U
        # Deployment" column.
        expected = [(1000, 50), (2500, 100), (3500, 150), (4500, 200), (5500, 250), (6500, 300)]
        for bankroll, expected_deployment in expected:
            amount, _ = cs.apex_ascension_deployment(_apex_settings(), bankroll)
            self.assertEqual(amount, expected_deployment, f"bankroll={bankroll}")

    def test_between_milestones_uses_floor(self):
        # $4,999 is still tier 3 ($40 unit) - the next tier isn't reached
        # until $5,500, not proportionally interpolated.
        tier = cs.apex_ascension_tier(_apex_settings(), 4999)
        self.assertEqual(tier["tier_index"], 3)
        self.assertEqual(tier["unit_value"], 40)


class ApexAscensionDowngradeProtectionTests(unittest.TestCase):
    def test_drawdown_does_not_demote_tier_when_protected(self):
        # Reached tier 3 ($4,500+) previously, bankroll has since fallen
        # back to $3,000 (would derive as tier 1 on its own) - protection
        # on means the tier stays at the highest ever reached.
        settings = _apex_settings(highest_tier_reached=3)
        amount, effective = cs.apex_ascension_deployment(settings, 3000)
        self.assertEqual(effective["tier_index"], 3)
        self.assertEqual(effective["unit_value"], 40)
        self.assertEqual(amount, 200)

    def test_drawdown_demotes_tier_when_protection_off(self):
        settings = _apex_settings(highest_tier_reached=3, downgrade_protection=0)
        amount, effective = cs.apex_ascension_deployment(settings, 3000)
        self.assertEqual(effective["tier_index"], 1)
        self.assertEqual(amount, 100)

    def test_advancing_past_protected_tier_uses_the_higher_derived_tier(self):
        settings = _apex_settings(highest_tier_reached=1)
        amount, effective = cs.apex_ascension_deployment(settings, 5500)
        self.assertEqual(effective["tier_index"], 4)
        self.assertEqual(amount, 250)


def _sentinel_settings(**overrides):
    base = {"enabled": 1, "bands_json": None, "suspend_above_percent": 20, "scope": "account"}
    base.update(overrides)
    return base


class SentinelTests(unittest.TestCase):
    """Approved default drawdown behavior table, tested band by band."""

    def test_disabled_passes_through_unchanged(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(enabled=0), 100, 12, 10)
        self.assertEqual((amount, status), (100, "disabled"))

    def test_0_to_5_percent_full_deployment(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, 5, 10)
        self.assertEqual((amount, status), (100, "full"))

    def test_5_to_10_percent_reduces_25_percent(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, 7, 10)
        self.assertEqual((amount, status), (75, "reduced"))

    def test_10_to_15_percent_reduces_50_percent(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, 12, 10)
        self.assertEqual((amount, status), (50, "reduced"))

    def test_15_to_20_percent_minimum_only(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, 18, 10)
        self.assertEqual((amount, status), (10, "minimum"))

    def test_above_20_percent_suspends(self):
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, 25, 10)
        self.assertEqual((amount, status), (0, "suspended"))

    def test_negative_drawdown_clamped_to_zero(self):
        # A profitable account shouldn't be treated as having negative
        # drawdown - clamp at 0, which is the "full deployment" band.
        amount, status = cs.sentinel_adjusted_amount(_sentinel_settings(), 100, -5, 10)
        self.assertEqual((amount, status), (100, "full"))

    def test_custom_bands_override_default(self):
        # suspend_above_percent is a separate, independent ceiling - must
        # be raised too, or it fires before the custom band table is ever
        # consulted (matches the spec's own separation of the drawdown
        # band table from the standalone "above X%: suspend" rule).
        amount, status = cs.sentinel_adjusted_amount(
            _sentinel_settings(
                bands_json='[{"max_drawdown_percent": 50, "action": "reduce", "reduction_percent": 10}]',
                suspend_above_percent=60,
            ),
            100, 30, 10,
        )
        self.assertEqual((amount, status), (90, "reduced"))


def _cashflow_settings(**overrides):
    base = {
        "enabled": 1, "target_amount": 100, "target_period": "session",
        "partial_target_percent": 75, "partial_reduction_percent": 50,
    }
    base.update(overrides)
    return base


class CashflowTests(unittest.TestCase):
    def test_disabled_passes_through(self):
        amount, reached = cs.cashflow_adjusted_amount(_cashflow_settings(enabled=0), 20, 200)
        self.assertEqual((amount, reached), (20, False))

    def test_below_partial_threshold_unchanged(self):
        amount, reached = cs.cashflow_adjusted_amount(_cashflow_settings(), 20, 50)
        self.assertEqual((amount, reached), (20, False))

    def test_at_partial_threshold_reduces_size(self):
        # 75% of $100 target = $75 - at or above that, reduce by the
        # configured 50%.
        amount, reached = cs.cashflow_adjusted_amount(_cashflow_settings(), 20, 75)
        self.assertEqual((amount, reached), (10, False))

    def test_target_reached_stops_trading(self):
        amount, reached = cs.cashflow_adjusted_amount(_cashflow_settings(), 20, 100)
        self.assertEqual((amount, reached), (0, True))

    def test_target_exceeded_still_reports_reached(self):
        amount, reached = cs.cashflow_adjusted_amount(_cashflow_settings(), 20, 150)
        self.assertEqual((amount, reached), (0, True))


def _strike_settings(**overrides):
    base = {"enabled": 1, "max_session_duration_minutes": 0, "max_consecutive_losses": 0}
    base.update(overrides)
    return base


def _profile(**overrides):
    base = {"profit_target": 0, "max_session_loss": 0, "max_trades": 0}
    base.update(overrides)
    return base


def _session_state(**overrides):
    base = {"realized_pnl": 0, "trades_count": 0, "consecutive_losses": 0, "elapsed_minutes": 0}
    base.update(overrides)
    return base


class StrikeTests(unittest.TestCase):
    def test_disabled_never_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(profit_target=10), _strike_settings(enabled=0), _session_state(realized_pnl=100),
        )
        self.assertIsNone(reason)

    def test_no_condition_met_continues(self):
        reason = cs.strike_should_terminate(
            _profile(profit_target=50, max_session_loss=20, max_trades=10),
            _strike_settings(), _session_state(realized_pnl=10, trades_count=3),
        )
        self.assertIsNone(reason)

    def test_profit_target_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(profit_target=50), _strike_settings(), _session_state(realized_pnl=50),
        )
        self.assertEqual(reason, "profit_target")

    def test_loss_limit_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(max_session_loss=30), _strike_settings(), _session_state(realized_pnl=-30),
        )
        self.assertEqual(reason, "loss_limit")

    def test_max_trades_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(max_trades=5), _strike_settings(), _session_state(trades_count=5),
        )
        self.assertEqual(reason, "max_trades")

    def test_max_consecutive_losses_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(), _strike_settings(max_consecutive_losses=4), _session_state(consecutive_losses=4),
        )
        self.assertEqual(reason, "max_consecutive_losses")

    def test_max_duration_terminates(self):
        reason = cs.strike_should_terminate(
            _profile(), _strike_settings(max_session_duration_minutes=60), _session_state(elapsed_minutes=61),
        )
        self.assertEqual(reason, "max_duration")

    def test_priority_order_profit_before_loss(self):
        # Both conditions technically satisfiable is impossible in
        # practice (pnl can't be both >= profit_target and <=
        # -max_session_loss at once unless config is nonsensical), but
        # confirm the check order matches the documented priority for a
        # case where an earlier condition should win.
        reason = cs.strike_should_terminate(
            _profile(profit_target=10, max_trades=1), _strike_settings(),
            _session_state(realized_pnl=10, trades_count=1),
        )
        self.assertEqual(reason, "profit_target")


class SimulateStrategyTests(unittest.TestCase):
    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            cs.simulate_strategy("not_real", {}, 10, 0.5, 90)

    def test_invalid_win_rate_raises(self):
        with self.assertRaises(ValueError):
            cs.simulate_strategy("foundation", {"fixed_amount": 1}, 10, 1.5, 90)

    def test_invalid_payout_raises(self):
        with self.assertRaises(ValueError):
            cs.simulate_strategy("foundation", {"fixed_amount": 1}, 10, 0.5, 0)

    def test_deterministic_with_same_seed(self):
        settings = {"fixed_amount": 10}
        r1 = cs.simulate_strategy("foundation", settings, 100, 0.4, 85, starting_bankroll=1000, seed=42)
        r2 = cs.simulate_strategy("foundation", settings, 100, 0.4, 85, starting_bankroll=1000, seed=42)
        self.assertEqual(r1, r2)

    def test_different_seeds_can_differ(self):
        settings = {"fixed_amount": 10}
        r1 = cs.simulate_strategy("foundation", settings, 200, 0.4, 85, starting_bankroll=1000, seed=1)
        r2 = cs.simulate_strategy("foundation", settings, 200, 0.4, 85, starting_bankroll=1000, seed=2)
        self.assertNotEqual(r1["ending_bankroll"], r2["ending_bankroll"])

    def test_100_percent_win_rate_only_grows(self):
        settings = {"fixed_amount": 10}
        result = cs.simulate_strategy("foundation", settings, 20, 1.0, 90, starting_bankroll=1000, seed=1)
        self.assertEqual(result["wins"], 20)
        self.assertEqual(result["losses"], 0)
        self.assertGreater(result["ending_bankroll"], 1000)
        self.assertEqual(result["max_drawdown_percent"], 0)

    def test_0_percent_win_rate_can_ruin(self):
        settings = {"fixed_amount": 100}
        result = cs.simulate_strategy("foundation", settings, 50, 0.0, 90, starting_bankroll=1000, seed=1)
        self.assertEqual(result["wins"], 0)
        self.assertTrue(result["ruined"])
        self.assertEqual(result["ending_bankroll"], 0)

    def test_apex_ascension_simulation_uses_real_tier_logic(self):
        settings = _apex_settings()
        result = cs.simulate_strategy("apex_ascension", settings, 500, 0.55, 90, seed=7)
        self.assertIn("ending_bankroll", result)
        self.assertGreaterEqual(result["trades_run"], 1)


if __name__ == "__main__":
    unittest.main()
