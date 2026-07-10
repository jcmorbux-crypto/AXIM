import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import event_stream_routes


class VisibleToTests(unittest.TestCase):
    """notification.created carries a per-recipient user_id and must only
    reach that recipient's SSE stream - every other bridged event type
    (trade.*, signal.ignored) describes the one shared broker/Telegram
    connection and stays visible to any authenticated user, matching the
    equivalent REST endpoints. See _visible_to's own docstring."""

    def test_notification_visible_to_its_own_recipient(self):
        self.assertTrue(
            event_stream_routes._visible_to(10, "notification.created", {"user_id": 10, "message": "hi"})
        )

    def test_notification_not_visible_to_a_different_user(self):
        self.assertFalse(
            event_stream_routes._visible_to(11, "notification.created", {"user_id": 10, "message": "hi"})
        )

    def test_notification_with_missing_payload_defaults_closed(self):
        self.assertFalse(event_stream_routes._visible_to(10, "notification.created", None))
        self.assertFalse(event_stream_routes._visible_to(10, "notification.created", {}))

    def test_trade_events_are_visible_to_any_authenticated_user(self):
        for event_type in ("trade.signal_received", "signal.ignored", "trade.prepared", "trade.error", "trade.closed"):
            with self.subTest(event_type=event_type):
                self.assertTrue(event_stream_routes._visible_to(999, event_type, {"trade_id": 1}))


class _FakeRequest:
    async def is_disconnected(self):
        return False


class SessionRecheckTests(unittest.TestCase):
    """_event_generator's periodic re-validation (SESSION_RECHECK_SECONDS)
    of the session it was authenticated with at connect time - added so a
    revoked device (Settings > Connected Devices) or a since-expired trial
    can't keep an already-open SSE stream alive indefinitely, unlike every
    other endpoint which re-checks on every request. See the docstring on
    _event_generator itself and docs/AXIM_ROADMAP.md's "SSE session
    recheck" entries for the incidents these close."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._orig_recheck = event_stream_routes.SESSION_RECHECK_SECONDS
        self._orig_keepalive = event_stream_routes.KEEPALIVE_SECONDS
        # Speed both up for the test - same code path, not a mock, just not
        # waiting the real 30s/15s.
        event_stream_routes.SESSION_RECHECK_SECONDS = 0
        event_stream_routes.KEEPALIVE_SECONDS = 0.05

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        event_stream_routes.SESSION_RECHECK_SECONDS = self._orig_recheck
        event_stream_routes.KEEPALIVE_SECONDS = self._orig_keepalive

    def test_still_valid_session_keeps_streaming(self):
        async def run():
            user_id = database.create_user("stream@axim.local", "pw12345678", role="user",
                                            access_state="active")
            token = database.create_session(user_id, expires_hours=1)
            gen = event_stream_routes._event_generator(_FakeRequest(), None, user_id, token)
            for _ in range(3):
                item = await asyncio.wait_for(gen.__anext__(), timeout=2)
                self.assertIn("keep-alive", item)

        asyncio.run(run())

    def test_revoked_session_terminates_the_stream(self):
        async def run():
            user_id = database.create_user("revoked@axim.local", "pw12345678", role="user",
                                            access_state="active")
            token = database.create_session(user_id, expires_hours=1)
            gen = event_stream_routes._event_generator(_FakeRequest(), None, user_id, token)
            await asyncio.wait_for(gen.__anext__(), timeout=2)  # prove it's alive first

            database.delete_session(token)  # same call Connected Devices' Revoke uses

            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(gen.__anext__(), timeout=2)

        asyncio.run(run())

    def test_expired_trial_terminates_the_stream_and_flips_access_state(self):
        async def run():
            user_id = database.create_user("trial@axim.local", "pw12345678", role="user",
                                            access_tier="trial", access_state="trial")
            database.update_user(user_id, trial_expires_at=(datetime.now() - timedelta(days=1)).isoformat())
            token = database.create_session(user_id, expires_hours=1)
            gen = event_stream_routes._event_generator(_FakeRequest(), None, user_id, token)

            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(gen.__anext__(), timeout=2)

            row = database.get_user_by_id(user_id)
            self.assertEqual(row["access_state"], "expired")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
