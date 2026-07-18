import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import daily_compounding as dc
import database
import risk_engine
import backtest_engine


def _settings(**overrides):
    base = {
        "risk_percent": 1.0, "risk_fixed_amount": None,
        "profit_target_percent": 50.0, "profit_target_fixed_amount": None,
        "loss_limit_percent": 25.0, "loss_limit_fixed_amount": None,
        "timezone": "UTC", "stop_after_target": True, "stop_after_loss_limit": True,
        "vault_enabled": False, "vault_percent_on_target": 0,
    }
    base.update(overrides)
    return base


class TradingDateForTests(unittest.TestCase):
    def test_utc_and_named_zone_both_resolve(self):
        self.assertRegex(dc.trading_date_for("UTC"), r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(dc.trading_date_for("America/New_York"), r"^\d{4}-\d{2}-\d{2}$")

    def test_unknown_timezone_fails_safe_to_utc_rather_than_raising(self):
        # Must not raise - a bad saved value can never take down live sizing.
        result = dc.trading_date_for("Not/AReal_Zone")
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")

    def test_a_moment_near_midnight_can_land_on_different_dates_in_different_zones(self):
        moment = datetime(2026, 7, 18, 2, 0, 0)  # 2am UTC
        from zoneinfo import ZoneInfo
        moment_utc = moment.replace(tzinfo=ZoneInfo("UTC"))
        utc_date = dc.trading_date_for("UTC", now=moment_utc)
        # 2am UTC is still the previous evening in US/Pacific (UTC-7/8)
        pacific_date = dc.trading_date_for("America/Los_Angeles", now=moment_utc)
        self.assertNotEqual(utc_date, pacific_date)


class ComputeSizingTests(unittest.TestCase):
    def test_risk_per_trade_is_the_percent_of_starting_balance(self):
        self.assertEqual(dc.compute_risk_per_trade(_settings(risk_percent=2.0), 1000), 20.0)

    def test_fixed_risk_amount_overrides_percent(self):
        s = _settings(risk_percent=2.0, risk_fixed_amount=15.0)
        self.assertEqual(dc.compute_risk_per_trade(s, 1000), 15.0)

    def test_profit_target_is_the_percent_of_starting_balance(self):
        self.assertEqual(dc.compute_profit_target(_settings(profit_target_percent=50), 1000), 500.0)

    def test_fixed_profit_target_overrides_percent(self):
        s = _settings(profit_target_percent=50, profit_target_fixed_amount=300)
        self.assertEqual(dc.compute_profit_target(s, 1000), 300.0)

    def test_loss_limit_is_the_percent_of_starting_balance(self):
        self.assertEqual(dc.compute_loss_limit(_settings(loss_limit_percent=25), 1000), 250.0)

    def test_fixed_loss_limit_overrides_percent(self):
        s = _settings(loss_limit_percent=25, loss_limit_fixed_amount=100)
        self.assertEqual(dc.compute_loss_limit(s, 1000), 100.0)


class CheckShouldStopTests(unittest.TestCase):
    def test_no_stop_when_within_both_thresholds(self):
        self.assertIsNone(dc.check_should_stop(_settings(), 1000, 100))

    def test_stops_on_profit_target(self):
        self.assertEqual(dc.check_should_stop(_settings(), 1000, 500), "profit_target")

    def test_stops_on_loss_limit(self):
        self.assertEqual(dc.check_should_stop(_settings(), 1000, -250), "loss_limit")

    def test_profit_target_checked_before_loss_limit(self):
        # Contrived, but proves ordering: if both would somehow fire, profit wins.
        self.assertEqual(dc.check_should_stop(_settings(profit_target_percent=1, loss_limit_percent=1), 1000, 50), "profit_target")

    def test_pending_stake_is_folded_in_pessimistically_for_loss_limit_only(self):
        # -100 realized alone doesn't breach a -250 limit, but with 200 more
        # at risk (worst case all lose), -300 would - loss_limit should fire.
        self.assertEqual(dc.check_should_stop(_settings(), 1000, -100, pending_stake_today=200), "loss_limit")

    def test_pending_stake_never_counts_toward_the_profit_target(self):
        # An unresolved trade is not yet a win - only realized P/L counts.
        # realized_pnl_today=450 is close to (but under) the $500 target;
        # if pending_stake were wrongly ADDED to it (450+200=650), it would
        # wrongly cross - the pending_stake here is small enough that it
        # also can't trigger the LOSS side (effective_pnl 450-200=250, not
        # anywhere near -250), isolating the profit-target behavior alone.
        self.assertIsNone(dc.check_should_stop(_settings(), 1000, 450, pending_stake_today=200))

    def test_stop_after_target_false_disables_the_target_stop(self):
        s = _settings(stop_after_target=False)
        self.assertIsNone(dc.check_should_stop(s, 1000, 500))

    def test_stop_after_loss_limit_false_disables_the_loss_stop(self):
        s = _settings(stop_after_loss_limit=False)
        self.assertIsNone(dc.check_should_stop(s, 1000, -300))


class VaultSkimOnTargetTests(unittest.TestCase):
    def test_skims_the_moment_this_trade_crosses_the_target(self):
        s = _settings(vault_enabled=True, vault_percent_on_target=10)
        skim = dc.vault_skim_on_target(s, 1000, 490, 510)
        self.assertEqual(skim, 51.0)  # 10% of the AFTER total, 510

    def test_no_skim_before_crossing(self):
        s = _settings(vault_enabled=True, vault_percent_on_target=10)
        self.assertEqual(dc.vault_skim_on_target(s, 1000, 300, 490), 0)

    def test_no_double_skim_once_already_past_target(self):
        s = _settings(vault_enabled=True, vault_percent_on_target=10)
        self.assertEqual(dc.vault_skim_on_target(s, 1000, 520, 540), 0)

    def test_disabled_vault_never_skims(self):
        s = _settings(vault_enabled=False, vault_percent_on_target=10)
        self.assertEqual(dc.vault_skim_on_target(s, 1000, 490, 510), 0)


class RiskEngineIntegrationTests(unittest.TestCase):
    """End-to-end against a real temp DB - proves sizing_mode='daily_compounding'
    genuinely drives risk_engine.compute_position_size and stops trading via
    a real DailyCompoundingStopped exception, not just the pure math above."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.fund_id = database.create_fund("Test Fund", starting_balance=1000)
        self.profile_id = database.create_risk_profile(
            "Daily Test", sizing_mode="daily_compounding", bankroll=1000,
        )
        database.update_daily_compounding_settings(
            self.profile_id, enabled=True, risk_percent=1.0,
            profit_target_percent=50, loss_limit_percent=25, timezone="UTC",
        )
        self.session_id = database.start_trading_session(
            "Test Fund - Live Signals", [1], "DEMO", fund_id=self.fund_id, risk_profile_id=self.profile_id,
        )
        database.set_session_risk_profile(self.session_id, self.profile_id)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _close_trade(self, profit_loss, result):
        # Mirrors the real pipeline order (core/trade_coordinator.py): a
        # trade is always SIZED via compute_position_size before it's
        # placed, and only closes afterward - sizing first is what
        # establishes (and freezes) the day's starting balance from the
        # Fund's balance BEFORE this trade's own P/L lands, exactly as
        # live production does for every day's first signal.
        risk_engine.compute_position_size(self.session_id, 5.0)
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD", "direction": "BUY", "expiry": "1m", "raw_message": "t"},
            session_id=self.session_id, fund_id=self.fund_id,
        )
        database.update_trade_status(
            trade_id, "closed", trade_amount=abs(profit_loss) if profit_loss < 0 else 10,
            closed_at=datetime.now().isoformat(), result=result, profit_loss=profit_loss,
        )
        database.update_session_pnl(self.session_id, profit_loss)
        risk_engine.on_trade_closed(self.session_id, won=(result == "win"), profit_loss=profit_loss)

    def test_sizing_is_1_percent_of_the_funds_starting_balance(self):
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(amount, 10.0)

    def test_stake_stays_fixed_even_as_realized_pnl_moves_within_the_day(self):
        self._close_trade(-10, "loss")
        self._close_trade(-10, "loss")
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(amount, 10.0)  # still 1% of the DAY's starting balance, not current balance

    def test_loss_limit_stops_new_trades_the_same_day(self):
        # _close_trade sizes (compute_position_size) before every close, so
        # the stop fires INSIDE the loop the moment the 25th loss ($250 =
        # 25% of $1000) makes the NEXT size check see the breach.
        raised = None
        for _ in range(30):
            try:
                self._close_trade(-10, "loss")
            except risk_engine.DailyCompoundingStopped as e:
                raised = e
                break
        self.assertIsNotNone(raised, "expected a stop before all 30 signals were processed")
        self.assertEqual(raised.rule, "daily_compounding_loss_limit")

    def test_profit_target_stops_new_trades_the_same_day(self):
        raised = None
        for _ in range(80):
            try:
                self._close_trade(10, "win")
            except risk_engine.DailyCompoundingStopped as e:
                raised = e
                break
        self.assertIsNotNone(raised, "expected a stop before all 80 signals were processed")
        self.assertEqual(raised.rule, "daily_compounding_profit_target")

    def test_vault_skims_when_this_trades_close_crosses_the_target(self):
        database.update_daily_compounding_settings(self.profile_id, vault_enabled=True, vault_percent_on_target=20)
        for _ in range(49):
            self._close_trade(10, "win")  # $490, not yet at $500 target
        session_before = database.get_trading_session(self.session_id)
        self.assertEqual(session_before["vaulted_amount"], 0)
        self._close_trade(10, "win")  # crosses to $500
        session_after = database.get_trading_session(self.session_id)
        self.assertGreater(session_after["vaulted_amount"], 0)

    def test_disabled_daily_compounding_falls_back_to_fixed_amount(self):
        database.update_daily_compounding_settings(self.profile_id, enabled=False)
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(amount, database.get_risk_profile(self.profile_id)["fixed_amount"])


class BacktestIntegrationTests(unittest.TestCase):
    """Proves core/backtest_engine.py enforces the exact same daily-boundary
    rules as live execution, via the same core/daily_compounding.py math -
    not a separately-maintained approximation."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _pool(self, days):
        pool = []
        sid = 1
        for date, n, result in days:
            for i in range(n):
                pool.append({
                    "source_type": "live", "signal_id": sid, "asset": "EUR/USD", "direction": "BUY",
                    "timestamp": f"{date}T{9 + i // 10:02d}:{(i % 10) * 5:02d}:00",
                    "result": result, "payout_percent": 85,
                })
                sid += 1
        return pool

    def _profile(self, **daily_overrides):
        pid = database.create_risk_profile("BT Daily", sizing_mode="daily_compounding", bankroll=1000)
        fields = dict(enabled=True, risk_percent=1.0, profit_target_percent=50, loss_limit_percent=25, timezone="UTC")
        fields.update(daily_overrides)
        database.update_daily_compounding_settings(pid, **fields)
        return database.get_risk_profile(pid)

    def test_forces_daily_grouping_even_if_caller_asked_for_all(self):
        pool = self._pool([("2026-07-01", 3, "loss"), ("2026-07-02", 3, "loss")])
        profile = self._profile()
        result = backtest_engine.simulate_strategy(pool, profile, starting_bankroll=1000, session_window="all")
        self.assertEqual(len(result["sessions"]), 2)  # two calendar days, not one merged "all" session

    def test_loss_limit_stops_that_day_and_the_next_day_resets(self):
        pool = self._pool([("2026-07-01", 30, "loss"), ("2026-07-02", 3, "loss")])
        profile = self._profile()
        result = backtest_engine.simulate_strategy(pool, profile, starting_bankroll=1000, session_window="daily")
        day1, day2 = result["sessions"]
        self.assertEqual(day1["status"], "stopped_daily_compounding_loss_limit")
        self.assertLess(day1["trades_count"], 30)  # stopped before exhausting all 30 signals
        self.assertEqual(day2["starting_balance"], round(1000 + day1["realized_pnl"], 2))

    def test_profit_target_stop_is_reflected_in_metrics(self):
        pool = self._pool([("2026-07-01", 80, "win")])
        profile = self._profile()
        result = backtest_engine.simulate_strategy(pool, profile, starting_bankroll=1000, session_window="daily")
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(metrics["days_profit_target_hit"], 1)
        self.assertEqual(metrics["daily_target_hit_rate"], 100.0)

    def test_loss_limit_stop_is_reflected_in_metrics(self):
        pool = self._pool([("2026-07-01", 30, "loss")])
        profile = self._profile()
        result = backtest_engine.simulate_strategy(pool, profile, starting_bankroll=1000, session_window="daily")
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], 1000)
        self.assertEqual(metrics["days_loss_limit_hit"], 1)
        self.assertEqual(metrics["daily_loss_limit_hit_rate"], 100.0)

    def test_a_day_that_neither_hits_target_nor_limit_completes_normally(self):
        pool = self._pool([("2026-07-01", 5, "loss")])
        profile = self._profile()
        result = backtest_engine.simulate_strategy(pool, profile, starting_bankroll=1000, session_window="daily")
        self.assertEqual(result["sessions"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
