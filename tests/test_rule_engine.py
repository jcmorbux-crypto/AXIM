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
                        trade_amount=1, payout=90, session_id=None, fund_id=None):
    signal = {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
              "trade_amount": trade_amount}
    trade_id = database.record_signal_received(signal, source=channel, session_id=session_id, fund_id=fund_id)
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
        self.assertTrue(rule_engine._cond_daily_profit_gte({"threshold": 50}, {}))
        self.assertFalse(rule_engine._cond_daily_profit_gte({"threshold": 100}, {}))

    def test_daily_loss_gte(self):
        _make_closed_trade(result="loss", profit_loss=-60)
        self.assertTrue(rule_engine._cond_daily_loss_gte({"threshold": 50}, {}))
        self.assertFalse(rule_engine._cond_daily_loss_gte({"threshold": 100}, {}))

    def test_consecutive_wins_eq_exact_match_only(self):
        _make_closed_trade(result="win")
        _make_closed_trade(result="win")
        self.assertTrue(rule_engine._cond_consecutive_wins_eq({"count": 2}, {}))
        self.assertFalse(rule_engine._cond_consecutive_wins_eq({"count": 3}, {}))
        self.assertFalse(rule_engine._cond_consecutive_wins_eq({"count": 1}, {}))

    def test_consecutive_losses_eq(self):
        _make_closed_trade(result="loss")
        _make_closed_trade(result="loss")
        _make_closed_trade(result="loss")
        self.assertTrue(rule_engine._cond_consecutive_losses_eq({"count": 3}, {}))

    def test_session_profit_gte_no_active_session(self):
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 10}, {}))

    def test_session_profit_gte_and_loss_gte(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 25)
        self.assertTrue(rule_engine._cond_session_profit_gte({"threshold": 20}, {}))
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 30}, {}))
        database.update_session_pnl(session_id, -50)
        self.assertTrue(rule_engine._cond_session_loss_gte({"threshold": 20}, {}))

    def test_lifetime_profit_gte(self):
        _make_closed_trade(result="win", profit_loss=40)
        _make_closed_trade(result="win", profit_loss=40)
        self.assertTrue(rule_engine._cond_lifetime_profit_gte({"threshold": 75}, {}))
        self.assertFalse(rule_engine._cond_lifetime_profit_gte({"threshold": 200}, {}))

    def test_source_win_rate_below_requires_min_trades(self):
        channel_id = database.upsert_channel(111, "chan", "Losers", "channel") or \
            next(c["id"] for c in database.list_channels() if c["title"] == "Losers")
        for _ in range(3):
            _make_closed_trade(channel="Losers", result="loss", profit_loss=-1)
        # below min_trades of 10 by default -> should not trigger
        self.assertFalse(rule_engine._cond_source_win_rate_below({"channel_id": channel_id, "threshold": 0.5}, {}))
        self.assertTrue(rule_engine._cond_source_win_rate_below(
            {"channel_id": channel_id, "threshold": 0.5, "min_trades": 3}, {}))

    def test_source_win_rate_below_unknown_channel(self):
        self.assertFalse(rule_engine._cond_source_win_rate_below({"channel_id": 999999, "threshold": 0.5}, {}))

    def test_martingale_step_gte(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.advance_martingale_step(session_id)
        database.advance_martingale_step(session_id)
        self.assertTrue(rule_engine._cond_martingale_step_gte({"step": 2}, {}))
        self.assertFalse(rule_engine._cond_martingale_step_gte({"step": 3}, {}))

    def test_martingale_step_gte_no_active_session(self):
        self.assertFalse(rule_engine._cond_martingale_step_gte({"step": 1}, {}))

    def test_broker_disconnected_false_with_no_fund_on_rule(self):
        self.assertFalse(rule_engine._cond_broker_disconnected({}, {}))

    def test_broker_disconnected_true_when_account_not_connected(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acct1", mode="demo")
        database.assign_broker_account_to_fund(fund_id, account_id)
        self.assertTrue(rule_engine._cond_broker_disconnected({}, {"fund_id": fund_id}))

    def test_broker_disconnected_false_when_connected(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acct1", mode="demo")
        database.assign_broker_account_to_fund(fund_id, account_id)
        database.update_broker_account(account_id, connection_status="connected")
        self.assertFalse(rule_engine._cond_broker_disconnected({}, {"fund_id": fund_id}))

    def test_channel_disabled(self):
        database.upsert_channel(333, "chan3", "MaybeDisabled", "channel")
        channel = next(c for c in database.list_channels() if c["title"] == "MaybeDisabled")
        database.set_channel_enabled(channel["id"], False)
        self.assertTrue(rule_engine._cond_channel_disabled({"channel_id": channel["id"]}, {}))
        database.set_channel_enabled(channel["id"], True)
        self.assertFalse(rule_engine._cond_channel_disabled({"channel_id": channel["id"]}, {}))


class FundScopedResolutionTests(RuleEngineTestCase):
    """A rule with a fund_id must resolve session-scoped conditions/
    actions against THAT fund's own active session, never a different
    fund's - the whole point of the Fund-owned rules redesign."""

    def test_session_profit_gte_only_sees_its_own_funds_session(self):
        fund_a = database.create_fund("Fund A")
        fund_b = database.create_fund("Fund B")
        account_a = database.create_broker_account("Acct A", mode="demo")
        account_b = database.create_broker_account("Acct B", mode="demo")
        session_a = database.start_trading_session("SA", [1], "DEMO", fund_id=fund_a, broker_account_id=account_a)
        session_b = database.start_trading_session("SB", [2], "DEMO", fund_id=fund_b, broker_account_id=account_b)
        database.update_session_pnl(session_a, 100)
        database.update_session_pnl(session_b, -100)

        rule_for_a = {"fund_id": fund_a, "scope": "fund"}
        rule_for_b = {"fund_id": fund_b, "scope": "fund"}
        self.assertTrue(rule_engine._cond_session_profit_gte({"threshold": 50}, rule_for_a))
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 50}, rule_for_b))

    def test_stop_active_session_only_stops_its_own_funds_session(self):
        fund_a = database.create_fund("Fund A")
        fund_b = database.create_fund("Fund B")
        account_a = database.create_broker_account("Acct A", mode="demo")
        account_b = database.create_broker_account("Acct B", mode="demo")
        session_a = database.start_trading_session("SA", [1], "DEMO", fund_id=fund_a, broker_account_id=account_a)
        session_b = database.start_trading_session("SB", [2], "DEMO", fund_id=fund_b, broker_account_id=account_b)

        rule_engine._act_stop_active_session({}, "stop A", {"fund_id": fund_a, "scope": "fund"})
        self.assertEqual(database.get_trading_session(session_a)["status"], "stopped_rule")
        self.assertEqual(database.get_trading_session(session_b)["status"], "active")

    def test_session_scope_rule_targets_only_the_pinned_session(self):
        fund_id = database.create_fund("F1")
        session_id = database.start_trading_session("S1", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_id, 100)
        # A newer session for the SAME fund must not be seen by a rule
        # pinned to the older session_id.
        database.stop_trading_session(session_id, "stopped_manual")
        session_2 = database.start_trading_session("S2", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_2, 5)

        pinned_rule = {"fund_id": fund_id, "scope": "session", "session_id": session_id}
        # session_id's session is no longer active -> resolves to None
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 1}, pinned_rule))

    def test_session_scope_rule_refuses_a_session_belonging_to_a_different_fund(self):
        # Defense in depth for the gap api/rules.py's update_rule had:
        # a scope='session' rule's session_id must belong to the SAME
        # fund_id the rule itself is owned by. Without this check,
        # _resolve_session_for_rule would let a rule act (stop/
        # emergency-stop/resize/vault) on a completely different Fund's
        # session than the one it's scoped to - a real cross-Fund
        # correctness issue, not a display glitch.
        fund_a = database.create_fund("Fund A")
        fund_b = database.create_fund("Fund B")
        session_b = database.start_trading_session("SB", [1], "DEMO", fund_id=fund_b)
        database.update_session_pnl(session_b, 1000)  # would trip the threshold if wrongly resolved

        # Malformed/malicious rule state: fund_id says A, but session_id
        # actually belongs to B (exactly the state the missing
        # update_rule validation could previously have produced).
        cross_fund_rule = {"fund_id": fund_a, "scope": "session", "session_id": session_b}
        self.assertIsNone(rule_engine._resolve_session_for_rule(cross_fund_rule))
        self.assertFalse(rule_engine._cond_session_profit_gte({"threshold": 1}, cross_fund_rule))

        # The action side too - stopping "session_b via a Fund-A-owned
        # rule" must be a no-op, not actually stop Fund B's session.
        rule_engine._act_stop_active_session({}, "cross-fund stop attempt", cross_fund_rule)
        self.assertEqual(database.get_trading_session(session_b)["status"], "active")

    def test_daily_profit_gte_is_scoped_per_fund(self):
        fund_a = database.create_fund("Fund A")
        fund_b = database.create_fund("Fund B")
        session_a = database.start_trading_session("SA", [1], "DEMO", fund_id=fund_a)
        _make_closed_trade(result="win", profit_loss=60, session_id=session_a, fund_id=fund_a)
        self.assertTrue(rule_engine._cond_daily_profit_gte({"threshold": 50}, {"fund_id": fund_a}))
        self.assertFalse(rule_engine._cond_daily_profit_gte({"threshold": 50}, {"fund_id": fund_b}))


