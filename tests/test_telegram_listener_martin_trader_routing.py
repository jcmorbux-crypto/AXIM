import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import telegram_listener
from settings import MARTIN_TRADER_CHANNEL_ID


class IsMartinTraderChannelTests(unittest.TestCase):
    """Section B's own requirement: identify Martin Trader only by its
    configured, immutable channel id plus a direct chat_id cross-check -
    never display name, folder position, pinned status, or username text,
    since database.find_channel's shared match precedence can in
    principle resolve an unrelated chat to this row via a username/title
    fallback (see _is_martin_trader_channel's own docstring)."""

    def _channel_row(self, id=MARTIN_TRADER_CHANNEL_ID, chat_id="-1002122892148", **overrides):
        row = {"id": id, "chat_id": chat_id, "title": "⚡️ Martin Trader \U0001f4af", "username": None}
        row.update(overrides)
        return row

    def test_matches_when_id_and_chat_id_both_agree(self):
        row = self._channel_row()
        self.assertTrue(telegram_listener._is_martin_trader_channel(row, "-1002122892148"))

    def test_does_not_match_a_different_channel_id(self):
        row = self._channel_row(id=999, chat_id="-1002122892148")
        self.assertFalse(telegram_listener._is_martin_trader_channel(row, "-1002122892148"))

    def test_does_not_match_when_event_chat_id_disagrees_with_the_stored_row(self):
        # Simulates find_channel() having resolved this row via its
        # username/title fallback for a message that did NOT actually
        # come from Martin Trader's real chat - the row's own id happens
        # to be MARTIN_TRADER_CHANNEL_ID, but the real event's chat_id
        # does not match what's actually on record for it.
        row = self._channel_row(id=MARTIN_TRADER_CHANNEL_ID, chat_id="-1002122892148")
        self.assertFalse(telegram_listener._is_martin_trader_channel(row, "-9999999999999"))

    def test_title_or_username_text_alone_is_never_sufficient(self):
        # A row with the right-looking title/username but the WRONG id
        # and chat_id must never match, regardless of how convincing the
        # display text is.
        row = self._channel_row(id=42, chat_id="-1119999999999",
                                 title="⚡️ Martin Trader \U0001f4af", username="MartinTraderOfficial")
        self.assertFalse(telegram_listener._is_martin_trader_channel(row, "-1119999999999"))

    def test_chat_id_type_mismatch_str_vs_int_still_matches(self):
        # Telethon events carry chat_id as an int; ui_channels stores it
        # as TEXT - the comparison must not be defeated by that alone.
        row = self._channel_row(chat_id="-1002122892148")
        self.assertTrue(telegram_listener._is_martin_trader_channel(row, -1002122892148))


if __name__ == "__main__":
    unittest.main()
