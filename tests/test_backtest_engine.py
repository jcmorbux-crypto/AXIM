import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import backtest_engine
import risk_engine


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


if __name__ == "__main__":
    unittest.main()
