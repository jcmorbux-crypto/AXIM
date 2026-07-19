import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class SetSettingAuditTests(unittest.TestCase):
    """set_setting previously overwrote ui_settings in place with no
    history at all - these cover the new settings_audit_log trail that
    makes "who changed the daily-loss limit and when" answerable."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_first_write_logs_old_value_as_none(self):
        database.set_setting("max_daily_loss", 200, changed_by="owner@axim.local", reason="initial setup")
        log = database.list_settings_audit_log(key="max_daily_loss")
        self.assertEqual(len(log), 1)
        self.assertIsNone(log[0]["old_value"])
        self.assertEqual(log[0]["new_value"], 200)
        self.assertEqual(log[0]["changed_by"], "owner@axim.local")
        self.assertEqual(log[0]["reason"], "initial setup")

    def test_changing_value_logs_old_and_new(self):
        database.set_setting("max_daily_loss", 200, changed_by="owner@axim.local")
        database.set_setting("max_daily_loss", 50, changed_by="owner@axim.local", source="ui_trading_settings")
        log = database.list_settings_audit_log(key="max_daily_loss")
        self.assertEqual(len(log), 2)
        # newest first
        self.assertEqual(log[0]["old_value"], 200)
        self.assertEqual(log[0]["new_value"], 50)
        self.assertEqual(log[0]["source"], "ui_trading_settings")
        self.assertEqual(log[1]["old_value"], None)
        self.assertEqual(log[1]["new_value"], 200)

    def test_resaving_same_value_does_not_log(self):
        database.set_setting("max_daily_loss", 200, changed_by="owner@axim.local")
        database.set_setting("max_daily_loss", 200, changed_by="owner@axim.local")
        log = database.list_settings_audit_log(key="max_daily_loss")
        self.assertEqual(len(log), 1)

    def test_set_setting_without_changed_by_still_logs(self):
        # Internal callers (tests, risk_manager bookkeeping) don't always
        # pass changed_by - the audit row still gets created, just with a
        # null actor, rather than silently skipping the log.
        database.set_setting("max_daily_loss", 200)
        log = database.list_settings_audit_log(key="max_daily_loss")
        self.assertEqual(len(log), 1)
        self.assertIsNone(log[0]["changed_by"])

    def test_list_settings_audit_log_filters_by_key(self):
        database.set_setting("max_daily_loss", 200)
        database.set_setting("minimum_payout", 85)
        log = database.list_settings_audit_log(key="minimum_payout")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["key"], "minimum_payout")

    def test_list_settings_audit_log_no_filter_returns_all_newest_first(self):
        database.set_setting("max_daily_loss", 200)
        database.set_setting("minimum_payout", 85)
        log = database.list_settings_audit_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["key"], "minimum_payout")
        self.assertEqual(log[1]["key"], "max_daily_loss")

    def test_get_settings_audit_entry_by_id(self):
        database.set_setting("max_daily_loss", 200)
        entry_id = database.list_settings_audit_log()[0]["id"]
        entry = database.get_settings_audit_entry(entry_id)
        self.assertEqual(entry["key"], "max_daily_loss")
        self.assertEqual(entry["new_value"], 200)

    def test_get_settings_audit_entry_missing_returns_none(self):
        self.assertIsNone(database.get_settings_audit_entry(9999))

    def test_restore_setting_from_audit_reverts_value_and_logs_new_entry(self):
        database.set_setting("max_daily_loss", 200, changed_by="owner@axim.local")
        database.set_setting("max_daily_loss", 50, changed_by="owner@axim.local")
        old_entry_id = database.list_settings_audit_log(key="max_daily_loss")[1]["id"]  # the 200 entry

        key, value = database.restore_setting_from_audit(old_entry_id, changed_by="admin@axim.local")

        self.assertEqual(key, "max_daily_loss")
        self.assertEqual(value, 200)
        self.assertEqual(database.get_setting("max_daily_loss"), 200)

        log = database.list_settings_audit_log(key="max_daily_loss")
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["new_value"], 200)
        self.assertEqual(log[0]["old_value"], 50)
        self.assertEqual(log[0]["source"], "restore")
        self.assertEqual(log[0]["changed_by"], "admin@axim.local")
        self.assertIn(str(old_entry_id), log[0]["reason"])

    def test_restore_setting_from_missing_entry_raises(self):
        with self.assertRaises(ValueError):
            database.restore_setting_from_audit(9999, changed_by="admin@axim.local")


if __name__ == "__main__":
    unittest.main()