class ActionExecutorTests(RuleEngineTestCase):
    def test_stop_active_session_no_session(self):
        result = rule_engine._act_stop_active_session({}, "test rule", {})
        self.assertIn("no active session", result)

    def test_stop_active_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_engine._act_stop_active_session({}, "test rule", {})
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_rule")

    def test_emergency_stop_sets_control_state_and_stops_all_sessions(self):
        session_1 = database.start_trading_session("Test1", [1], "DEMO")
        rule_engine._act_emergency_stop({}, "test rule", {})
        self.assertEqual(database.get_trading_session(session_1)["status"], "stopped_emergency")
        self.assertTrue(database.get_control_state()["emergency_stop"])

    def test_emergency_stop_stops_every_concurrently_active_session(self):
        account_a = database.create_broker_account("Acct A", mode="demo")
        account_b = database.create_broker_account("Acct B", mode="demo")
        session_a = database.start_trading_session("SA", [1], "DEMO", broker_account_id=account_a)
        session_b = database.start_trading_session("SB", [2], "DEMO", broker_account_id=account_b)
        rule_engine._act_emergency_stop({}, "test rule", {})
        self.assertEqual(database.get_trading_session(session_a)["status"], "stopped_emergency")
        self.assertEqual(database.get_trading_session(session_b)["status"], "stopped_emergency")

    def test_increase_risk_profile_percent(self):
        profile_id = database.create_risk_profile("P", sizing_mode="percent", percent_of_bankroll=2.0)
        session_id = database.start_trading_session("Test", [1], "DEMO", risk_profile_id=profile_id)
        rule_engine._act_increase_risk_profile_percent({"percent_increase": 10}, "test rule", {})
        self.assertAlmostEqual(database.get_risk_profile(profile_id)["percent_of_bankroll"], 2.2)

    def test_increase_risk_profile_percent_caps_at_the_hard_ceiling(self):
        profile_id = database.create_risk_profile("P", sizing_mode="percent", percent_of_bankroll=20.0)
        database.start_trading_session("Test", [1], "DEMO", risk_profile_id=profile_id)
        rule_engine._act_increase_risk_profile_percent({"percent_increase": 50}, "test rule", {})
        self.assertEqual(
            database.get_risk_profile(profile_id)["percent_of_bankroll"],
            rule_engine.MAX_RULE_DRIVEN_PERCENT_OF_BANKROLL,
        )

    def test_increase_risk_profile_percent_repeated_firings_cannot_exceed_the_ceiling(self):
        # Edge-triggering (see module docstring) only stops this from
        # refiring on every tick of an unchanged condition - a condition
        # that repeatedly transitions false->true over a session's
        # lifetime (e.g. a win-streak counter that resets and rebuilds)
        # can still fire this action many times. Simulates that directly
        # rather than trusting the single-firing test above to generalize.
        profile_id = database.create_risk_profile("P", sizing_mode="percent", percent_of_bankroll=2.0)
        database.start_trading_session("Test", [1], "DEMO", risk_profile_id=profile_id)
        for _ in range(50):
            rule_engine._act_increase_risk_profile_percent({"percent_increase": 10}, "test rule", {})
        self.assertLessEqual(
            database.get_risk_profile(profile_id)["percent_of_bankroll"],
            rule_engine.MAX_RULE_DRIVEN_PERCENT_OF_BANKROLL,
        )

    def test_switch_session_risk_profile(self):
        profile_id = database.create_risk_profile("New Profile")
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_engine._act_switch_session_risk_profile({"risk_profile_id": profile_id}, "test rule", {})
        self.assertEqual(database.get_trading_session(session_id)["risk_profile_id"], profile_id)

    def test_disable_channel(self):
        database.upsert_channel(222, "chan2", "ToDisable", "channel")
        channel = next(c for c in database.list_channels() if c["title"] == "ToDisable")
        database.set_channel_enabled(channel["id"], True)
        rule_engine._act_disable_channel({"channel_id": channel["id"]}, "test rule", {})
        updated = next(c for c in database.list_channels() if c["id"] == channel["id"])
        self.assertFalse(updated["enabled"])

    def test_disable_martingale_for_session(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        result = rule_engine._act_disable_martingale_for_session({}, "test rule", {})
        self.assertIn(str(session_id), result)
        self.assertTrue(database.get_trading_session(session_id)["martingale_disabled"])

    def test_disable_martingale_for_session_no_active_session(self):
        result = rule_engine._act_disable_martingale_for_session({}, "test rule", {})
        self.assertIn("no active session", result)

    def test_move_profit_to_vault(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 80)
        result = rule_engine._act_move_profit_to_vault({}, "test rule", {})
        self.assertIn("80.00", result)
        self.assertEqual(database.get_trading_session(session_id)["vaulted_amount"], 80)

    def test_move_profit_to_vault_no_unvaulted_profit(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        result = rule_engine._act_move_profit_to_vault({}, "test rule", {})
        self.assertIn("no unvaulted profit", result)

    def test_pause_fund(self):
        fund_id = database.create_fund("F1")
        result = rule_engine._act_pause_fund({}, "test rule", {"fund_id": fund_id})
        self.assertIn(str(fund_id), result)
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")

    def test_pause_fund_no_fund_on_rule(self):
        result = rule_engine._act_pause_fund({}, "test rule", {})
        self.assertIn("no Fund", result)

    def test_resume_fund(self):
        fund_id = database.create_fund("F1")
        database.update_fund(fund_id, status="paused")
        rule_engine._act_resume_fund({}, "test rule", {"fund_id": fund_id})
        self.assertEqual(database.get_fund(fund_id)["status"], "active")

    def test_notify_owner_creates_notification_for_owner(self):
        owner_id = database.create_user("owner@axim.local", "password123", role="owner")
        result = rule_engine._act_notify_owner({"message": "Daily target hit"}, "test rule", {})
        self.assertIn("Daily target hit", result)
        notifications = database.list_notifications(owner_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["message"], "Daily target hit")

    def test_notify_owner_default_message(self):
        database.create_user("owner@axim.local", "password123", role="owner")
        result = rule_engine._act_notify_owner({}, "my rule", {})
        self.assertIn("my rule", result)

    def test_notify_owner_no_owner_account(self):
        result = rule_engine._act_notify_owner({"message": "hi"}, "test rule", {})
        self.assertIn("no owner account", result)


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

    def test_deleting_a_rule_deletes_its_firing_history(self):
        rule_id = database.create_rule("R1", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        database.record_rule_firing(rule_id, "stopped session 1")
        database.delete_rule(rule_id)
        self.assertEqual(database.list_rule_firings(rule_id), [])


class RuleFiringHistoryTests(RuleEngineTestCase):
    def test_record_and_list_firings(self):
        rule_id = database.create_rule("R1", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        database.record_rule_firing(rule_id, "stopped active session 1")
        database.record_rule_firing(rule_id, "stopped active session 2")
        firings = database.list_rule_firings(rule_id)
        self.assertEqual(len(firings), 2)
        # newest first
        self.assertEqual(firings[0]["outcome_message"], "stopped active session 2")

    def test_firings_scoped_per_rule(self):
        rule_a = database.create_rule("A", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        rule_b = database.create_rule("B", "daily_profit_gte", {"threshold": 10}, "stop_active_session", {})
        database.record_rule_firing(rule_a, "fired for A")
        self.assertEqual(len(database.list_rule_firings(rule_a)), 1)
        self.assertEqual(len(database.list_rule_firings(rule_b)), 0)

    def test_evaluate_rule_writes_a_real_firing_row(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        rule_id = database.create_rule("Stop at $5", "daily_profit_gte", {"threshold": 5}, "stop_active_session", {})
        _make_closed_trade(result="win", profit_loss=10)
        rule_engine.evaluate_rule(database.get_rule(rule_id))
        firings = database.list_rule_firings(rule_id)
        self.assertEqual(len(firings), 1)
        self.assertIn(str(session_id), firings[0]["outcome_message"])

    def test_no_firing_row_when_condition_does_not_fire(self):
        rule_id = database.create_rule("Stop at $50", "daily_profit_gte", {"threshold": 50}, "stop_active_session", {})
        _make_closed_trade(result="win", profit_loss=10)
        rule_engine.evaluate_rule(database.get_rule(rule_id))
        self.assertEqual(database.list_rule_firings(rule_id), [])


if __name__ == "__main__":
    unittest.main()
