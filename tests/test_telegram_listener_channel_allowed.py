import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import telegram_listener


def _channel(chat_id, title, username=None):
    return {"chat_id": str(chat_id) if chat_id is not None else None, "title": title, "username": username}


class ChannelAllowedTests(unittest.TestCase):
    """channel_allowed() is the live authorization gate telegram_listener.py's
    handler() checks before letting a passive channel's signals reach the
    trade pipeline at all. Real bug found live in production (2026-07-18):
    an operator explicitly disabled "OTC Pro Trading Robot", but its
    messages kept being treated as allowed, because a DIFFERENT, separately
    -enabled channel's title ("Pro Trading Robot") is a literal substring
    of the disabled channel's title - the old fuzzy title-only match had no
    way to tell the two apart. Confirmed live: channel_allowed("OTC Pro
    Trading Robot", None) returned True with zero chat_id awareness."""

    def setUp(self):
        self._orig = telegram_listener.database.get_enabled_channels
        self._orig_watch = telegram_listener.WATCH_CHANNELS

    def tearDown(self):
        telegram_listener.database.get_enabled_channels = self._orig
        telegram_listener.WATCH_CHANNELS = self._orig_watch

    def _set_enabled(self, channels):
        telegram_listener.database.get_enabled_channels = lambda: channels

    def test_exact_chat_id_match_allows(self):
        self._set_enabled([_channel(-1001515679451, "Pro Trading Robot")])
        self.assertTrue(
            telegram_listener.channel_allowed("Pro Trading Robot", None, -1001515679451)
        )

    def test_disabled_channel_whose_title_contains_an_enabled_channels_title_is_rejected(self):
        # The real production bug: "Pro Trading Robot" (enabled, its own
        # chat_id) must NOT authorize "OTC Pro Trading Robot" (a different,
        # disabled chat_id) just because its title is a substring.
        self._set_enabled([_channel(-1001515679451, "Pro Trading Robot")])
        self.assertFalse(
            telegram_listener.channel_allowed("OTC Pro Trading Robot", None, -1001851061994)
        )

    def test_wrong_chat_id_never_falls_back_to_fuzzy_title_match(self):
        self._set_enabled([_channel(-1001515679451, "Pro Trading Robot")])
        self.assertFalse(
            telegram_listener.channel_allowed("Pro Trading Robot", None, -999999999)
        )

    def test_username_exact_match_still_allows(self):
        self._set_enabled([_channel(None, "Some Channel", username="realsignals")])
        self.assertTrue(
            telegram_listener.channel_allowed("A Totally Different Title", "realsignals", 12345)
        )

    def test_no_chat_id_available_falls_back_to_fuzzy_title_match(self):
        # The TELEGRAM_DEBUG_LOG preview path doesn't have a chat_id yet -
        # fuzzy title matching remains the only option there.
        self._set_enabled([_channel(None, "Pro Trading Robot")])
        self.assertTrue(
            telegram_listener.channel_allowed("Pro Trading Robot Extra Words", None, None)
        )

    def test_enabled_row_with_no_chat_id_yet_falls_back_to_fuzzy_title_match(self):
        # A channel seeded from WATCH_CHANNELS but never synced yet has no
        # real chat_id in ui_channels - must still match by title/username.
        self._set_enabled([_channel(None, "Pro Trading Robot")])
        self.assertTrue(
            telegram_listener.channel_allowed("Pro Trading Robot", None, -1001515679451)
        )

    def test_watch_channels_env_substring_still_works(self):
        telegram_listener.WATCH_CHANNELS = ["go_plusbot"]
        self._set_enabled([])
        self.assertTrue(telegram_listener.channel_allowed("Go+ | Trading Bot", "go_plusbot", 555))

    def test_nothing_configured_fails_closed(self):
        telegram_listener.WATCH_CHANNELS = []
        self._set_enabled([])
        self.assertFalse(telegram_listener.channel_allowed("Anything At All", "anything", 1))

    def test_disabled_channel_with_no_matching_enabled_row_at_all_is_rejected(self):
        self._set_enabled([_channel(-111, "Totally Unrelated Channel")])
        self.assertFalse(
            telegram_listener.channel_allowed("OTC Pro Trading Robot", None, -1001851061994)
        )


if __name__ == "__main__":
    unittest.main()
