import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class RiskProfileSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_source_profile(self):
        profile_id = database.create_risk_profile(
            "Source Profile", is_template=True, description="original",
            sizing_mode="percent", fixed_amount=10, percent_of_bankroll=2.5,
        )
        database.update_martingale_settings(profile_id, enabled=True, max_steps=3, multiplier=2.2)
        database.update_compounding_settings(profile_id, mode="daily", base_risk_percent=2.0)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=15.0)
        return profile_id

    def test_snapshot_creates_independent_non_template_profile(self):
        source_id = self._make_source_profile()
        snapshot = database.get_risk_profile(source_id)
        new_id = database.create_risk_profile_from_snapshot("Deployed Copy", snapshot)

        new_profile = database.get_risk_profile(new_id)
        self.assertNotEqual(new_id, source_id)
        self.assertEqual(new_profile["name"], "Deployed Copy")
        self.assertFalse(new_profile["is_template"])
        self.assertEqual(new_profile["percent_of_bankroll"], 2.5)
        self.assertTrue(new_profile["martingale"]["enabled"])
        self.assertEqual(new_profile["martingale"]["max_steps"], 3)
        self.assertEqual(new_profile["compounding"]["mode"], "daily")
        self.assertTrue(new_profile["profit_vault"]["enabled"])
        self.assertEqual(new_profile["profit_vault"]["vault_percent"], 15.0)

    def test_editing_the_copy_does_not_affect_the_source(self):
        source_id = self._make_source_profile()
        snapshot = database.get_risk_profile(source_id)
        new_id = database.create_risk_profile_from_snapshot("Deployed Copy", snapshot)

        database.update_risk_profile(new_id, percent_of_bankroll=9.9)
        self.assertEqual(database.get_risk_profile(source_id)["percent_of_bankroll"], 2.5)
        self.assertEqual(database.get_risk_profile(new_id)["percent_of_bankroll"], 9.9)

    def test_works_even_if_original_source_profile_is_later_deleted(self):
        # This is the actual deploy scenario - the snapshot was taken at
        # backtest time and must not depend on the source profile still
        # existing.
        source_id = self._make_source_profile()
        snapshot = database.get_risk_profile(source_id)
        database.delete_risk_profile(source_id)

        new_id = database.create_risk_profile_from_snapshot("Deployed Copy", snapshot)
        self.assertIsNotNone(database.get_risk_profile(new_id))

    def test_duplicate_risk_profile_still_works_via_shared_logic(self):
        source_id = self._make_source_profile()
        new_id = database.duplicate_risk_profile(source_id, "Duplicated")
        new_profile = database.get_risk_profile(new_id)
        self.assertEqual(new_profile["name"], "Duplicated")
        self.assertFalse(new_profile["is_template"])
        self.assertEqual(new_profile["percent_of_bankroll"], 2.5)


if __name__ == "__main__":
    unittest.main()
