import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
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


_MARTIN_TRADER_CHAT_ID = "-1002122892148"

# Faithful replica of the real message_id=26622 text captured live in
# channel_messages during the forensic investigation - keycap-emoji
# Martingale re-entries, exactly as Martin Trader actually publishes it.
_REAL_MARTIN_TRADER_MESSAGE_TEXT = (
    "⚡ SIGNAL\n\n"
    "\U0001f1e8\U0001f1e6 CAD/JPY \U0001f1ef\U0001f1f5 OTC\n"
    "Timeframe: M5\n"
    "⏱ Expiration: 5 minutes\n"
    "⏰ Entry: {entry}\n"
    "\U0001f7e9 Direction: BUY\n\n"
    "\U0001f4ca Martingale:\n"
    "1⃣ {e2}\n"
    "2⃣ {e3}\n"
    "3⃣ {e4}"
)


class _FakeChat:
    title = "⚡️ Martin Trader \U0001f4af"
    username = None


class _FakeReplyTo:
    reply_to_msg_id = None


class _FakeMessage:
    def __init__(self, raw_text):
        self.raw_text = raw_text
        self.reply_to = None
        self.buttons = None


class _FakeSender:
    id = 999888777


class _FakeEvent:
    """Duck-types exactly what telegram_listener.handler(event) touches -
    the same technique proven during this session's manual reproduction
    of the real production defect, now captured as a permanent
    production-path integration test per Section 5's explicit
    requirement ("Add a production-path integration test proving:
    PARSED, series created, Entry 1 scheduled, no fallthrough
    ... Do not test only helper functions in isolation")."""

    def __init__(self, message_id, raw_text, chat_id=_MARTIN_TRADER_CHAT_ID):
        self.id = message_id
        self.chat_id = chat_id
        self.raw_text = raw_text
        self.message = _FakeMessage(raw_text)
        self.date = datetime.now(timezone.utc)

    async def get_chat(self):
        return _FakeChat()

    async def get_sender(self):
        return _FakeSender()


