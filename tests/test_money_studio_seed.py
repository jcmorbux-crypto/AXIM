import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import money_studio


class MoneyStudioSeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_seeds_exactly_the_5_official_strategies(self):
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        seeded = [p for p in profiles if p["strategy_key"] in money_studio.STRATEGIES_BY_KEY]
        self.assertEqual(len(seeded), 5)
        self.assertTrue(all(p["is_template"] for p in seeded))

    def test_seeded_profiles_are_selectable_as_templates(self):
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        keys = {p["strategy_key"] for p in profiles if p["strategy_key"]}
        self.assertEqual(keys, set(money_studio.STRATEGIES_BY_KEY.keys()))

    def test_recovery_ladder_gets_real_martingale_settings(self):
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        ladder = next(p for p in profiles if p["strategy_key"] == "recovery_ladder")
        martingale = database.get_martingale_settings(ladder["id"])
        self.assertTrue(martingale["enabled"])
        self.assertEqual(martingale["max_steps"], money_studio.DEFAULT_RECOVERY_MAX_STEPS)

    def test_capital_preservation_gets_vault_settings(self):
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        preservation = next(p for p in profiles if p["strategy_key"] == "capital_preservation")
        vault = database.get_profit_vault_settings(preservation["id"])
        self.assertTrue(vault["enabled"])
        self.assertEqual(vault["vault_percent"], 25)

    def test_daily_compounding_gets_daily_compounding_settings(self):
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        daily = next(p for p in profiles if p["strategy_key"] == "daily_compounding")
        self.assertEqual(daily["sizing_mode"], "daily_compounding")
        settings = database.get_daily_compounding_settings(daily["id"])
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["risk_percent"], money_studio.DAILY_COMPOUNDING_RISK_PERCENT)

    def test_is_a_no_op_if_templates_already_exist(self):
        database.seed_money_studio_templates()
        database.seed_money_studio_templates()
        profiles = database.list_risk_profiles(include_templates=True)
        seeded = [p for p in profiles if p["strategy_key"] in money_studio.STRATEGIES_BY_KEY]
        self.assertEqual(len(seeded), 5)


if __name__ == "__main__":
    unittest.main()
