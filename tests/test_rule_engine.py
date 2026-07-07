import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import rule_engine
import trade_lifecycle


def _make_closed_trade(asset="EUR/USD OTC", channel="TestChannel", result="win", profit_loss=0.9,
                        trade_amount=1, payout=90, session_id=None):
    signal = {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
              "trade_amount": trade_amount}
    trade_id = database.record_signal_received(signal, source=channel, session_id=session_id)
    status = trade_lifecycle.TradeStatus.RESULT_WIN if result == "win" else trade_lifecycle.TradeStatus.RESULT_LOSS
    database.update_trade_status(trade_id, status, result=result, profit_loss=profit_loss, payout=payout,
                                  closed_at=datetime.now().isoformat())
    return trade_id


class RuleEngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class ConditionEvaluatorTests(RuleEngineTestCase):
    def test_daily_profit_gte(self):
        _make_closed_trade(result="win", profit_loss=60)
        self.assertTrue(rule_engine._cond_daily_profit_gte({"threshold": 50}))
        self.assertFalse(rule_engine._cond_daily_profit_gte({"threshold": 100}))

    def test_daily_loss_gte(self):
        _make_closed_trade(result="loss", profit_loss=-60)
        self.assertTrue(rule_engine._cond_daily_loss_gte({"threshold": 50}))
        self.assertFalse(rule_engine._cond_daily_loss_gte({"threshold": 100}))

    def test_consecutive_wins_eq_exact_match_only(self):
        _make_closed_trade(result="win")
        _make_closed_trade(result="win")
        self.assertTrue(rule_engine._cond_consecutive_wins_eq({"count": 2}))
        self.assertFalse(rule_engine._cond_consecutive_wins_eq({"count": 3}))
        self.assertFalse(rule_engine._cond_consecutive_wins_eq({"count": 1}))

    def test_consecutive_losses_eq(self):
        _make_closed_trade(result="loss")
        _make_closed_trade(result="loss")
        _make_closed_trade(result="loss")
        self.assertTrue(rule_engine._cond_consecutive_losses_eq({"count": 3}))

    def test_session_profit_gte_no_active_session(self):
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 10}))

    def test_session_profit_gte_and_loss_gte(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 25)
        self.assertTrue(rule_engine._cond_session_profit_gte({"threshold": 20}))
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 30}))
        database.update_session_pnl(session_id, -50)
        self.assertTrue(rule_engine._cond_session_loss_gte({"threshold": 20}))

    def test_lifetime_profit_gte(self):
        _make_closed_trade(result="win", profit_loss=40)
        _make_closed_trade(result="win", profit_loss=40)
        self.assertTrue(rule_engine._cond_lifetime_profit_gte({"threshold": 75}))
        self.assertFalse(rule_engine._cond_lifetime_profit_gte({"threshold": 200}))

    def test_source_win_rate_below_requires_min_trades(self):
        channel_id = database.upsert_channel(111, "chan", "Losers", "channel") or \
            next(c["id"] for c in database.list_channels() if c["title"] == "Losers")
        for _ in range(3):
            _make_closed_trade(channel="Losers", result="loss", profit_loss=-1)
        # below min_trades of 10 by default -> should not trigger
        self.assertFalse(rule_engine._cond_source_win_rate_below({"channel_id": channel_id, "threshold": 0.5}))
        self.assertTrue(rule_engine._cond_source_win_rate_below(
            {"channel_id": channel_id, "threshold": 0.5, "min_trades": 3}))

    def test_source_win_rate_below_unknown_channel(self):
        self.assertFalse(rule_engine._cond_source_win_rate_below({"channel_id": 999999, "threshold": 0.5}))