class MartinTraderProductionPathIntegrationTests(unittest.TestCase):
    """The real handler(event) coroutine, unmodified, against an isolated
    temp database - not a reimplementation, not just create_series_from_signal
    called directly. Proves the actual fix (core/database.py's trade_series
    schema migration) plus the exception boundary (core/telegram_listener.py)
    together deliver: RECEIVED -> PARSED -> a real trade_series row with
    Entry 1 scheduled, and critically, NO fallthrough into the normal
    broker_account_manager.route_signal path (which would show up as a
    `signals` row - Martin Trader must never create one of those)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        conn = sqlite3.connect(database.DB_FILE)
        conn.execute(
            "INSERT INTO ui_channels (id, chat_id, username, title, kind, enabled, source_type) "
            "VALUES (?, ?, ?, ?, 'channel', 1, 'passive')",
            (MARTIN_TRADER_CHANNEL_ID, _MARTIN_TRADER_CHAT_ID, None, "⚡️ Martin Trader \U0001f4af"),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _entry_times_in_the_near_future(self):
        # Entry 1 must land safely in the future so it's scheduled, not
        # immediately fired or rejected as stale by the due-entries loop,
        # which this test never starts.
        base = datetime.now() + timedelta(hours=2)
        return [(base + timedelta(minutes=5 * i)).strftime("%H:%M") for i in range(4)]

    def test_real_martin_trader_signal_creates_series_with_entry_1_scheduled_and_no_fallthrough(self):
        e1, e2, e3, e4 = self._entry_times_in_the_near_future()
        message_text = _REAL_MARTIN_TRADER_MESSAGE_TEXT.format(entry=e1, e2=e2, e3=e3, e4=e4)
        event = _FakeEvent(message_id=26622, raw_text=message_text)

        asyncio.run(telegram_listener.handler(event))

        pipeline_events = database.list_pipeline_events_for_message(MARTIN_TRADER_CHANNEL_ID, 26622)
        states = [e["state"] for e in pipeline_events]
        self.assertIn("RECEIVED", states)
        self.assertIn("PARSED", states)
        self.assertNotIn("FAILED", states)

        series = database.get_trade_series_by_message(MARTIN_TRADER_CHANNEL_ID, 26622)
        self.assertIsNotNone(series, "a trade_series row must be created for a valid Martin Trader signal")
        self.assertEqual(series["asset"], "CAD/JPY OTC")
        self.assertEqual(series["direction"], "BUY")
        self.assertEqual(series["stake"], 10.0)
        self.assertEqual(series["max_entries"], 4)
        self.assertEqual(series["current_entry_number"], 0)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["entry_times"][0], e1, "Entry 1 must be scheduled at the published time")
        self.assertEqual(len(series["entry_times"]), 4)

        conn = sqlite3.connect(database.DB_FILE)
        try:
            signals_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            signals_count, 0,
            "Martin Trader must never fall through into the normal route_signal path "
            "(that would create a `signals` row) - it is handled exclusively via trade_series"
        )

    def test_an_exception_inside_the_martin_trader_branch_is_caught_logged_and_never_falls_through(self):
        # Directly exercises the exception boundary added in
        # core/telegram_listener.py's handler() (Section A) - forces the
        # exact failure mode this whole task was created to fix (an
        # uncaught exception inside create_series_from_signal) and proves
        # it is now: caught (never propagates), recorded as a FAILED
        # pipeline event (never silently vanishes), and never falls
        # through into the normal route_signal path.
        e1, e2, e3, e4 = self._entry_times_in_the_near_future()
        message_text = _REAL_MARTIN_TRADER_MESSAGE_TEXT.format(entry=e1, e2=e2, e3=e3, e4=e4)
        event = _FakeEvent(message_id=26622, raw_text=message_text)

        original = telegram_listener.trade_series_engine.create_series_from_signal

        async def _boom(*args, **kwargs):
            raise sqlite3.OperationalError("table trade_series has no column named expiry")

        telegram_listener.trade_series_engine.create_series_from_signal = _boom
        try:
            asyncio.run(telegram_listener.handler(event))
        finally:
            telegram_listener.trade_series_engine.create_series_from_signal = original

        pipeline_events = database.list_pipeline_events_for_message(MARTIN_TRADER_CHANNEL_ID, 26622)
        states = [e["state"] for e in pipeline_events]
        self.assertIn("RECEIVED", states)
        self.assertIn("PARSED", states)
        self.assertIn("FAILED", states, "the exception must be tracked as a real FAILED pipeline event, not silently dropped")
        failed_event = next(e for e in pipeline_events if e["state"] == "FAILED")
        self.assertIn("martin_trader_branch_exception", failed_event["detail"])
        self.assertIn("OperationalError", failed_event["detail"])

        series = database.get_trade_series_by_message(MARTIN_TRADER_CHANNEL_ID, 26622)
        self.assertIsNone(series, "a failed creation attempt must not leave a partial series row")

        conn = sqlite3.connect(database.DB_FILE)
        try:
            signals_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            signals_count, 0,
            "an exception in the Martin Trader branch must never fall through into route_signal either"
        )

    def test_a_non_martin_trader_channel_is_completely_unaffected(self):
        # Same handler, a DIFFERENT channel id/chat_id entirely - must take
        # the normal route_signal path, never trade_series, proving the
        # Martin Trader branch is additive and gated, not a rewrite of the
        # shared handler for every provider.
        conn = sqlite3.connect(database.DB_FILE)
        conn.execute(
            "INSERT INTO ui_channels (id, chat_id, username, title, kind, enabled, source_type) "
            "VALUES (999, '-1009999999999', NULL, 'Some Other Provider', 'channel', 1, 'passive')"
        )
        conn.commit()
        conn.close()

        event = _FakeEvent(
            message_id=1, raw_text="Some unrelated unparseable text with no signal in it at all",
            chat_id="-1009999999999",
        )
        asyncio.run(telegram_listener.handler(event))

        series = database.get_trade_series_by_message(999, 1)
        self.assertIsNone(series, "a non-Martin-Trader channel must never create a trade_series row")


if __name__ == "__main__":
    unittest.main()
