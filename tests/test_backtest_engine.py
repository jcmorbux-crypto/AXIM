import io
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import backtest_engine
import risk_engine
import capital_strategies


def _signal(ts, result, source_type="imported", signal_id=1, asset="EUR/USD OTC", direction="BUY", payout_percent=85):
    return {
        "source_type": source_type, "signal_id": signal_id, "channel": "Test",
        "asset": asset, "direction": direction, "expiry": "1 Minute", "timestamp": ts,
        "result": result, "payout_percent": payout_percent, "profit_loss": None, "trade_amount": None,
    }


def _fixed_profile(fixed_amount=10, max_trade_amount=0, profit_target=0, max_session_loss=0, max_trades=0):
    return {
        "sizing_mode": "fixed", "bankroll": 0, "fixed_amount": fixed_amount, "percent_of_bankroll": 0,
        "kelly_win_rate_estimate": None, "kelly_payout_estimate": None, "kelly_fraction_multiplier": 0.5,
        "max_trade_amount": max_trade_amount, "profit_target": profit_target, "max_session_loss": max_session_loss,
        "max_trades": max_trades,
        "martingale": {"enabled": False, "max_steps": 0, "multiplier": 2.0, "custom_ladder_json": None,
                       "reset_after_win": True, "reset_after_session": True, "max_total_exposure": 0},
        "compounding": {"mode": "disabled", "steps_json": None, "drawdown_reset_percent": 0,
                        "max_risk_percent": 0, "min_risk_percent": 0},
        "profit_vault": {"enabled": False, "vault_percent": 0, "trigger_event": "every_winning_session",
                          "milestone_amount": 0},
    }


class DbBackedTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class SimulateFixedSizingTests(unittest.TestCase):
    def test_simple_win_loss(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(result["trades"][0]["profit_loss"], 8.5)  # 10 * 0.85
        self.assertEqual(result["trades"][1]["profit_loss"], -10)
        self.assertAlmostEqual(result["sessions"][0]["realized_pnl"], -1.5, places=2)

    def test_draw_has_zero_profit_loss(self):
        pool = [_signal("2026-01-01T10:00:00", "draw")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(), 1000)
        self.assertEqual(result["trades"][0]["profit_loss"], 0)

    def test_sessions_grouped_by_day(self):
        pool = [
            _signal("2026-01-01T10:00:00", "win"),
            _signal("2026-01-01T11:00:00", "win"),
            _signal("2026-01-02T10:00:00", "loss"),
        ]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(), 1000, session_window="daily")
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(result["sessions"][0]["trades_count"], 2)
        self.assertEqual(result["sessions"][1]["trades_count"], 1)

    def test_session_window_all_is_one_session(self):
        pool = [
            _signal("2026-01-01T10:00:00", "win"),
            _signal("2026-01-05T10:00:00", "loss"),
        ]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(), 1000, session_window="all")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["trades_count"], 2)

    def test_default_payout_used_when_signal_has_none(self):
        pool = [_signal("2026-01-01T10:00:00", "win", payout_percent=None)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000, default_payout_percent=90)
        self.assertEqual(result["trades"][0]["profit_loss"], 9.0)


