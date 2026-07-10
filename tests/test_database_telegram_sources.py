import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import secrets_store


class ChannelConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A", kind="user")
        self.channel_id = database.list_channels()[0]["id"]

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_new_channel_defaults_to_passive(self):
        channel = database.list_channels()[0]
        self.assertEqual(channel["source_type"], "passive")
        self.assertEqual(channel["priority"], 0)

    def test_set_channel_config_updates_fields(self):
        database.set_channel_config(self.channel_id, source_type="bot_command", trigger_command="/signal", priority=5)
        channel = database.list_channels()[0]
        self.assertEqual(channel["source_type"], "bot_command")
        self.assertEqual(channel["trigger_command"], "/signal")
        self.assertEqual(channel["priority"], 5)

    def test_set_channel_config_rejects_invalid_source_type(self):
        with self.assertRaises(ValueError):
            database.set_channel_config(self.channel_id, source_type="not_a_real_type")

    def test_set_channel_config_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            database.set_channel_config(self.channel_id, enabled=1)

    def test_find_channel_by_chat_id(self):
        found = database.find_channel(chat_id="1")
        self.assertEqual(found["id"], self.channel_id)

    def test_find_channel_falls_back_to_username_then_title(self):
        found = database.find_channel(chat_id="unknown-id", username="bot_a")
        self.assertEqual(found["id"], self.channel_id)
        found2 = database.find_channel(chat_id="unknown-id", username=None, title="Bot A")
        self.assertEqual(found2["id"], self.channel_id)

    def test_find_channel_returns_none_when_no_match(self):
        self.assertIsNone(database.find_channel(chat_id="999", username="nope", title="Nothing"))

    def test_get_channel_by_id(self):
        found = database.get_channel(self.channel_id)
        self.assertEqual(found["id"], self.channel_id)
        self.assertEqual(found["title"], "Bot A")

    def test_get_channel_returns_none_for_unknown_id(self):
        self.assertIsNone(database.get_channel(999999))


class ChannelMessageTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_record_and_get_last_message_by_chat_id(self):
        database.record_channel_message(chat_id="1", username="bot_a", title="Bot A", message_text="hello")
        database.record_channel_message(chat_id="1", username="bot_a", title="Bot A", message_text="world")
        last = database.get_last_channel_message(chat_id="1")
        self.assertEqual(last["message_text"], "world")

    def test_list_recent_channel_messages_scoped_to_chat(self):
        database.record_channel_message(chat_id="1", message_text="a")
        database.record_channel_message(chat_id="2", message_text="b")
        recent = database.list_recent_channel_messages(chat_id="1")
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["message_text"], "a")

    def test_get_last_channel_message_none_when_empty(self):
        self.assertIsNone(database.get_last_channel_message(chat_id="999"))


class SignalRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A", kind="user")
        self.channel_id = database.list_channels()[0]["id"]

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_and_list_rule(self):
        database.create_signal_rule(self.channel_id, r"Signal:\s*", "Direction: ", rule_name="normalize label")
        rules = database.list_signal_rules(self.channel_id)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["find_pattern"], r"Signal:\s*")

    def test_disabled_rule_excluded_from_enabled_rules(self):
        rule_id = database.create_signal_rule(self.channel_id, "a", "b")
        database.set_signal_rule_enabled(rule_id, False)
        self.assertEqual(database.get_enabled_rules_for_channel(self.channel_id), [])

    def test_delete_rule(self):
        rule_id = database.create_signal_rule(self.channel_id, "a", "b")
        database.delete_signal_rule(rule_id)
        self.assertEqual(database.list_signal_rules(self.channel_id), [])


class TelegramCredentialsTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._original_key_file = secrets_store.KEY_FILE
        secrets_store.KEY_FILE = Path(self._tmp_dir.name) / ".secret_key"

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        secrets_store.KEY_FILE = self._original_key_file
        self._tmp_dir.cleanup()

    def test_status_before_configured(self):
        status = database.get_telegram_credentials_status()
        self.assertFalse(status["configured"])

    def test_set_and_get_status_masks_phone(self):
        database.set_telegram_credentials(12345, "abcabcabc", "+15551234567")
        status = database.get_telegram_credentials_status()
        self.assertTrue(status["configured"])
        self.assertNotIn("15551234567", status["phone_masked"])
        self.assertTrue(status["phone_masked"].endswith("4567"))

    def test_get_decrypted_roundtrips(self):
        database.set_telegram_credentials(12345, "abcabcabc", "+15551234567")
        api_id, api_hash, phone = database.get_decrypted_telegram_credentials()
        self.assertEqual(api_id, 12345)
        self.assertEqual(api_hash, "abcabcabc")
        self.assertEqual(phone, "+15551234567")

    def test_overwriting_credentials_replaces_values(self):
        database.set_telegram_credentials(1, "a", "+10000000000")
        database.set_telegram_credentials(2, "b", "+20000000000")
        api_id, api_hash, phone = database.get_decrypted_telegram_credentials()
        self.assertEqual(api_id, 2)
        self.assertEqual(phone, "+20000000000")


if __name__ == "__main__":
    unittest.main()
