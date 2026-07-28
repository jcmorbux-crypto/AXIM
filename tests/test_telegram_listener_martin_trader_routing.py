import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import telegram_listener
import trade_series_engine
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
# 2026-07-27 next-five-minute-boundary redesign: these published clock
# times are preserved for AUDIT ONLY - never consulted for scheduling.
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

_SOME_PUBLISHED_ENTRY_TIMES = ("14:00", "14:05", "14:10", "14:15")


def _martin_trader_message_text():
    e1, e2, e3, e4 = _SOME_PUBLISHED_ENTRY_TIMES
    return _REAL_MARTIN_TRADER_MESSAGE_TEXT.format(entry=e1, e2=e2, e3=e3, e4=e4)


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
    production-path integration test. `date` defaults to real now but is
    freely overridable after construction (a plain attribute) - the
    boundary tests below set it to a deterministic, frozen value."""

    def __init__(self, message_id, raw_text, chat_id=_MARTIN_TRADER_CHAT_ID, date=None):
        self.id = message_id
        self.chat_id = chat_id
        self.raw_text = raw_text
        self.message = _FakeMessage(raw_text)
        self.date = date if date is not None else datetime.now(timezone.utc)

    async def get_chat(self):
        return _FakeChat()

    async def get_sender(self):
        return _FakeSender()


class MartinTraderProductionPathIntegrationTests(unittest.TestCase):
    """The real handler(event) coroutine, unmodified, against an isolated
    temp database - not a reimplementation, not just create_series_from_signal
    called directly. Proves the current, simplified Martin Trader contract
    (2026-07-27 next-five-minute-boundary redesign): signal receipt
    determines Entry #1, published/provider-timezone data is audit-only,
    and there is exactly one executable UTC timestamp at creation time."""

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

    def _signals_count(self):
        conn = sqlite3.connect(database.DB_FILE)
        try:
            return conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        finally:
            conn.close()

    def test_real_martin_trader_signal_creates_series_with_entry_1_scheduled_and_no_fallthrough(self):
        """The full "Real handler verification" checklist in one pass,
        frozen to a deterministic reference time so entry_1_utc is exact,
        not just "somewhere in the next 5 minutes"."""
        frozen_now = datetime(2026, 7, 27, 12, 2, 30, tzinfo=timezone.utc)
        event = _FakeEvent(message_id=26622, raw_text=_martin_trader_message_text(), date=frozen_now)

        with patch.object(trade_series_engine, "_now_utc", return_value=frozen_now):
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
        self.assertEqual(series["stake"], 10.0, "exactly $10")
        self.assertEqual(series["max_entries"], 4, "always 4, regardless of how many times the provider published")
        self.assertEqual(series["current_entry_number"], 0)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["schedule_resolution_method"], "next_five_minute_boundary_v1")

        # Published times are preserved, but ONLY as audit data - never
        # consulted for scheduling (that's entry_times_utc, checked below).
        self.assertEqual(series["entry_times"], list(_SOME_PUBLISHED_ENTRY_TIMES),
                          "the four published clock times must still be persisted, for audit purposes only")
        self.assertEqual(series["published_entry_time"], _SOME_PUBLISHED_ENTRY_TIMES[0])

        # Telegram's own timestamp is persisted for audit, distinct from
        # whatever reference was actually used to compute the schedule.
        self.assertEqual(series["telegram_message_date_utc"], frozen_now.isoformat())

        # Exactly ONE executable UTC timestamp exists at creation time -
        # Entries #2-4 do not exist yet.
        self.assertEqual(len(series["entry_times_utc"]), 1, "only Entry 1 is computed at creation time")
        entry_1_utc = datetime.fromisoformat(series["entry_times_utc"][0])

        self.assertIsNotNone(entry_1_utc.tzinfo, "must be timezone-aware")
        self.assertEqual(entry_1_utc.utcoffset(), timedelta(0), "must be UTC")
        self.assertEqual(entry_1_utc.second, 0)
        self.assertEqual(entry_1_utc.microsecond, 0)
        self.assertGreater(entry_1_utc, frozen_now, "strictly after the Telegram signal timestamp")
        self.assertLessEqual(entry_1_utc, frozen_now + timedelta(minutes=5),
                              "no more than five minutes after the signal timestamp")
        self.assertEqual(entry_1_utc.minute % 5, 0, "aligned to a minute divisible by five")
        # Exact value for this specific frozen reference (12:02:30 -> 12:05:00).
        self.assertEqual(entry_1_utc, datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc))

        self.assertEqual(
            self._signals_count(), 0,
            "Martin Trader must never fall through into the normal route_signal path "
            "(that would create a `signals` row) - it is handled exclusively via trade_series"
        )

    def test_provider_timezone_has_no_effect_on_entry_1_execution(self):
        """2026-07-27 redesign explicit requirement: provider timezone
        must have zero influence on Entry #1's scheduled time. Setting an
        exotic, far-from-UTC provider_timezone on the channel's profile
        must produce the EXACT SAME entry_1_utc as the default case."""
        profile_id = database.create_provider_profile(MARTIN_TRADER_CHANNEL_ID, timezone="Pacific/Kiritimati")
        self.assertIsNotNone(profile_id)

        frozen_now = datetime(2026, 7, 27, 12, 2, 30, tzinfo=timezone.utc)
        event = _FakeEvent(message_id=26622, raw_text=_martin_trader_message_text(), date=frozen_now)
        with patch.object(trade_series_engine, "_now_utc", return_value=frozen_now):
            asyncio.run(telegram_listener.handler(event))

        series = database.get_trade_series_by_message(MARTIN_TRADER_CHANNEL_ID, 26622)
        entry_1_utc = datetime.fromisoformat(series["entry_times_utc"][0])
        self.assertEqual(entry_1_utc, datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc),
                          "provider_timezone='Pacific/Kiritimati' (UTC+14) must not shift Entry 1 at all")

    def test_duplicate_telegram_message_via_real_handler_does_not_create_a_second_series(self):
        """Idempotent by (channel_id, source_message_id) through the REAL
        handler path - a redelivered Telegram event for a message already
        turned into a series must return/reuse that same series, never
        start a second one."""
        frozen_now = datetime(2026, 7, 27, 12, 2, 30, tzinfo=timezone.utc)
        event = _FakeEvent(message_id=26622, raw_text=_martin_trader_message_text(), date=frozen_now)

        with patch.object(trade_series_engine, "_now_utc", return_value=frozen_now):
            asyncio.run(telegram_listener.handler(event))
            # A second, later delivery of the exact same message.
            asyncio.run(telegram_listener.handler(event))

        conn = sqlite3.connect(database.DB_FILE)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM trade_series WHERE channel_id = ? AND source_message_id = 26622",
                (MARTIN_TRADER_CHANNEL_ID,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1, "a redelivered Telegram message must never create a second series")

    def test_an_exception_inside_the_martin_trader_branch_is_caught_logged_and_never_falls_through(self):
        # Directly exercises the exception boundary in
        # core/telegram_listener.py's handler() - forces the exact
        # failure mode a real production defect once took (an uncaught
        # exception inside create_series_from_signal) and proves it is
        # caught (never propagates), recorded as a FAILED pipeline event
        # (never silently vanishes), and never falls through into the
        # normal route_signal path.
        event = _FakeEvent(message_id=26622, raw_text=_martin_trader_message_text())

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
        self.assertEqual(
            self._signals_count(), 0,
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


class MartinTraderBoundaryHandlerTests(unittest.TestCase):
    """Handler-level (not just pure-function-level) verification of the
    exact five-minute boundary rule, using deterministic frozen
    timestamps - constraint: "Freeze time or inject timestamps in tests;
    do not rely on the real wall clock where deterministic testing is
    possible." Each case patches trade_series_engine._now_utc (the one
    seam create_series_from_signal actually consults for "when was this
    signal received") to a fixed instant, then asserts the real handler
    path produces the exact expected Entry 1 UTC time."""

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
        self._next_message_id = 100000

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _resolved_entry_1_for(self, received_at):
        self._next_message_id += 1
        message_id = self._next_message_id
        event = _FakeEvent(message_id=message_id, raw_text=_martin_trader_message_text(), date=received_at)
        with patch.object(trade_series_engine, "_now_utc", return_value=received_at):
            asyncio.run(telegram_listener.handler(event))
        series = database.get_trade_series_by_message(MARTIN_TRADER_CHANNEL_ID, message_id)
        self.assertIsNotNone(series, f"no series created for received_at={received_at}")
        return datetime.fromisoformat(series["entry_times_utc"][0])

    def test_boundary_cases_via_the_real_handler_path(self):
        cases = [
            (datetime(2026, 7, 27, 12, 0, 1, tzinfo=timezone.utc), datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)),
            (datetime(2026, 7, 27, 12, 2, 30, tzinfo=timezone.utc), datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)),
            (datetime(2026, 7, 27, 12, 4, 59, tzinfo=timezone.utc), datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)),
            # Explicit product rule: exactly on the boundary is too late
            # for it (safer for browser execution latency) - schedules
            # the FOLLOWING boundary, never the same instant.
            (datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc), datetime(2026, 7, 27, 12, 10, 0, tzinfo=timezone.utc)),
            (datetime(2026, 7, 27, 12, 5, 1, tzinfo=timezone.utc), datetime(2026, 7, 27, 12, 10, 0, tzinfo=timezone.utc)),
            (datetime(2026, 7, 27, 12, 58, 30, tzinfo=timezone.utc), datetime(2026, 7, 27, 13, 0, 0, tzinfo=timezone.utc)),
            (datetime(2026, 7, 27, 23, 58, 30, tzinfo=timezone.utc), datetime(2026, 7, 28, 0, 0, 0, tzinfo=timezone.utc)),
        ]
        for received_at, expected in cases:
            with self.subTest(received_at=received_at.isoformat()):
                resolved = self._resolved_entry_1_for(received_at)
                self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
