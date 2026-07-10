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

    # ---- Listener heartbeat process-health columns (soak-test support) ----

    def test_heartbeat_process_health_columns_round_trip(self):
        database.update_listener_heartbeat(
            generation=2, worker_count=6, demo_mode_verified=True,
            listener_pid=12345, listener_uptime_min=42.5, listener_mem_mb=110.3,
            chrome_count=10, chrome_mem_mb=1500.7,
        )
        hb = database.get_listener_heartbeat()
        self.assertEqual(hb["listener_pid"], 12345)
        self.assertEqual(hb["listener_uptime_min"], 42.5)
        self.assertEqual(hb["listener_mem_mb"], 110.3)
        self.assertEqual(hb["chrome_count"], 10)
        self.assertEqual(hb["chrome_mem_mb"], 1500.7)

    def test_heartbeat_process_health_columns_default_to_none(self):
        """Callers that don't pass the new process-health kwargs (e.g.
        any future caller unaware of them) must not error, and the
        columns should read back as None, not some fabricated 0/false
        value - matches this project's own never-fabricate discipline."""
        database.update_listener_heartbeat(generation=1, worker_count=2, demo_mode_verified=True)
        hb = database.get_listener_heartbeat()
        self.assertIsNone(hb["listener_pid"])
        self.assertIsNone(hb["listener_uptime_min"])
        self.assertIsNone(hb["listener_mem_mb"])
        self.assertIsNone(hb["chrome_count"])
        self.assertIsNone(hb["chrome_mem_mb"])

    def test_heartbeat_process_health_columns_update_on_second_call(self):
        """update_listener_heartbeat upserts a singleton row (id=1) - the
        second call's process-health values must overwrite the first,
        not merge/preserve stale ones from an earlier generation."""
        database.update_listener_heartbeat(
            generation=1, worker_count=2, demo_mode_verified=True,
            listener_pid=111, listener_uptime_min=1.0, listener_mem_mb=50.0,
            chrome_count=2, chrome_mem_mb=200.0,
        )
        database.update_listener_heartbeat(
            generation=2, worker_count=6, demo_mode_verified=True,
            listener_pid=222, listener_uptime_min=99.0, listener_mem_mb=150.0,
            chrome_count=10, chrome_mem_mb=1600.0,
        )
        hb = database.get_listener_heartbeat()
        self.assertEqual(hb["listener_pid"], 222)
        self.assertEqual(hb["chrome_count"], 10)


if __name__ == "__main__":
    unittest.main()