class StopConditionTests(unittest.TestCase):
    def test_profit_target_stops_session_early(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(5)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000, profit_target=15)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_target")
        self.assertLess(session["trades_count"], 5)

    def test_loss_limit_stops_session_early(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(5)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000, loss_limit=15)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_loss_limit")
        self.assertEqual(session["trades_count"], 2)  # -10, -20 -> stops at 2nd trade

    def test_max_trades_stops_session_early(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(5)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=1), 1000, max_trades=2)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_max_trades")
        self.assertEqual(session["trades_count"], 2)

    def test_no_stop_condition_completes_normally(self):
        pool = [_signal("2026-01-01T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(), 1000)
        self.assertEqual(result["sessions"][0]["status"], "completed")


class MartingaleSimulationTests(unittest.TestCase):
    def _martingale_profile(self, max_steps=5, multiplier=2.0, reset_after_win=True):
        profile = _fixed_profile(fixed_amount=10)
        profile["martingale"] = {
            "enabled": True, "max_steps": max_steps, "multiplier": multiplier, "custom_ladder_json": None,
            "reset_after_win": reset_after_win, "reset_after_session": True, "max_total_exposure": 0,
        }
        return profile

    def test_steps_up_after_loss(self):
        pool = [_signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:05:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, self._martingale_profile(), 1000)
        self.assertEqual(result["trades"][0]["trade_amount"], 10)   # step 0
        self.assertEqual(result["trades"][1]["trade_amount"], 20)   # step 1: 10 * 2^1

    def test_resets_after_win_when_configured(self):
        pool = [_signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:05:00", "win"),
                _signal("2026-01-01T10:10:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, self._martingale_profile(), 1000)
        self.assertEqual(result["trades"][0]["trade_amount"], 10)
        self.assertEqual(result["trades"][1]["trade_amount"], 20)  # still stepped up for the win trade itself
        self.assertEqual(result["trades"][2]["trade_amount"], 10)  # reset after the win

    def test_max_martingale_step_used_metric(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(4)]
        result = backtest_engine.simulate_strategy(pool, self._martingale_profile(max_steps=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(metrics["max_martingale_step_used"], 4)


class AlternatingCycleSimulationTests(unittest.TestCase):
    """Money Management Studio's Alternating Compound, replayed through
    the SAME shared risk_engine._base_amount the live path uses (see
    module docstring) - proves the real trade-by-trade 2.5%/5% cycle,
    not the old averaged-percent approximation, holds in backtests too."""

    def _alternating_profile(self, bankroll=1000, cycle=(2.5, 5.0, 2.5, 5.0)):
        import json
        profile = _fixed_profile(fixed_amount=10)
        profile["sizing_mode"] = "percent"
        profile["bankroll"] = bankroll
        profile["percent_of_bankroll"] = cycle[0]
        profile["compounding"] = {
            "mode": "alternating_cycle", "steps_json": json.dumps(list(cycle)),
            "drawdown_reset_percent": 0, "max_risk_percent": 0, "min_risk_percent": 0,
        }
        return profile

    def test_stake_follows_the_cycle_by_trade_count_not_pnl(self):
        pool = [
            _signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:05:00", "win"),
            _signal("2026-01-01T10:10:00", "loss"), _signal("2026-01-01T10:15:00", "win"),
            _signal("2026-01-01T10:20:00", "loss"),  # wraps back to the start of the cycle
        ]
        result = backtest_engine.simulate_strategy(pool, self._alternating_profile(), 1000)
        amounts = [t["trade_amount"] for t in result["trades"]]
        self.assertEqual(amounts, [25.0, 50.0, 25.0, 50.0, 25.0])

    def test_a_losing_streak_does_not_disrupt_the_cycle(self):
        # The whole point: unlike martingale or milestone-based
        # compounding, wins/losses never change which step comes next.
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(4)]
        result = backtest_engine.simulate_strategy(pool, self._alternating_profile(), 1000)
        amounts = [t["trade_amount"] for t in result["trades"]]
        self.assertEqual(amounts, [25.0, 50.0, 25.0, 50.0])


class VaultSimulationTests(unittest.TestCase):
    def test_milestone_based_skim(self):
        profile = _fixed_profile(fixed_amount=10)
        profile["profit_vault"] = {"enabled": True, "vault_percent": 50, "trigger_event": "milestone_based",
                                    "milestone_amount": 10}
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(3)]  # 8.5 each -> crosses $10 at trade 2
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        total_vaulted = sum(s["ending_vaulted_amount"] for s in result["sessions"])
        self.assertGreater(total_vaulted, 0)

    def test_every_winning_session_skim(self):
        profile = _fixed_profile(fixed_amount=10)
        profile["profit_vault"] = {"enabled": True, "vault_percent": 20, "trigger_event": "every_winning_session",
                                    "milestone_amount": 0}
        pool = [_signal("2026-01-01T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        self.assertAlmostEqual(result["sessions"][0]["ending_vaulted_amount"], 8.5 * 0.20, places=2)

    def test_no_vault_on_losing_session(self):
        profile = _fixed_profile(fixed_amount=10)
        profile["profit_vault"] = {"enabled": True, "vault_percent": 20, "trigger_event": "every_winning_session",
                                    "milestone_amount": 0}
        pool = [_signal("2026-01-01T10:00:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        self.assertEqual(result["sessions"][0]["ending_vaulted_amount"], 0)

    def test_vaulted_funds_excluded_from_next_session_bankroll(self):
        profile = _fixed_profile(fixed_amount=10)
        profile["sizing_mode"] = "dynamic"
        profile["percent_of_bankroll"] = 100  # simplifies: trade_amount == current bankroll
        profile["profit_vault"] = {"enabled": True, "vault_percent": 100, "trigger_event": "every_winning_session",
                                    "milestone_amount": 0}
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-02T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, profile, 100, session_window="daily")
        # session 2 starts with the vaulted profit fully excluded from the trading balance
        self.assertLess(result["sessions"][1]["starting_balance"], result["sessions"][0]["starting_balance"] + result["sessions"][0]["realized_pnl"])


class BankrollCarryForwardTests(unittest.TestCase):
    def test_percent_mode_grows_with_cumulative_pnl_across_sessions(self):
        profile = _fixed_profile()
        profile["sizing_mode"] = "percent"
        profile["percent_of_bankroll"] = 10
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-02T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000, session_window="daily")
        # session 2's starting_balance should reflect session 1's profit added in
        self.assertGreater(result["sessions"][1]["starting_balance"], result["sessions"][0]["starting_balance"])


class RiskEngineParityTests(unittest.TestCase):
    """Guards against the backtest engine's math silently drifting from
    core/risk_engine.py's real live math - both should agree exactly for
    a single trade with no cross-session bankroll effects in play."""

    def test_fixed_sizing_matches_risk_engine(self):
        profile = _fixed_profile(fixed_amount=15)
        session_state = {"realized_pnl": 0, "current_martingale_step": 0}
        direct = risk_engine._base_amount(profile, session_state)

        pool = [_signal("2026-01-01T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        self.assertEqual(result["trades"][0]["trade_amount"], round(direct, 2))

    def test_martingale_ladder_matches_risk_engine(self):
        profile = _fixed_profile(fixed_amount=10)
        profile["martingale"] = {"enabled": True, "max_steps": 5, "multiplier": 2.2, "custom_ladder_json": None,
                                  "reset_after_win": True, "reset_after_session": True, "max_total_exposure": 0}
        direct_step2 = risk_engine._apply_martingale(10, profile["martingale"], 2)

        pool = [_signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:05:00", "loss"),
                _signal("2026-01-01T10:10:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        # 3rd trade is placed AT step 2 (0-indexed steps advance after each loss)
        self.assertEqual(result["trades"][2]["trade_amount"], round(direct_step2, 2))


class CapitalStrategiesSimulationTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) layers (Momentum/Cashflow/Sentinel/
    Fortress/Empire) reused inside the backtest engine - same pure
    functions live sizing calls, per core/backtest_engine.py's own
    docstring commitment. A profile_snapshot missing these keys entirely
    (pre-existing snapshots saved before this feature existed) must keep
    working exactly as before - covered by every test above this class
    still passing with the bare _fixed_profile() fixture."""

    def _momentum_profile(self, max_steps=5, multiplier=1.5):
        profile = _fixed_profile(fixed_amount=10)
        profile["momentum"] = {
            "enabled": True, "max_steps": max_steps, "multiplier": multiplier,
            "custom_ladder_json": None, "profit_lock_percent": 0,
        }
        return profile

    def test_momentum_steps_up_after_win(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win")]
        result = backtest_engine.simulate_strategy(pool, self._momentum_profile(), 1000)
        self.assertEqual(result["trades"][0]["trade_amount"], 10)    # step 0
        self.assertEqual(result["trades"][1]["trade_amount"], 15)    # step 1: 10 * 1.5^1

    def test_momentum_resets_after_loss(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "loss"),
                _signal("2026-01-01T10:10:00", "win")]
        result = backtest_engine.simulate_strategy(pool, self._momentum_profile(), 1000)
        self.assertEqual(result["trades"][2]["trade_amount"], 10)  # back to step 0 after the loss

    def test_momentum_absent_from_profile_snapshot_is_a_pure_noop(self):
        # A profile_snapshot saved before Momentum existed has no
        # "momentum" key at all - must not KeyError, must behave exactly
        # like Momentum disabled.
        profile = _fixed_profile(fixed_amount=10)
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win")]
        result = backtest_engine.simulate_strategy(pool, profile, 1000)
        self.assertEqual(result["trades"][1]["trade_amount"], 10)

    def _cashflow_profile(self, target_amount=15):
        profile = _fixed_profile(fixed_amount=10)
        profile["cashflow"] = {
            "enabled": True, "target_amount": target_amount, "target_period": "session",
            "partial_target_percent": 75, "partial_reduction_percent": 50,
        }
        return profile

    def test_cashflow_target_stops_the_session(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(5)]  # 8.5 each
        result = backtest_engine.simulate_strategy(pool, self._cashflow_profile(target_amount=15), 1000)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_cashflow_target_reached")
        self.assertLess(session["trades_count"], 5)

    def _sentinel_profile(self, suspend_above_percent=20):
        profile = _fixed_profile(fixed_amount=10)
        profile["bankroll"] = 100
        profile["drawdown_protection"] = {
            "enabled": True, "bands_json": None, "suspend_above_percent": suspend_above_percent, "scope": "account",
        }
        return profile

    def test_sentinel_suspends_after_deep_drawdown(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(5)]
        result = backtest_engine.simulate_strategy(pool, self._sentinel_profile(suspend_above_percent=15), 100)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_sentinel_suspended")
        self.assertLess(session["trades_count"], 5)

    def _fortress_profile(self, fixed_amount=20, protection_threshold=15):
        profile = _fixed_profile(fixed_amount=fixed_amount)
        profile["fortress"] = {"enabled": True, "protection_threshold": protection_threshold, "protected_principal": 0}
        return profile

    def test_fortress_caps_stake_at_available_capital_once_protected(self):
        # $20/trade, 85% payout. Trade 0: $17 profit -> realized_pnl=17,
        # already over the $15 threshold, but the CHECK happens before
        # trade 0 opens (realized_pnl=0 then) so trade 0 is unaffected.
        # Trade 1: protection triggers (17 >= 15) with protected_principal
        # locked at starting_bankroll=1000; available = 1017-1000 = 17,
        # capping the nominal $20 stake down to exactly $17 - a real,
        # deterministic cap, not silently ignored (which is what happened
        # before this wiring existed - Fortress was invisible to backtests).
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(2)]
        result = backtest_engine.simulate_strategy(pool, self._fortress_profile(), 1000)
        trades = result["trades"]
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["trade_amount"], 20)   # not yet protected
        self.assertEqual(trades[1]["trade_amount"], 17)   # capped to available capital

    def _empire_profile(self, num_levels=3, failure_behavior="reset_to_start"):
        profile = _fixed_profile(fixed_amount=10)
        profile["sizing_mode"] = "empire"
        profile["empire"] = {
            "enabled": True, "starting_amount": 10, "target_amount": 40, "num_levels": num_levels,
            "levels_json": None, "failure_behavior": failure_behavior, "checkpoint_level": 0, "current_level": 0,
        }
        return profile

    def test_empire_stake_follows_the_ladder_on_wins(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win")]
        result = backtest_engine.simulate_strategy(pool, self._empire_profile(), 1000)
        ladder = capital_strategies.empire_generate_ladder(10, 40, 3)
        self.assertEqual(result["trades"][0]["trade_amount"], ladder[0])
        self.assertEqual(result["trades"][1]["trade_amount"], ladder[1])

    def test_empire_challenge_complete_stops_the_session_not_a_crash(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(5)]
        result = backtest_engine.simulate_strategy(pool, self._empire_profile(num_levels=2), 1000)
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_empire_challenge_complete")
        self.assertLess(session["trades_count"], 5)

    def test_empire_level_persists_across_sessions_not_session_scoped(self):
        # Unlike Martingale/Momentum, Empire's current_level is
        # profile-scoped in live AXIM (empire_settings, not
        # trading_sessions) - a win in session 1 should carry the ladder
        # level into session 2, not reset at the session boundary.
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-02T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, self._empire_profile(num_levels=5), 1000, session_window="daily")
        ladder = capital_strategies.empire_generate_ladder(10, 40, 5)
        self.assertEqual(result["sessions"][0]["trades"][0]["trade_amount"], ladder[0])
        self.assertEqual(result["sessions"][1]["trades"][0]["trade_amount"], ladder[1])

    def _strike_profile(self, max_consecutive_losses=0, max_session_duration_minutes=0):
        profile = _fixed_profile(fixed_amount=10)
        profile["strike"] = {
            "enabled": True, "max_consecutive_losses": max_consecutive_losses,
            "max_session_duration_minutes": max_session_duration_minutes,
        }
        return profile

    def test_strike_absent_from_profile_snapshot_is_a_pure_noop(self):
        # Pre-existing snapshots saved before Strike's backtest simulation
        # was added have no "strike" key at all - must keep working
        # exactly like the bare _fixed_profile() fixture, same as every
        # other Capital Strategies layer.
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(10)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        self.assertEqual(result["sessions"][0]["status"], "completed")
        self.assertEqual(result["sessions"][0]["trades_count"], 10)

    def test_consecutive_losses_streak_stops_the_session(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "loss") for i in range(5)]
        result = backtest_engine.simulate_strategy(
            pool, self._strike_profile(max_consecutive_losses=3), 1000,
        )
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_strike_max_consecutive_losses")
        self.assertEqual(session["trades_count"], 3)

    def test_a_win_breaks_the_streak(self):
        pool = [
            _signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:01:00", "loss"),
            _signal("2026-01-01T10:02:00", "win"),
            _signal("2026-01-01T10:03:00", "loss"), _signal("2026-01-01T10:04:00", "loss"),
        ]
        result = backtest_engine.simulate_strategy(
            pool, self._strike_profile(max_consecutive_losses=3), 1000,
        )
        session = result["sessions"][0]
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["trades_count"], 5)

    def test_a_draw_also_breaks_the_streak(self):
        pool = [
            _signal("2026-01-01T10:00:00", "loss"), _signal("2026-01-01T10:01:00", "loss"),
            _signal("2026-01-01T10:02:00", "draw"),
            _signal("2026-01-01T10:03:00", "loss"), _signal("2026-01-01T10:04:00", "loss"),
        ]
        result = backtest_engine.simulate_strategy(
            pool, self._strike_profile(max_consecutive_losses=3), 1000,
        )
        self.assertEqual(result["sessions"][0]["status"], "completed")

    def test_session_duration_cap_stops_the_session(self):
        pool = [
            _signal("2026-01-01T10:00:00", "win"),
            _signal("2026-01-01T10:15:00", "win"),
            _signal("2026-01-01T11:00:00", "win"),  # 60 minutes after the first
        ]
        result = backtest_engine.simulate_strategy(
            pool, self._strike_profile(max_session_duration_minutes=30), 1000,
        )
        session = result["sessions"][0]
        self.assertEqual(session["status"], "stopped_strike_max_duration")
        self.assertEqual(session["trades_count"], 3)  # stops AFTER the trade that crosses the cap

    def test_within_duration_cap_completes_normally(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win")]
        result = backtest_engine.simulate_strategy(
            pool, self._strike_profile(max_session_duration_minutes=30), 1000,
        )
        self.assertEqual(result["sessions"][0]["status"], "completed")


class MetricsTests(unittest.TestCase):
    def test_metrics_on_all_wins(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(3)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(metrics["win_rate"], 1.0)
        self.assertEqual(metrics["loss_rate"], 0.0)
        self.assertGreater(metrics["final_bankroll"], 1000)
        self.assertGreater(metrics["roi_percent"], 0)
        self.assertEqual(metrics["max_drawdown_percent"], 0)

    def test_max_drawdown_computed_correctly(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "loss"),
                _signal("2026-01-01T10:10:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=100), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertGreater(metrics["max_drawdown_percent"], 0)

    def test_longest_streaks(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", r) for i, r in
                enumerate(["win", "win", "win", "loss", "win", "loss", "loss"])]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(metrics["longest_win_streak"], 3)
        self.assertEqual(metrics["longest_loss_streak"], 2)

    def test_no_trades_produces_safe_defaults(self):
        metrics = backtest_engine.compute_metrics([], [], 1000)
        self.assertEqual(metrics["final_bankroll"], 1000)
        self.assertIsNone(metrics["win_rate"])
        self.assertEqual(metrics["avg_trade_size"], 0.0)

    def test_risk_score_and_best_for_label_are_deterministic(self):
        self.assertEqual(backtest_engine._risk_score(5, 0), "Low")
        self.assertEqual(backtest_engine._risk_score(15, 2), "Medium")
        self.assertEqual(backtest_engine._risk_score(30, 5), "High")
        self.assertEqual(backtest_engine._best_for_label(5, 5), "Capital Preservation")
        self.assertEqual(backtest_engine._best_for_label(80, 20), "Aggressive Growth")

    def test_profit_factor_all_wins_has_no_losses_to_divide_by(self):
        pool = [_signal(f"2026-01-01T10:0{i}:00", "win") for i in range(3)]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertGreater(metrics["profit_factor"], 0)

    def test_profit_factor_mixed_results(self):
        # 2 wins @ $8.5 profit each (85% payout on $10), 1 loss @ -$10
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win"),
                _signal("2026-01-01T10:10:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        # gross_profit=17.0, gross_loss=10.0 -> profit_factor=1.7
        self.assertAlmostEqual(metrics["profit_factor"], 1.7, places=2)

    def test_consistency_percent_across_multiple_sessions(self):
        # Two daily sessions: day 1 all wins (profitable), day 2 all losses (not profitable)
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "win"),
                _signal("2026-01-02T10:00:00", "loss"), _signal("2026-01-02T10:05:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(metrics["consistency_percent"], 50.0)

    def test_recovery_factor_relates_profit_to_max_drawdown(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-01T10:05:00", "loss"),
                _signal("2026-01-01T10:10:00", "win"), _signal("2026-01-01T10:15:00", "win")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertGreater(metrics["max_drawdown_amount"], 0)
        self.assertIsNotNone(metrics["recovery_factor"])
        self.assertAlmostEqual(
            metrics["recovery_factor"],
            round(metrics["total_profit_loss"] / metrics["max_drawdown_amount"], 2),
        )

    def test_sharpe_like_score_needs_at_least_two_sessions(self):
        pool = [_signal("2026-01-01T10:00:00", "win")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertIsNone(metrics["sharpe_like_score"])

    def test_sharpe_like_score_computed_with_multiple_sessions(self):
        pool = [_signal("2026-01-01T10:00:00", "win"), _signal("2026-01-02T10:00:00", "win"),
                _signal("2026-01-03T10:00:00", "loss")]
        result = backtest_engine.simulate_strategy(pool, _fixed_profile(fixed_amount=10), 1000)
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertIsNotNone(metrics["sharpe_like_score"])
        self.assertGreater(metrics["volatility"], 0)


class RankStrategiesTests(unittest.TestCase):
    def test_ranks_best_roi_and_lowest_drawdown(self):
        strategy_metrics = [
            (1, {"roi_percent": 10, "max_drawdown_percent": 20, "win_rate": 0.5}),
            (2, {"roi_percent": 50, "max_drawdown_percent": 5, "win_rate": 0.6}),
            (3, {"roi_percent": -5, "max_drawdown_percent": 40, "win_rate": 0.4}),
        ]
        ranks = backtest_engine.rank_strategies(strategy_metrics)
        self.assertEqual(ranks[2]["rank_highest_growth"], 1)
        self.assertEqual(ranks[2]["rank_safest"], 1)
        self.assertEqual(ranks[2]["rank_overall"], 1)
        self.assertEqual(ranks[3]["rank_highest_growth"], 3)

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual(backtest_engine.rank_strategies([]), {})

    def test_constant_values_do_not_crash(self):
        strategy_metrics = [
            (1, {"roi_percent": 10, "max_drawdown_percent": 10, "win_rate": 0.5}),
            (2, {"roi_percent": 10, "max_drawdown_percent": 10, "win_rate": 0.5}),
        ]
        ranks = backtest_engine.rank_strategies(strategy_metrics)
        self.assertIn(1, ranks)
        self.assertIn(2, ranks)


class CsvImportParsingTests(unittest.TestCase):
    def test_parses_well_formed_csv(self):
        csv_text = "channel,asset,direction,expiry,timestamp,result,payout\n" \
                   "Alpha,EUR/USD OTC,BUY,1 Minute,2026-01-01T10:00:00,win,85\n" \
                   "Alpha,GBP/USD OTC,SELL,1 Minute,2026-01-01T10:05:00,loss,\n"
        rows, errors = backtest_engine.parse_signal_csv(csv_text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["payout_percent"], 85.0)
        self.assertIsNone(rows[1]["payout_percent"])

    def test_missing_required_column(self):
        rows, errors = backtest_engine.parse_signal_csv("channel,result\nAlpha,win\n")
        self.assertEqual(rows, [])
        self.assertTrue(errors)
        self.assertIn("missing required column", errors[0]["message"])

    def test_invalid_result_reported_per_line_not_fatal(self):
        csv_text = "asset,direction,timestamp,result\n" \
                   "EUR/USD,BUY,2026-01-01T10:00:00,win\n" \
                   "GBP/USD,SELL,2026-01-01T10:05:00,maybe\n"
        rows, errors = backtest_engine.parse_signal_csv(csv_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["line"], 3)

    def test_blank_asset_reported_as_error(self):
        csv_text = "asset,direction,timestamp\n,BUY,2026-01-01T10:00:00\n"
        rows, errors = backtest_engine.parse_signal_csv(csv_text)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)

    def test_empty_file(self):
        rows, errors = backtest_engine.parse_signal_csv("")
        self.assertEqual(rows, [])
        self.assertTrue(errors)

    def test_column_aliases_case_insensitive(self):
        csv_text = "Symbol,Side,Date\nEUR/USD,BUY,2026-01-01T10:00:00\n"
        rows, errors = backtest_engine.parse_signal_csv(csv_text)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["asset"], "EUR/USD")
        self.assertEqual(rows[0]["direction"], "BUY")


def _make_xlsx_bytes(header, data_rows):
    """Builds a real, genuine .xlsx file in memory (not a mock/fake) -
    same discipline as parsing real CSV text above, just for the binary
    format. Returns raw bytes, matching what parse_signal_excel actually
    receives from the base64-decoded upload."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in data_rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ExcelImportParsingTests(unittest.TestCase):
    """parse_signal_excel - mirrors CsvImportParsingTests exactly (same
    shared _parse_signal_row underneath), covering what's specific to
    the Excel path: real .xlsx bytes, native cell types (datetime
    objects, numbers) instead of pre-stringified CSV text, and blank
    trailing rows."""

    def test_parses_well_formed_workbook(self):
        xlsx_bytes = _make_xlsx_bytes(
            ["channel", "asset", "direction", "expiry", "timestamp", "result", "payout"],
            [
                ["Alpha", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00", "win", 85],
                ["Alpha", "GBP/USD OTC", "SELL", "1 Minute", "2026-01-01T10:05:00", "loss", None],
            ],
        )
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["payout_percent"], 85.0)
        self.assertIsNone(rows[1]["payout_percent"])

    def test_native_datetime_cell_is_normalized(self):
        """A real spreadsheet date cell (openpyxl gives back a Python
        datetime, not a string) must work the same as typed text."""
        xlsx_bytes = _make_xlsx_bytes(
            ["asset", "direction", "timestamp"],
            [["EUR/USD", "BUY", datetime(2026, 1, 1, 10, 0, 0)]],
        )
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["received_at"].startswith("2026-01-01"))

    def test_missing_required_column(self):
        xlsx_bytes = _make_xlsx_bytes(["channel", "result"], [["Alpha", "win"]])
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(rows, [])
        self.assertTrue(errors)
        self.assertIn("missing required column", errors[0]["message"])

    def test_invalid_result_reported_per_line_not_fatal(self):
        xlsx_bytes = _make_xlsx_bytes(
            ["asset", "direction", "timestamp", "result"],
            [
                ["EUR/USD", "BUY", "2026-01-01T10:00:00", "win"],
                ["GBP/USD", "SELL", "2026-01-01T10:05:00", "maybe"],
            ],
        )
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["line"], 3)

    def test_blank_trailing_row_is_skipped_not_an_error(self):
        """A common real-world artifact - a spreadsheet with a fully
        blank row after the data (leftover formatting, an extra
        newline) - should be silently skipped, not reported as a
        blank-required-field error the way a genuinely bad data row
        would be."""
        xlsx_bytes = _make_xlsx_bytes(
            ["asset", "direction", "timestamp"],
            [
                ["EUR/USD", "BUY", "2026-01-01T10:00:00"],
                [None, None, None],
            ],
        )
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, [])

    def test_empty_workbook(self):
        xlsx_bytes = _make_xlsx_bytes([], [])
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(rows, [])
        self.assertTrue(errors)

    def test_not_a_real_xlsx_file_reports_a_clean_error(self):
        rows, errors = backtest_engine.parse_signal_excel(b"this is not an xlsx file")
        self.assertEqual(rows, [])
        self.assertTrue(errors)
        self.assertIn("could not read Excel file", errors[0]["message"])

    def test_column_aliases_case_insensitive(self):
        xlsx_bytes = _make_xlsx_bytes(["Symbol", "Side", "Date"], [["EUR/USD", "BUY", "2026-01-01T10:00:00"]])
        rows, errors = backtest_engine.parse_signal_excel(xlsx_bytes)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["asset"], "EUR/USD")
        self.assertEqual(rows[0]["direction"], "BUY")


class RunBacktestIntegrationTests(DbBackedTestCase):
    def test_full_run_end_to_end(self):
        for i in range(5):
            sig_id = database.create_imported_signal(
                "TestChannel", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-01T10:0{i}:00")
            database.grade_imported_signal(sig_id, "win" if i % 2 == 0 else "loss", payout_percent=85)

        profile_id = database.create_risk_profile("Test Fixed", sizing_mode="fixed", fixed_amount=10)
        run_id = database.create_backtest_run(
            "Integration Test", {"source": "imported"}, 1000, default_payout_percent=85)
        profile = database.get_risk_profile(profile_id)
        database.create_backtest_strategy(run_id, profile_id, profile["name"], profile)

        backtest_engine.run_backtest(run_id)

        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "completed")
        report = database.get_backtest_report(run_id)
        self.assertEqual(len(report["strategies"]), 1)
        metrics = report["strategies"][0]["metrics"]
        self.assertIsNotNone(metrics)
        self.assertIsNotNone(metrics["final_bankroll"])
        self.assertEqual(metrics["rank_overall"], 1)  # only strategy in the run

    def test_apex_ascension_backtest_never_writes_real_tier_events(self):
        # The bug this guards against: risk_engine._base_amount's
        # apex_ascension branch calls database.record_tier_event as a
        # live side effect. A backtest replays a PROFILE SNAPSHOT, not
        # the live profile - it must never leave real rows in
        # capital_tier_events just because a simulated tier was crossed.
        for i in range(10):
            sig_id = database.create_imported_signal(
                "TestChannel", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-0{i+1}T10:00:00")
            database.grade_imported_signal(sig_id, "win", payout_percent=85)

        profile_id = database.create_risk_profile("Apex Test", sizing_mode="apex_ascension", fixed_amount=10)
        database.update_apex_ascension_settings(
            profile_id, enabled=1, starting_bankroll=100, starting_unit_value=10,
            standard_units=5, first_reset_threshold=20, reset_increment=20,
        )
        run_id = database.create_backtest_run(
            "Apex Purity Test", {"source": "imported"}, 100, default_payout_percent=85, session_window="all")
        profile = database.get_risk_profile(profile_id)
        database.create_backtest_strategy(run_id, profile_id, profile["name"], profile)

        backtest_engine.run_backtest(run_id)

        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "completed")
        # 10 wins at 85% payout on a low first_reset_threshold guarantees
        # at least one simulated tier crossing occurred during the run -
        # this test is only meaningful if that's actually true.
        report = database.get_backtest_report(run_id)
        self.assertEqual(report["strategies"][0]["metrics"]["win_rate"], 1.0)
        self.assertEqual(database.list_tier_events(profile_id), [])

    def test_run_fails_cleanly_with_no_matching_signals(self):
        run_id = database.create_backtest_run("Empty Run", {"source": "imported"}, 1000)
        profile_id = database.create_risk_profile("P", sizing_mode="fixed", fixed_amount=10)
        profile = database.get_risk_profile(profile_id)
        database.create_backtest_strategy(run_id, profile_id, "P", profile)

        with self.assertRaises(ValueError):
            backtest_engine.run_backtest(run_id)

        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "failed")
        self.assertIsNotNone(run["error_message"])

    def test_run_honors_min_trust_tier(self):
        legacy_id = database.create_imported_signal(
            "TestChannel", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00")
        database.grade_imported_signal(legacy_id, "win", payout_percent=85)
        verified_id = database.create_imported_signal(
            "TestChannel", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:01:00")
        database.grade_imported_signal(verified_id, "win", payout_percent=85)
        database.set_imported_signal_trust_tier(verified_id, "verified_only", changed_by="ops", reason="checked")

        profile_id = database.create_risk_profile("P", sizing_mode="fixed", fixed_amount=10)
        profile = database.get_risk_profile(profile_id)
        run_id = database.create_backtest_run(
            "Trust Tier Test", {"source": "imported", "min_trust_tier": "verified_only"}, 1000)
        database.create_backtest_strategy(run_id, profile_id, "P", profile)

        backtest_engine.run_backtest(run_id)

        report = database.get_backtest_report(run_id)
        # Only the verified_only signal should have been simulated - the
        # legacy_unverified one must be excluded from THIS run entirely.
        trades = database.list_backtest_trades_for_strategy(report["strategies"][0]["id"])
        self.assertEqual(len(trades), 1)


class EstimateBacktestRunTests(DbBackedTestCase):
    def test_no_matching_signals_warns_and_reports_zero(self):
        estimate = backtest_engine.estimate_backtest_run({"source": "imported"}, strategy_count=1)
        self.assertEqual(estimate["eligible_signal_count"], 0)
        self.assertEqual(estimate["estimated_total_trades"], 0)
        self.assertTrue(any("No eligible signals" in w for w in estimate["warnings"]))

    def test_small_sample_warns(self):
        sig_id = database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        estimate = backtest_engine.estimate_backtest_run({"source": "imported"}, strategy_count=1)
        self.assertEqual(estimate["eligible_signal_count"], 1)
        self.assertTrue(any("may not be statistically meaningful" in w for w in estimate["warnings"]))

    def test_excluded_signals_are_reported_in_warnings_and_breakdown(self):
        ungraded_id = database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00")
        graded_id = database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:01:00")
        database.grade_imported_signal(graded_id, "win", payout_percent=85)
        estimate = backtest_engine.estimate_backtest_run({"source": "imported"}, strategy_count=1)
        self.assertEqual(estimate["eligibility_total_examined"], 2)
        self.assertEqual(estimate["eligibility_breakdown"]["excluded_ungraded"], 1)
        self.assertTrue(any("would be excluded" in w for w in estimate["warnings"]))

    def test_estimated_total_trades_scales_with_strategy_count(self):
        for i in range(5):
            sig_id = database.create_imported_signal(
                "C", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-01T10:0{i}:00")
            database.grade_imported_signal(sig_id, "win", payout_percent=85)
        estimate = backtest_engine.estimate_backtest_run({"source": "imported"}, strategy_count=3)
        self.assertEqual(estimate["eligible_signal_count"], 5)
        self.assertEqual(estimate["estimated_total_trades"], 15)
        self.assertGreater(estimate["estimated_duration_seconds"], 0)

    def test_session_count_uses_the_real_grouping_function(self):
        database.grade_imported_signal(database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00"), "win", payout_percent=85)
        database.grade_imported_signal(database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-02T10:00:00"), "win", payout_percent=85)
        estimate = backtest_engine.estimate_backtest_run(
            {"source": "imported", "session_window": "daily"}, strategy_count=1)
        self.assertEqual(estimate["estimated_session_count"], 2)

    def test_min_trust_tier_narrows_the_estimate(self):
        legacy_id = database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00")
        database.grade_imported_signal(legacy_id, "win", payout_percent=85)
        estimate = backtest_engine.estimate_backtest_run(
            {"source": "imported", "min_trust_tier": "verified_only"}, strategy_count=1)
        self.assertEqual(estimate["eligible_signal_count"], 0)
        self.assertEqual(estimate["eligibility_breakdown"]["excluded_below_trust_tier"], 1)

    def test_zero_strategies_warns(self):
        estimate = backtest_engine.estimate_backtest_run({"source": "imported"}, strategy_count=0)
        self.assertTrue(any("Select at least one strategy" in w for w in estimate["warnings"]))


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class CooperativeCancellationTests(DbBackedTestCase):
    def _run_with_two_strategies(self, cancel_check):
        for i in range(5):
            sig_id = database.create_imported_signal(
                "C", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-01T10:0{i}:00")
            database.grade_imported_signal(sig_id, "win", payout_percent=85)
        run_id = database.create_backtest_run("R", {"source": "imported"}, 1000)
        for label in ("S1", "S2"):
            profile_id = database.create_risk_profile(label, sizing_mode="fixed", fixed_amount=10)
            profile = database.get_risk_profile(profile_id)
            database.create_backtest_strategy(run_id, profile_id, label, profile)
        backtest_engine.run_backtest(run_id, cancel_check=cancel_check)
        return run_id

    def test_cancel_check_true_before_first_strategy_stops_immediately(self):
        run_id = self._run_with_two_strategies(cancel_check=lambda: True)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "cancelled")
        report = database.get_backtest_report(run_id)
        self.assertIsNone(report["strategies"][0]["metrics"])  # never simulated

    def test_cancel_check_false_runs_to_completion_unaffected(self):
        run_id = self._run_with_two_strategies(cancel_check=lambda: False)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["progress_percent"], 100.0)
        self.assertEqual(run["strategies_completed"], 2)

    def test_cancel_check_fires_after_first_strategy_stops_before_second(self):
        calls = {"n": 0}

        def cancel_check():
            calls["n"] += 1
            return calls["n"] > 1  # False on strategy 1's check, True on strategy 2's

        run_id = self._run_with_two_strategies(cancel_check=cancel_check)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(run["strategies_completed"], 1)
        report = database.get_backtest_report(run_id)
        self.assertIsNotNone(report["strategies"][0]["metrics"])
        self.assertIsNone(report["strategies"][1]["metrics"])

    def test_default_none_never_checks_cancellation(self):
        run_id = self._run_with_two_strategies(cancel_check=None)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "completed")


class RunBacktestAsyncTests(DbBackedTestCase):
    def _make_run_with_signals(self, n=5):
        for i in range(n):
            sig_id = database.create_imported_signal(
                "C", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-01T10:0{i}:00")
            database.grade_imported_signal(sig_id, "win", payout_percent=85)
        run_id = database.create_backtest_run("R", {"source": "imported"}, 1000, run_mode="async")
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10)
        profile = database.get_risk_profile(profile_id)
        database.create_backtest_strategy(run_id, profile_id, "S1", profile)
        return run_id

    def test_run_backtest_async_completes_normally(self):
        run_id = self._make_run_with_signals()
        _run(backtest_engine.run_backtest_async(run_id))
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["progress_percent"], 100.0)

    def test_run_backtest_async_honors_a_cancellation_requested_before_it_starts(self):
        run_id = self._make_run_with_signals()
        database.request_backtest_cancellation(run_id)
        _run(backtest_engine.run_backtest_async(run_id))
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "cancelled")
        report = database.get_backtest_report(run_id)
        self.assertIsNone(report["strategies"][0]["metrics"])

    def test_start_backtest_run_async_schedules_a_real_task_and_cleans_up(self):
        async def scenario():
            run_id = self._make_run_with_signals()
            task = backtest_engine.start_backtest_run_async(run_id)
            self.assertIn(run_id, backtest_engine._ACTIVE_BACKTEST_TASKS)
            await task
            self.assertNotIn(run_id, backtest_engine._ACTIVE_BACKTEST_TASKS)
            run = database.get_backtest_run(run_id)
            self.assertEqual(run["status"], "completed")

        _run(scenario())


class RecoverAbandonedRunsTests(DbBackedTestCase):
    """2026-07-19 directive: restart-safe recovery - a run still marked
    'running' when the API boots can only mean the previous process
    died mid-run, since this is called once, synchronously, before
    anything new could legitimately be running yet."""

    def test_running_run_is_marked_failed(self):
        run_id = database.create_backtest_run("R", {}, 1000)
        database.update_backtest_run_status(run_id, "running")
        recovered = backtest_engine.recover_abandoned_runs()
        self.assertEqual(recovered, 1)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "failed")
        self.assertIn("restarted", run["error_message"])

    def test_pending_run_is_left_untouched(self):
        run_id = database.create_backtest_run("R", {}, 1000)  # defaults to pending
        recovered = backtest_engine.recover_abandoned_runs()
        self.assertEqual(recovered, 0)
        self.assertEqual(database.get_backtest_run(run_id)["status"], "pending")

    def test_completed_and_failed_and_cancelled_runs_are_left_untouched(self):
        completed_id = database.create_backtest_run("C", {}, 1000)
        database.update_backtest_run_status(completed_id, "completed")
        failed_id = database.create_backtest_run("F", {}, 1000)
        database.update_backtest_run_status(failed_id, "failed", error_message="real failure")
        cancelled_id = database.create_backtest_run("X", {}, 1000)
        database.update_backtest_run_status(cancelled_id, "cancelled")

        recovered = backtest_engine.recover_abandoned_runs()

        self.assertEqual(recovered, 0)
        self.assertEqual(database.get_backtest_run(completed_id)["status"], "completed")
        self.assertEqual(database.get_backtest_run(failed_id)["status"], "failed")
        self.assertEqual(database.get_backtest_run(failed_id)["error_message"], "real failure")
        self.assertEqual(database.get_backtest_run(cancelled_id)["status"], "cancelled")

    def test_multiple_abandoned_runs_are_all_recovered(self):
        ids = [database.create_backtest_run(f"R{i}", {}, 1000) for i in range(3)]
        for run_id in ids:
            database.update_backtest_run_status(run_id, "running")
        recovered = backtest_engine.recover_abandoned_runs()
        self.assertEqual(recovered, 3)
        for run_id in ids:
            self.assertEqual(database.get_backtest_run(run_id)["status"], "failed")

    def test_recovery_is_audited(self):
        run_id = database.create_backtest_run("R", {}, 1000)
        database.update_backtest_run_status(run_id, "running")
        backtest_engine.recover_abandoned_runs()
        log = database.list_backtest_data_audit_log(entity_type="backtest_run", entity_id=run_id)
        self.assertTrue(any(entry["action"] == "status_failed" for entry in log))

    def test_no_op_when_nothing_is_running(self):
        self.assertEqual(backtest_engine.recover_abandoned_runs(), 0)


if __name__ == "__main__":
    unittest.main()
