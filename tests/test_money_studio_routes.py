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

    def test_list_strategies_returns_all_4_official_ones(self):
        result = routes.list_strategies(user=_FAKE_USER)
        self.assertEqual(len(result["strategies"]), 4)
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

    def test_create_profile_from_strategy_saves_a_real_risk_profile(self):
        body = routes.CreateFromStrategyRequest(name="My OTC Fund", bankroll=500.0)
        created = routes.create_profile_from_strategy("capital_preservation", body, user=_FAKE_USER)
        self.assertEqual(created["name"], "My OTC Fund")
        self.assertEqual(created["strategy_key"], "capital_preservation")
        self.assertEqual(created["percent_of_bankroll"], 1.0)

    def test_create_profile_from_strategy_wires_up_martingale_for_recovery_ladder(self):
        body = routes.CreateFromStrategyRequest(name="Recovery Fund", bankroll=1000.0)
        created = routes.create_profile_from_strategy("recovery_ladder", body, user=_FAKE_USER)
        martingale = database.get_martingale_settings(created["id"])
        self.assertTrue(martingale["enabled"])
        self.assertEqual(martingale["max_steps"], money_studio.DEFAULT_RECOVERY_MAX_STEPS)

    def test_create_profile_from_strategy_wires_up_vault_for_capital_preservation(self):
        body = routes.CreateFromStrategyRequest(name="Preservation Fund", bankroll=1000.0)
        created = routes.create_profile_from_strategy("capital_preservation", body, user=_FAKE_USER)
        vault = database.get_profit_vault_settings(created["id"])
        self.assertTrue(vault["enabled"])
        self.assertEqual(vault["vault_percent"], 25)

    def test_create_profile_from_strategy_wires_up_the_real_cycle_for_alternating_compound(self):
        import json
        body = routes.CreateFromStrategyRequest(name="Alternating Fund", bankroll=1000.0)
        created = routes.create_profile_from_strategy("alternating_compound", body, user=_FAKE_USER)
        compounding = database.get_compounding_settings(created["id"])
        self.assertEqual(compounding["mode"], "alternating_cycle")
        self.assertEqual(json.loads(compounding["steps_json"]), [2.5, 5.0, 2.5, 5.0])

    def test_create_profile_from_strategy_rejects_an_unknown_key(self):
        from fastapi import HTTPException
        body = routes.CreateFromStrategyRequest(name="Bogus", bankroll=1000.0)
        with self.assertRaises(HTTPException) as ctx:
            routes.create_profile_from_strategy("not_a_real_strategy", body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
