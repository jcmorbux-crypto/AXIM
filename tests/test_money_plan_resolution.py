import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "api"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import money_studio


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class NoSeedingRegressionTests(DatabaseTestCase):
    """2026-07-19 product directive: the 5 canonical plans must never
    become risk_profiles rows, on startup or anywhere else - a fresh
    database must contain zero of them."""

    def test_seed_money_studio_templates_no_longer_exists(self):
        self.assertFalse(hasattr(database, "seed_money_studio_templates"))

    def test_seed_risk_profile_templates_no_longer_exists(self):
        self.assertFalse(hasattr(database, "seed_risk_profile_templates"))

    def test_a_freshly_initialized_database_has_no_money_studio_rows(self):
        profiles = database.list_risk_profiles(include_templates=True)
        canonical_keys = set(money_studio.STRATEGIES_BY_KEY.keys())
        leaked = [p for p in profiles if p.get("strategy_key") in canonical_keys]
        self.assertEqual(leaked, [])

    def test_exactly_5_canonical_strategies_defined_in_code(self):
        self.assertEqual(len(money_studio.STRATEGIES), 5)
        self.assertEqual(len(money_studio.STRATEGIES_BY_KEY), 5)

    def test_no_sixth_strategy_can_appear_via_the_api(self):
        import money_studio_routes as routes
        result = routes.list_strategies(user={"id": 1, "email": "x", "role": "owner"})
        self.assertEqual(len(result["strategies"]), 5)


class ResolveMoneyPlanTests(DatabaseTestCase):
    def test_returns_none_when_neither_reference_is_set(self):
        self.assertIsNone(database.resolve_money_plan(None, None))

    def test_resolves_a_real_risk_profile_by_id(self):
        profile_id = database.create_risk_profile("Custom", sizing_mode="fixed", fixed_amount=5)
        resolved = database.resolve_money_plan(profile_id, None)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["id"], profile_id)
        self.assertEqual(resolved["name"], "Custom")

    def test_resolves_a_canonical_plan_key_as_a_virtual_profile(self):
        resolved = database.resolve_money_plan(None, "capital_preservation")
        self.assertIsNotNone(resolved)
        self.assertIsNone(resolved["id"])
        self.assertTrue(resolved["is_virtual"])
        self.assertEqual(resolved["strategy_key"], "capital_preservation")

    def test_money_plan_key_wins_if_both_are_somehow_set(self):
        profile_id = database.create_risk_profile("Custom", sizing_mode="fixed", fixed_amount=5)
        resolved = database.resolve_money_plan(profile_id, "growth_accelerator")
        self.assertTrue(resolved["is_virtual"])
        self.assertEqual(resolved["strategy_key"], "growth_accelerator")

    def test_unknown_plan_key_resolves_to_none(self):
        self.assertIsNone(database.resolve_money_plan(None, "not_a_real_strategy"))


class SessionMoneyPlanKeyMutualExclusivityTests(DatabaseTestCase):
    def test_start_trading_session_accepts_a_money_plan_key(self):
        session_id = database.start_trading_session(
            "Plan Session", channel_ids=[1], account_mode="DEMO", money_plan_key="capital_preservation",
        )
        session = database.get_trading_session(session_id)
        self.assertEqual(session["money_plan_key"], "capital_preservation")
        self.assertIsNone(session["risk_profile_id"])

    def test_set_session_risk_profile_with_a_plan_key_clears_risk_profile_id(self):
        profile_id = database.create_risk_profile("Custom", sizing_mode="fixed", fixed_amount=5)
        session_id = database.start_trading_session(
            "Session", channel_ids=[1], account_mode="DEMO", risk_profile_id=profile_id,
        )
        database.set_session_risk_profile(session_id, None, money_plan_key="recovery_ladder")
        session = database.get_trading_session(session_id)
        self.assertIsNone(session["risk_profile_id"])
        self.assertEqual(session["money_plan_key"], "recovery_ladder")

    def test_set_session_risk_profile_with_a_real_id_clears_money_plan_key(self):
        session_id = database.start_trading_session(
            "Session", channel_ids=[1], account_mode="DEMO", money_plan_key="growth_accelerator",
        )
        profile_id = database.create_risk_profile("Custom", sizing_mode="fixed", fixed_amount=5)
        database.set_session_risk_profile(session_id, profile_id)
        session = database.get_trading_session(session_id)
        self.assertEqual(session["risk_profile_id"], profile_id)
        self.assertIsNone(session["money_plan_key"])


class FundMoneyPlanKeyTests(DatabaseTestCase):
    def test_create_fund_accepts_a_default_money_plan_key(self):
        fund_id = database.create_fund("A Fund", default_money_plan_key="alternating_compound")
        fund = database.get_fund(fund_id)
        self.assertEqual(fund["default_money_plan_key"], "alternating_compound")
        self.assertIsNone(fund["default_risk_profile_id"])

    def test_update_fund_can_switch_from_a_custom_profile_to_a_canonical_plan(self):
        profile_id = database.create_risk_profile("Custom", sizing_mode="fixed", fixed_amount=5)
        fund_id = database.create_fund("A Fund", default_risk_profile_id=profile_id)
        database.update_fund(fund_id, default_money_plan_key="daily_compounding", default_risk_profile_id=None)
        fund = database.get_fund(fund_id)
        self.assertEqual(fund["default_money_plan_key"], "daily_compounding")
        self.assertIsNone(fund["default_risk_profile_id"])

    def test_duplicate_fund_carries_over_the_money_plan_key(self):
        fund_id = database.create_fund("Original", default_money_plan_key="capital_preservation")
        new_id = database.duplicate_fund(fund_id, "Copy")
        self.assertEqual(database.get_fund(new_id)["default_money_plan_key"], "capital_preservation")


if __name__ == "__main__":
    unittest.main()
