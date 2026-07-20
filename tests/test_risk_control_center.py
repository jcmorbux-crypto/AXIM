import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import risk_control_center


class RiskControlCenterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _connected_fund(self, name="Test Fund", starting_balance=1000):
        account_id = database.create_broker_account("Test Broker", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        fund_id = database.create_fund(name, starting_balance=starting_balance)
        database.assign_broker_account_to_fund(fund_id, account_id)
        return fund_id, account_id


class MissingFundTests(RiskControlCenterTestCase):
    def test_unknown_fund_returns_none(self):
        self.assertIsNone(risk_control_center.get_risk_control_center(999999))


class NoActiveSessionTests(RiskControlCenterTestCase):
    def test_no_session_no_default_profile_next_trade_is_not_live(self):
        fund_id, _ = self._connected_fund()
        result = risk_control_center.get_risk_control_center(fund_id)
        self.assertIsNone(result["active_session"])
        self.assertIsNone(result["active_profile"])
        self.assertIsNone(result["next_trade"]["amount"])
        self.assertFalse(result["next_trade"]["is_live"])
        self.assertIsNone(result["if_it_wins"])
        self.assertIsNone(result["if_it_loses"])

    def test_no_session_but_fund_has_default_profile_names_it_in_the_reason(self):
        fund_id, _ = self._connected_fund()
        profile_id = database.create_risk_profile("My Strategy", sizing_mode="fixed", fixed_amount=5, bankroll=1000)
        database.update_fund(fund_id, default_risk_profile_id=profile_id)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertIsNone(result["active_session"])
        self.assertIsNone(result["next_trade"]["amount"])
        self.assertIn("My Strategy", result["next_trade"]["reason"])


class ActiveSessionNextTradeTests(RiskControlCenterTestCase):
    def _start_session(self, fund_id, account_id, profile_id):
        session_id = database.start_trading_session(
            "S", [1], "DEMO", fund_id=fund_id, broker_account_id=account_id, risk_profile_id=profile_id,
        )
        return session_id

    def test_fixed_sizing_profile_gives_a_live_amount(self):
        fund_id, account_id = self._connected_fund()
        profile_id = database.create_risk_profile("Fixed 5", sizing_mode="fixed", fixed_amount=5, bankroll=1000)
        self._start_session(fund_id, account_id, profile_id)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertEqual(result["active_profile"]["name"], "Fixed 5")
        self.assertEqual(result["active_profile"]["source"], "session")
        self.assertTrue(result["next_trade"]["is_live"])
        self.assertEqual(result["next_trade"]["amount"], 5.0)
        self.assertIn("Fixed 5", result["next_trade"]["reason"])

    def test_session_profile_wins_over_fund_default(self):
        fund_id, account_id = self._connected_fund()
        default_id = database.create_risk_profile("Default", sizing_mode="fixed", fixed_amount=1, bankroll=1000)
        session_profile_id = database.create_risk_profile("Session Pick", sizing_mode="fixed", fixed_amount=9, bankroll=1000)
        database.update_fund(fund_id, default_risk_profile_id=default_id)
        self._start_session(fund_id, account_id, session_profile_id)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertEqual(result["active_profile"]["name"], "Session Pick")
        self.assertEqual(result["next_trade"]["amount"], 9.0)

    def test_running_session_with_no_profile_of_its_own_does_not_borrow_the_fund_default(self):
        # A Fund's default_risk_profile_id is only resolved onto a
        # session ONCE, at start time, by api/sessions.py - it never
        # retroactively reattaches to an already-running session. Showing
        # it as "active" here would be a real display/compute mismatch:
        # compute_position_size would still use the legacy global
        # fallback for this exact session, not this profile's rules.
        fund_id, account_id = self._connected_fund()
        default_id = database.create_risk_profile("Default", sizing_mode="fixed", fixed_amount=7, bankroll=1000)
        database.update_fund(fund_id, default_risk_profile_id=default_id)
        self._start_session(fund_id, account_id, None)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertIsNone(result["active_profile"])
        self.assertNotEqual(result["next_trade"]["amount"], 7.0)
        self.assertTrue(result["next_trade"]["is_live"])
        self.assertIn("legacy global default", result["next_trade"]["reason"])

    def test_martingale_step_appears_in_the_reasoning_trail(self):
        fund_id, account_id = self._connected_fund()
        profile_id = database.create_risk_profile("Mart Strategy", sizing_mode="fixed", fixed_amount=5, bankroll=1000)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2, reset_after_win=True)
        session_id = self._start_session(fund_id, account_id, profile_id)
        database.advance_martingale_step(session_id)
        database.advance_martingale_step(session_id)  # two real losses -> step 2

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertIn("step 2", result["next_trade"]["reason"])
        self.assertEqual(result["next_trade"]["amount"], 20.0)  # 5 * 2^2

    def test_fortress_stop_is_surfaced_as_blocked_not_a_crash(self):
        fund_id, account_id = self._connected_fund()
        profile_id = database.create_risk_profile("Fortress Strategy", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        database.update_fortress_settings(profile_id, enabled=True, protected_principal=1000)
        session_id = self._start_session(fund_id, account_id, profile_id)
        database.update_session_pnl(session_id, 0)  # exactly back to protected principal

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertIsNone(result["next_trade"]["amount"])
        self.assertEqual(result["next_trade"]["blocked_by"], "fortress_principal_protected")
        self.assertIn("protected", result["next_trade"]["reason"])

    def test_preview_does_not_persist_apex_ascension_tier_crossing(self):
        # The record_events=False contract, exercised end-to-end through
        # this module - looking at the Risk Control Center must not
        # itself advance real Apex Ascension state.
        fund_id, account_id = self._connected_fund()
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.update_apex_ascension_settings(profile_id, enabled=True, starting_bankroll=1000)
        session_id = self._start_session(fund_id, account_id, profile_id)
        database.update_session_pnl(session_id, 1500)  # would cross into tier 1

        risk_control_center.get_risk_control_center(fund_id)

        self.assertEqual(database.list_tier_events(profile_id), [])
        self.assertEqual(database.get_apex_ascension_settings(profile_id)["highest_tier_reached"], 0)


class OutcomeEstimateTests(RiskControlCenterTestCase):
    def test_loss_is_real_win_is_a_labeled_estimate(self):
        fund_id, account_id = self._connected_fund(starting_balance=1000)
        profile_id = database.create_risk_profile("Fixed 10", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        database.start_trading_session("S", [1], "DEMO", fund_id=fund_id, broker_account_id=account_id, risk_profile_id=profile_id)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertFalse(result["if_it_loses"]["is_estimate"])
        self.assertEqual(result["if_it_loses"]["amount"], -10.0)
        self.assertEqual(result["if_it_loses"]["bankroll_after"], 990.0)

        self.assertTrue(result["if_it_wins"]["is_estimate"])
        self.assertEqual(result["if_it_wins"]["payout_assumption_percent"], 88)
        self.assertEqual(result["if_it_wins"]["amount"], 8.8)
        self.assertEqual(result["if_it_wins"]["bankroll_after"], 1008.8)


class GlobalProtectionTests(RiskControlCenterTestCase):
    def test_unconfigured_daily_loss_limit_is_reported_as_not_configured(self):
        fund_id, _ = self._connected_fund()
        database.set_setting("max_daily_loss", 0)
        result = risk_control_center.get_risk_control_center(fund_id)
        check = next(c for c in result["protections"] if c["name"] == "Daily Loss Limit")
        self.assertFalse(check["configured"])
        self.assertFalse(check["tripped"])

    def test_zero_max_consecutive_losses_is_tripped_immediately(self):
        # Matches risk_manager.diagnose_settings' own finding: 0 currently-
        # open trades already counts as "at the limit" when the limit is 0.
        fund_id, _ = self._connected_fund()
        database.set_setting("max_consecutive_losses", 0)
        result = risk_control_center.get_risk_control_center(fund_id)
        check = next(c for c in result["protections"] if c["name"] == "Consecutive-Loss Lock")
        self.assertTrue(check["tripped"])
        self.assertFalse(result["safe_to_trade"])

    def test_broker_disconnected_shows_up_as_a_tripped_fund_protection(self):
        account_id = database.create_broker_account("Test Broker", mode="demo")
        fund_id = database.create_fund("F", starting_balance=1000)
        database.assign_broker_account_to_fund(fund_id, account_id)
        # connection_status defaults to "disconnected" - never explicitly connected.

        result = risk_control_center.get_risk_control_center(fund_id)

        broker_check = next(c for c in result["protections"] if c["name"] == "Broker Connected")
        self.assertTrue(broker_check["tripped"])
        self.assertFalse(result["safe_to_trade"])


class MaxExposureTodayTests(RiskControlCenterTestCase):
    def test_fund_loss_limit_headroom_is_computed(self):
        fund_id, _ = self._connected_fund()
        database.update_fund(fund_id, loss_limit=200)

        result = risk_control_center.get_risk_control_center(fund_id)

        self.assertEqual(result["max_exposure_today"]["headroom_to_loss_limit"], 200)
        self.assertEqual(result["max_exposure_today"]["headroom_source"], "fund lifetime loss limit")

    def test_no_loss_limit_configured_gives_no_headroom_figure(self):
        fund_id, _ = self._connected_fund()
        result = risk_control_center.get_risk_control_center(fund_id)
        self.assertIsNone(result["max_exposure_today"]["headroom_to_loss_limit"])


if __name__ == "__main__":
    unittest.main()
