import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

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


if __name__ == "__main__":
    unittest.main()
