import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import money_studio
import money_studio_routes as routes

_FAKE_USER = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class MoneyStudioRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_list_strategies_returns_all_5_official_ones(self):
        result = routes.list_strategies(user=_FAKE_USER)
        self.assertEqual(len(result["strategies"]), 5)
        keys = {s["key"] for s in result["strategies"]}
        self.assertEqual(keys, set(money_studio.STRATEGIES_BY_KEY.keys()))

    def test_get_strategy_returns_the_full_detail_payload(self):
        detail = routes.get_strategy("growth_accelerator", user=_FAKE_USER)
        self.assertEqual(detail["key"], "growth_accelerator")
        self.assertIn("worked_example", detail)
        self.assertIn("growth_timeline", detail)

    def test_get_strategy_reports_unknown_keys_clearly(self):
        result = routes.get_strategy("not_a_real_strategy", user=_FAKE_USER)
        self.assertIn("error", result)

    def test_attach_to_fund_sets_the_funds_money_plan_key_with_no_new_profile_row(self):
        fund_id = database.create_fund("My OTC Fund", starting_balance=500.0)
        before = len(database.list_risk_profiles(include_templates=True))
        body = routes.AttachToFundRequest(fund_id=fund_id)
        result = routes.attach_strategy_to_fund("capital_preservation", body, user=_FAKE_USER)
        self.assertEqual(result["default_money_plan_key"], "capital_preservation")
        self.assertIsNone(result["default_risk_profile_id"])
        after = len(database.list_risk_profiles(include_templates=True))
        self.assertEqual(before, after)  # no risk_profiles row was created

    def test_attach_to_fund_clears_any_existing_custom_default(self):
        profile_id = database.create_risk_profile("Old Custom Profile", sizing_mode="fixed", fixed_amount=5)
        fund_id = database.create_fund("Fund With Custom Default", default_risk_profile_id=profile_id)
        body = routes.AttachToFundRequest(fund_id=fund_id)
        result = routes.attach_strategy_to_fund("growth_accelerator", body, user=_FAKE_USER)
        self.assertEqual(result["default_money_plan_key"], "growth_accelerator")
        self.assertIsNone(result["default_risk_profile_id"])

    def test_attach_to_fund_rejects_an_unknown_strategy_key(self):
        from fastapi import HTTPException
        fund_id = database.create_fund("Some Fund")
        body = routes.AttachToFundRequest(fund_id=fund_id)
        with self.assertRaises(HTTPException) as ctx:
            routes.attach_strategy_to_fund("not_a_real_strategy", body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_attach_to_fund_rejects_an_unknown_fund(self):
        from fastapi import HTTPException
        body = routes.AttachToFundRequest(fund_id=999999)
        with self.assertRaises(HTTPException) as ctx:
            routes.attach_strategy_to_fund("capital_preservation", body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
