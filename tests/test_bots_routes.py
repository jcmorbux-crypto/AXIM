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
import bots_routes as routes

_FAKE_USER = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class BotsRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_bot_channel(self, trigger_command="/signal"):
        database.upsert_channel(chat_id=777, username="testbot", title="TestBot", kind="bot")
        channel_id = database.list_channels()[0]["id"]
        database.set_channel_config(channel_id, source_type="bot_command", trigger_command=trigger_command,
                                     command_wait_for_result=1, max_requests_per_session=10)
        return channel_id

    def test_list_bots_is_empty_with_no_bot_command_channels(self):
        database.upsert_channel(chat_id=1, username="passive", title="Passive", kind="channel")
        self.assertEqual(routes.list_bots(user=_FAKE_USER), [])

    def test_list_bots_shows_idle_channel_with_no_active_session(self):
        self._make_bot_channel()
        result = routes.list_bots(user=_FAKE_USER)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "TestBot")
        self.assertEqual(result[0]["trigger_command"], "/signal")
        self.assertIsNone(result[0]["active_session"])

    def test_list_bots_shows_active_session_with_real_attributes(self):
        channel_id = self._make_bot_channel()
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session(
            "S", [channel_id], "DEMO", fund_id=fund_id, broker_account_id=account_id,
            profit_target=50, loss_limit=25,
        )
        database.record_bot_command_activity(session_id, channel_id, "/signal", "2026-01-01T00:00:00",
                                              outcome="no_reply")

        result = routes.list_bots(user=_FAKE_USER)
        session = result[0]["active_session"]
        self.assertIsNotNone(session)
        self.assertEqual(session["id"], session_id)
        self.assertEqual(session["fund_name"], "F1")
        self.assertEqual(session["broker_account_name"], "Acc1")
        self.assertEqual(session["profit_target"], 50)
        self.assertEqual(session["loss_limit"], 25)
        self.assertEqual(session["requests_sent"], 1)
        self.assertEqual(session["last_activity"]["outcome"], "no_reply")

    def test_activity_endpoint_returns_full_log_for_active_session(self):
        channel_id = self._make_bot_channel()
        session_id = database.start_trading_session("S", [channel_id], "DEMO")
        database.record_bot_command_activity(session_id, channel_id, "/signal", "2026-01-01T00:00:00", outcome="no_reply")
        database.record_bot_command_activity(session_id, channel_id, "/signal", "2026-01-01T00:05:00", outcome="no_signal")

        activity = routes.get_bot_activity(channel_id, user=_FAKE_USER)
        self.assertEqual(len(activity), 2)
        self.assertEqual(activity[0]["outcome"], "no_signal")  # most recent first

    def test_activity_endpoint_empty_when_no_active_session_and_none_specified(self):
        channel_id = self._make_bot_channel()
        self.assertEqual(routes.get_bot_activity(channel_id, user=_FAKE_USER), [])

    def test_activity_endpoint_404s_for_unknown_channel(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            routes.get_bot_activity(999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
