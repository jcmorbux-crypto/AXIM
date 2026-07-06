import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class DatabaseUITests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_seed_channels_from_env_populates_empty_table(self):
        database.seed_channels_from_env(["some_bot", "Some Channel"])
        channels = database.list_channels()
        self.assertEqual(len(channels), 2)
        self.assertTrue(all(c["enabled"] for c in channels))

    def test_seed_channels_from_env_is_noop_if_not_empty(self):
        database.upsert_channel(chat_id="1", username="real_bot", title="Real Bot", kind="user")
        database.seed_channels_from_env(["some_bot", "another_bot"])
        channels = database.list_channels()
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["username"], "real_bot")

    def test_upsert_channel_never_touches_enabled_flag(self):
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A", kind="user")
        channel = database.list_channels()[0]
        database.set_channel_enabled(channel["id"], True)

        # Re-sync (identity refresh) must not silently re-disable it.
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A (renamed)", kind="user")
        refreshed = database.list_channels()[0]
        self.assertTrue(refreshed["enabled"])
        self.assertEqual(refreshed["title"], "Bot A (renamed)")

    def test_get_enabled_channels_excludes_disabled(self):
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A", kind="user")
        database.upsert_channel(chat_id="2", username="bot_b", title="Bot B", kind="user")
        channels = {c["username"]: c["id"] for c in database.list_channels()}
        database.set_channel_enabled(channels["bot_a"], True)

        enabled = database.get_enabled_channels()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["username"], "bot_a")

    def test_record_channel_signal_seen_by_username(self):
        database.upsert_channel(chat_id="1", username="bot_a", title="Bot A", kind="user")
        channel = database.list_channels()[0]
        self.assertIsNone(channel["last_signal_at"])

        database.record_channel_signal_seen(username="bot_a")
        refreshed = database.list_channels()[0]
        self.assertIsNotNone(refreshed["last_signal_at"])

    def test_control_state_defaults_to_inactive(self):
        state = database.get_control_state()
        self.assertFalse(state["paused"])
        self.assertFalse(state["emergency_stop"])
        self.assertFalse(state["test_mode"])

    def test_set_control_state_pause_and_resume(self):
        database.set_control_state(paused=True)
        self.assertTrue(database.get_control_state()["paused"])
        database.set_control_state(paused=False)
        self.assertFalse(database.get_control_state()["paused"])

    def test_set_control_state_partial_update_preserves_other_flag(self):
        database.set_control_state(paused=True)
        database.set_control_state(emergency_stop=True)
        state = database.get_control_state()
        self.assertTrue(state["paused"])
        self.assertTrue(state["emergency_stop"])

    def test_set_control_state_test_mode_preserves_paused_and_emergency_stop(self):
        database.set_control_state(paused=True, emergency_stop=True)
        database.set_control_state(test_mode=True)
        state = database.get_control_state()
        self.assertTrue(state["test_mode"])
        self.assertTrue(state["paused"])
        self.assertTrue(state["emergency_stop"])

        database.set_control_state(test_mode=False)
        self.assertFalse(database.get_control_state()["test_mode"])


if __name__ == "__main__":
    unittest.main()