class ActionExecutorTests(RuleEngineTestCase):
    def test_stop_active_session_no_session(self):
        result = rule_engine._act_stop_active_session({}, "test rule")
        self.assertIn("no active session", result)

    def test_stop_active_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_engine._act_stop_active_session({}, "test rule")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_rule")

    def test_emergency_stop_sets_control_state_and_stops_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_engine._act_emergency_stop({}, "test rule")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_emergency")
        self.assertTrue(database.get_control_state()["emergency_stop"])

    def test_increase_risk_profile_percent(self):
        profile_id = database.create_risk_profile("P", sizing_mode="percent", percent_of_bankroll=2.0)
        session_id = database.start_trading_session("Test", [1], "DEMO", risk_profile_id=profile_id)
        rule_engine._act_increase_risk_profile_percent({"percent_increase": 10}, "test rule")
        self.assertAlmostEqual(database.get_risk_profile(profile_id)["percent_of_bankroll"], 2.2)

    def test_switch_session_risk_profile(self):
        profile_id = database.create_risk_profile("New Profile")
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_engine._act_switch_session_risk_profile({"risk_profile_id": profile_id}, "test rule")
        self.assertEqual(database.get_trading_session(session_id)["risk_profile_id"], profile_id)

    def test_disable_channel(self):
        database.upsert_channel(222, "chan2", "ToDisable", "channel")
        channel = next(c for c in database.list_channels() if c["title"] == "ToDisable")
        database.set_channel_enabled(channel["id"], True)
        rule_engine._act_disable_channel({"channel_id": channel["id"]}, "test rule")
        updated = next(c for c in database.list_channels() if c["id"] == channel["id"])
        self.assertFalse(updated["enabled"])


class EdgeTriggerTests(RuleEngineTestCase):
    def test_fires_once_then_not_again_while_condition_stays_true(self):
        rule_id = database.create_rule(
            "Stop at $50", "daily_profit_gte", {"threshold": 50}, "stop_active_session", {})
        session_id = database.start_trading_session("Test", [1], "DEMO")
        _make_closed_trade(result="win", profit_loss=60)

        fired_first = rule_engine.evaluate_rule(database.get_rule(rule_id))
        self.assertTrue(fired_first)
        self.assertEqual(database.get_rule(rule_id)["trigger_count"], 1)

        # condition still true (daily profit still >= 50) - must not refire
        fired_second = rule_engine.evaluate_rule(database.get_rule(rule_id))
        self.assertFalse(fired_second)
        self.assertEqual(database.get_rule(rule_id)["trigger_count"], 1)

    def test_disabled_rule_is_skipped_by_evaluate_all(self):
        database.create_rule("Disabled", "daily_profit_gte", {"threshold": 1}, "stop_active_session", {},
                              enabled=False)
        _make_closed_trade(result="win", profit_loss=10)
        fired_any = rule_engine.evaluate_all()
        self.assertFalse(fired_any)

    def test_evaluate_all_fires_enabled_rule(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.create_rule("Stop at $5", "daily_profit_gte", {"threshold": 5}, "stop_active_session", {})
        _make_closed_trade(result="win", profit_loss=10)
        fired_any = rule_engine.evaluate_all()
        self.assertTrue(fired_any)
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_rule")


class RuleCrudTests(RuleEngineTestCase):
    def test_create_get_list_update_delete(self):
        rule_id = database.create_rule("R1", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        rule = database.get_rule(rule_id)
        self.assertEqual(rule["name"], "R1")
        self.assertEqual(rule["condition_params"], {"threshold": 10})
        self.assertFalse(rule["last_condition_state"])

        database.update_rule(rule_id, name="R1 renamed", condition_params={"threshold": 20})
        updated = database.get_rule(rule_id)
        self.assertEqual(updated["name"], "R1 renamed")
        self.assertEqual(updated["condition_params"], {"threshold": 20})

        self.assertEqual(len(database.list_rules()), 1)
        database.delete_rule(rule_id)
        self.assertIsNone(database.get_rule(rule_id))

    def test_update_rejects_unknown_field(self):
        rule_id = database.create_rule("R1", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        with self.assertRaises(ValueError):
            database.update_rule(rule_id, not_a_real_field=1)


if __name__ == "__main__":
    unittest.main()
