import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import trade_series_engine as engine


def _run(coro):
    return asyncio.run(coro)


class TradeSeriesEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    # ---- build_entry_schedule (pure parsing->schedule logic) ----

    def test_build_entry_schedule_combines_entry_time_and_scheduled_entries(self):
        signal = {
            "entry_time": "09:00",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
            ],
        }
        self.assertEqual(engine.build_entry_schedule(signal), ["09:00", "09:05", "09:10", "09:15"])

    def test_build_entry_schedule_caps_at_four_entries(self):
        signal = {
            "entry_time": "09:00",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
                {"entry_number": 5, "time": "09:20"},  # a 5th entry must never be scheduled
            ],
        }
        self.assertEqual(len(engine.build_entry_schedule(signal)), 4)

    def test_build_entry_schedule_returns_none_without_an_entry_time(self):
        self.assertIsNone(engine.build_entry_schedule({"scheduled_entries": []}))

    def test_build_entry_schedule_handles_a_signal_with_no_re_entries(self):
        self.assertEqual(engine.build_entry_schedule({"entry_time": "09:00"}), ["09:00"])

    # ---- _resolve_scheduled_datetime_utc (2026-07-27 Martin Trader
    # timezone incident - the verified, evidence-based replacement for
    # the old naive AXIM-local-clock resolver) ----

    def test_utc_plus_3_published_time_converts_correctly_to_utc(self):
        # Telegram send: 2026-07-27T18:45:00+00:00. Provider timezone
        # UTC+3 (Europe/Moscow, no DST) -> local read at send time is
        # 21:45. A published entry of "21:50" (5 minutes after send, the
        # normal pattern) must resolve to 18:50 UTC - i.e. exactly
        # published-local-time minus the 3h provider offset.
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        resolved = engine._resolve_scheduled_datetime_utc("21:50", "Europe/Moscow", sent)
        self.assertEqual(resolved, datetime(2026, 7, 27, 18, 50, tzinfo=timezone.utc))
        self.assertIsNotNone(resolved.tzinfo)

    def test_utc_plus_3_published_time_converts_correctly_to_pacific_display(self):
        # The resolver always returns UTC internally - Pacific is only
        # ever a DISPLAY conversion applied afterward, never stored.
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        resolved_utc = engine._resolve_scheduled_datetime_utc("21:50", "Europe/Moscow", sent)
        pacific_display = resolved_utc.astimezone(ZoneInfo("America/Los_Angeles"))
        # 18:50 UTC on 2026-07-27 is within PDT (UTC-7) - displays as 11:50.
        self.assertEqual(pacific_display.hour, 11)
        self.assertEqual(pacific_display.minute, 50)
        self.assertEqual(str(pacific_display.tzinfo), "America/Los_Angeles")

    def test_date_rollover_across_midnight(self):
        # Signal sent at 23:58 (provider tz); published entry "00:05" -
        # naive same-day combination would be ~24h in the PAST. Verified
        # real-world pattern (every example examined) is "signal a few
        # minutes before its own entry" - the only interpretation
        # consistent with that is the entry belongs to the NEXT day.
        sent = datetime(2026, 7, 27, 20, 58, tzinfo=timezone.utc)  # 23:58 in UTC+3
        resolved = engine._resolve_scheduled_datetime_utc("00:05", "Europe/Moscow", sent)
        expected_local = datetime(2026, 7, 28, 0, 5, tzinfo=ZoneInfo("Europe/Moscow"))
        self.assertEqual(resolved, expected_local.astimezone(timezone.utc))

    def test_telegram_timestamp_earlier_than_published_entry_needs_no_rollover(self):
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)  # 21:45 in UTC+3
        resolved = engine._resolve_scheduled_datetime_utc("21:50", "Europe/Moscow", sent)
        # Same calendar day in the provider timezone - no rollover applied.
        self.assertEqual(resolved.astimezone(ZoneInfo("Europe/Moscow")).date(), datetime(2026, 7, 27).date())

    def test_telegram_timestamp_after_published_entry_rolls_to_next_day(self):
        # 23:50 in UTC+3, published entry "23:45" - already 5 minutes in
        # the past same-day; only a next-day interpretation is consistent
        # with the verified "signal precedes its own entry" pattern.
        sent = datetime(2026, 7, 27, 20, 50, tzinfo=timezone.utc)  # 23:50 in UTC+3
        resolved = engine._resolve_scheduled_datetime_utc("23:45", "Europe/Moscow", sent)
        self.assertEqual(resolved.astimezone(ZoneInfo("Europe/Moscow")).date(), datetime(2026, 7, 28).date())

    def test_provider_timezone_missing_is_rejected_not_guessed(self):
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._resolve_scheduled_datetime_utc("21:50", None, sent)
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._resolve_scheduled_datetime_utc("21:50", "", sent)

    def test_invalid_timezone_value_is_rejected_not_guessed(self):
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._resolve_scheduled_datetime_utc("21:50", "Not/A_Real_Zone", sent)

    def test_naive_reference_datetime_is_rejected_not_normalized_silently(self):
        # 2026-07-27 Martin Trader timezone incident's exact root cause:
        # a naive datetime silently treated as if it meant something -
        # the corrected resolver must refuse outright, never guess a
        # timezone for it.
        naive_sent = datetime(2026, 7, 27, 18, 45)  # no tzinfo
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._resolve_scheduled_datetime_utc("21:50", "Europe/Moscow", naive_sent)

    def test_resolve_entry_schedule_utc_derives_later_entries_from_entry_1(self):
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        resolved = engine._resolve_entry_schedule_utc(["21:50", "21:55", "22:00", "22:05"], "Europe/Moscow", sent)
        self.assertEqual(len(resolved), 4)
        for i in range(1, 4):
            self.assertEqual(resolved[i] - resolved[0], timedelta(minutes=5 * i))

    def test_resolve_entry_schedule_utc_empty_list_returns_empty(self):
        sent = datetime(2026, 7, 27, 18, 45, tzinfo=timezone.utc)
        self.assertEqual(engine._resolve_entry_schedule_utc([], "Europe/Moscow", sent), [])

    # ---- create_series_from_signal / DB round trip ----

    def _telegram_now(self):
        return datetime.now(timezone.utc)

    def test_create_series_from_signal_persists_the_full_schedule(self):
        signal = {
            "asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
            "entry_time": "09:00", "raw_message": "SIGNAL...",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
            ],
        }
        series_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["entry_times"], ["09:00", "09:05", "09:10", "09:15"])
        self.assertEqual(series["max_entries"], 4)
        self.assertEqual(series["stake"], 10.0)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 0)
        self.assertEqual(len(series["entry_times_utc"]), 4)
        self.assertEqual(series["provider_timezone"], "UTC")
        self.assertEqual(series["published_entry_time"], "09:00")
        self.assertEqual(series["schedule_resolution_method"], engine.SCHEDULE_RESOLUTION_METHOD)

    def test_create_series_from_signal_returns_none_without_entry_time(self):
        signal = {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"}
        series_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        self.assertIsNone(series_id)

    def test_create_series_from_signal_raises_rather_than_guess_with_no_provider_timezone(self):
        signal = {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
                  "entry_time": "09:00", "raw_message": "x"}
        with self.assertRaises(engine.ScheduleResolutionError):
            _run(engine.create_series_from_signal(
                signal, channel_id=163, stake=10.0,
                provider_timezone=None, telegram_message_date_utc=self._telegram_now(),
            ))
        # Nothing partial was left behind - the DB write only happens
        # after the schedule resolves successfully.
        conn = database.get_connection()
        count = conn.execute("SELECT COUNT(*) as n FROM trade_series").fetchone()["n"]
        conn.close()
        self.assertEqual(count, 0)

    def test_duplicate_telegram_message_does_not_create_a_second_series(self):
        signal = {
            "asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
            "entry_time": "09:00", "raw_message": "SIGNAL...",
        }
        first_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=99001,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        second_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=99001,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        self.assertEqual(first_id, second_id)
        conn = database.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) as n FROM trade_series WHERE channel_id = 163 AND source_message_id = 99001",
        ).fetchone()["n"]
        conn.close()
        self.assertEqual(count, 1)

    def test_a_different_message_id_creates_a_genuinely_separate_series(self):
        signal = {
            "asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
            "entry_time": "09:00", "raw_message": "SIGNAL...",
        }
        first_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=1,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        second_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=2,
            provider_timezone="UTC", telegram_message_date_utc=self._telegram_now(),
        ))
        self.assertNotEqual(first_id, second_id)


class DueEntryFiringTests(unittest.TestCase):
    """Covers the user-facing Logic Verification checklist directly:
    initial entry fires at its scheduled time, a loss schedules the next
    entry, a win terminates the series (later entries never fire), four
    losses exhaust it, every entry keeps the same fixed stake, and a
    duplicate poll tick never fires the same entry twice.

    2026-07-27 Martin Trader timezone incident: _fire_due_entries now
    reads each series' own stored, pre-resolved entry_times_utc (real
    UTC datetimes) rather than re-deriving anything from AXIM's local
    clock or created_at - _make_series below writes that column
    directly, computed independently of the (now cosmetic, for these
    tests) entry_times display strings, so these tests keep exercising
    the STATE MACHINE (routing/win/loss/exhaustion/duplicate-guard),
    not schedule conversion, which is covered separately above."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._coordinator = object()  # never actually used - route_signal is mocked

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_series(self, entry_times=("09:00", "09:05", "09:10", "09:15"), entry_times_utc=None,
                      created_offset_minutes=-30):
        created_at = (datetime.now() + timedelta(minutes=created_offset_minutes)).isoformat()
        if entry_times_utc is None:
            # Default: every entry a few minutes in the past relative to
            # real now, so it's already due - matches this suite's own
            # long-standing "is it due yet" testing intent, just anchored
            # on the real, stored UTC schedule now instead of being
            # re-derived from entry_times/created_at at fire time.
            base = datetime.now(timezone.utc) - timedelta(minutes=20)
            entry_times_utc = [base + timedelta(minutes=5 * i) for i in range(len(entry_times))]
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=list(entry_times), max_entries=len(entry_times),
            entry_times_utc=[dt.isoformat() for dt in entry_times_utc],
            provider_timezone="UTC", telegram_message_date_utc=datetime.now(timezone.utc).isoformat(),
            schedule_resolution_method=engine.SCHEDULE_RESOLUTION_METHOD,
        )
        conn = database.get_connection()
        conn.execute("UPDATE trade_series SET created_at = ? WHERE id = ?", (created_at, series_id))
        conn.commit()
        conn.close()
        return series_id

    def _due_entry_times_for_now(self):
        """Four display-only clock-time strings - no longer what
        _fire_due_entries actually schedules against (entry_times_utc,
        written directly by _make_series, is), but kept as realistic
        cosmetic content for the series' own entry_times_json."""
        base = datetime.now() - timedelta(minutes=20)
        return [(base + timedelta(minutes=5 * i)).strftime("%H:%M") for i in range(4)]

    def test_entry_1_fires_when_due_with_the_configured_stake(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 501})

        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        route_mock.assert_awaited_once()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "active")
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(series["stake"], 10.0)  # stake is fixed at series creation, never touched by firing

    def test_a_loss_on_entry_1_schedules_entry_2_not_a_new_series(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        # Simulate entry #1's real outcome: a loss.
        database.record_signal_received(
            {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"},
            series_id=series_id, entry_number=1,
        )
        # The real trade_id created by prepare_trade is what trade.closed
        # carries - simulate it by writing straight to the row this test
        # controls (record_signal_received above stands in for it).
        conn = database.get_connection()
        row = conn.execute(
            "SELECT id FROM signals WHERE series_id = ? AND entry_number = 1", (series_id,),
        ).fetchone()
        conn.close()
        trade_id = row["id"]

        _run(engine._on_trade_closed({"trade_id": trade_id, "result": "loss", "profit_loss": -10.0}))

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")  # waiting for entry #2's own scheduled time
        self.assertEqual(series["current_entry_number"], 1)

        # Entry #2 now fires on the next due-entries tick.
        route_mock2 = AsyncMock(return_value={"status": "clicked", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock2):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock2.assert_awaited_once()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["current_entry_number"], 2)
        self.assertEqual(series["status"], "active")

    def _simulate_full_entry_and_outcome(self, series_id, entry_number, coordinator, result, profit_loss):
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 900 + entry_number})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(coordinator, channel_id=163))
        database.record_signal_received(
            {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"},
            series_id=series_id, entry_number=entry_number,
        )
        conn = database.get_connection()
        row = conn.execute(
            "SELECT id FROM signals WHERE series_id = ? AND entry_number = ? ORDER BY id DESC",
            (series_id, entry_number),
        ).fetchone()
        conn.close()
        _run(engine._on_trade_closed({"trade_id": row["id"], "result": result, "profit_loss": profit_loss}))

    def test_a_win_on_entry_2_terminates_the_series_and_entries_3_4_never_fire(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)

        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "loss", -10.0)
        self._simulate_full_entry_and_outcome(series_id, 2, self._coordinator, "win", 8.5)

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")
        self.assertIsNotNone(series["resolved_at"])

        # Entries #3 and #4 must never fire now - a further due-entries
        # tick must be a complete no-op for this series.
        route_mock3 = AsyncMock(return_value={"status": "clicked", "trade_id": 999})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock3):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock3.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["current_entry_number"], 2)

    def test_four_consecutive_losses_exhaust_the_series(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)

        for entry_number in (1, 2, 3):
            self._simulate_full_entry_and_outcome(series_id, entry_number, self._coordinator, "loss", -10.0)
            series = database.get_trade_series(series_id)
            self.assertEqual(series["status"], "pending")  # more entries remain

        self._simulate_full_entry_and_outcome(series_id, 4, self._coordinator, "loss", -10.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")
        self.assertEqual(series["current_entry_number"], 4)
        self.assertIsNotNone(series["resolved_at"])

        # No 5th entry is ever attempted.
        route_mock5 = AsyncMock(return_value={"status": "clicked", "trade_id": 999})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock5):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock5.assert_not_awaited()

    def test_a_draw_is_treated_as_not_a_win_same_as_a_loss(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times[:2], created_offset_minutes=-30)
        # Only 2 scheduled entries this time, so a draw on the last one exhausts it.
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "draw", 0.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self._simulate_full_entry_and_outcome(series_id, 2, self._coordinator, "draw", 0.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")

    def test_pool_exhaustion_is_transient_and_retries_the_same_entry(self):
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        busy_mock = AsyncMock(return_value={
            "status": "rejected", "rule": "all_workers_busy", "reason": "all workers busy",
        })
        with patch.object(engine.broker_account_manager, "route_signal", busy_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")  # not "active" - never really placed
        self.assertEqual(series["current_entry_number"], 0)  # retries the SAME entry #1 next tick

        clicked_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 700})
        with patch.object(engine.broker_account_manager, "route_signal", clicked_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        clicked_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["current_entry_number"], 1)

    def test_a_policy_rejection_blocks_the_series_instead_of_retrying_forever(self):
        """A rejection reason that will very likely still be true on the
        next tick too (e.g. the consecutive-loss lock) must not be
        silently retried every poll interval forever - the series is
        marked 'blocked' and stops consuming the due-entries loop's
        attention until an operator explicitly resumes it."""
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        blocked_mock = AsyncMock(return_value={
            "status": "rejected", "rule": "max_consecutive_losses", "reason": "consecutive-loss lock engaged",
        })
        with patch.object(engine.broker_account_manager, "route_signal", blocked_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("max_consecutive_losses", series["result"])

        # A blocked series is invisible to the poll loop until resumed.
        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_not_awaited()

        self.assertTrue(database.resume_blocked_trade_series(series_id))
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_awaited_once()

    def test_a_second_concurrent_poll_tick_never_double_fires_the_same_entry(self):
        """The series is marked 'active' BEFORE route_signal is awaited,
        so even if a second _fire_due_entries call somehow interleaved
        with the first (the real poll loop is single-coroutine and can't,
        but this locks in the invariant regardless), it would see
        status='active' and skip - this is the series-level duplicate-
        execution guard."""
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)

        async def slow_route(*args, **kwargs):
            # By the time this "trade" finishes, the series row must
            # already show 'active' - proving the guard was written
            # before route_signal was ever called, not after.
            mid_flight = database.get_trade_series(series_id)
            assert mid_flight["status"] == "active", "series must be marked active before route_signal runs"
            return {"status": "clicked", "trade_id": 1}

        with patch.object(engine.broker_account_manager, "route_signal", AsyncMock(side_effect=slow_route)):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        # A second tick while still 'active' must not fire again.
        second_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 2})
        with patch.object(engine.broker_account_manager, "route_signal", second_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        second_mock.assert_not_awaited()

    def test_a_wildly_overdue_entry_is_rejected_as_stale_not_fired(self):
        """Also covers "result already posted before execution": verified
        real-world evidence (2026-07-27 investigation) shows a Martin
        Trader result is always posted within ~20 minutes of its signal,
        well inside STALE_ENTRY_THRESHOLD_SECONDS (30 min) - so an entry
        this overdue is, in practice, always one the channel has already
        resolved. There is no reliable way to match a free-text RESULT
        message back to a specific series (the incident's own
        investigation found genuinely ambiguous cases), so this
        threshold - not text-matching - is the real safety mechanism."""
        # entry_times_utc anchored ~2 hours in the past - far beyond
        # STALE_ENTRY_THRESHOLD_SECONDS (30 min).
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        entry_times_utc = [base + timedelta(minutes=5 * i) for i in range(4)]
        series_id = self._make_series(entry_times_utc=entry_times_utc)

        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        never_mock.assert_not_awaited()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("stale_entry", series["result"])

    def test_an_entry_a_few_minutes_late_still_fires_normally(self):
        # Well within STALE_ENTRY_THRESHOLD_SECONDS - a normal brief
        # restart/deploy delay must never be mistaken for a real outage.
        base = datetime.now(timezone.utc) - timedelta(minutes=5)
        entry_times_utc = [base + timedelta(minutes=5 * i) for i in range(4)]
        series_id = self._make_series(entry_times_utc=entry_times_utc, created_offset_minutes=-10)

        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        route_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["status"], "active")

    def test_a_series_with_no_resolved_utc_schedule_is_skipped_not_guessed(self):
        # Simulates pre-fix legacy data (entry_times_utc_json never
        # populated) - _fire_due_entries must refuse to guess a schedule
        # for it rather than falling back to any naive interpretation.
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_restart_recovery_uses_the_stored_utc_schedule_not_created_at(self):
        """2026-07-27 Martin Trader timezone incident's own explicit
        requirement: restart recovery must never re-derive a schedule
        from created_at/AXIM's local clock. created_at is deliberately
        set to something that would make the OLD naive resolver treat
        this entry as NOT yet due (created just now, entry_times display
        string far in the future) - only entry_times_utc (anchored in
        the past) determines due-ness now."""
        entry_times_utc = [datetime.now(timezone.utc) - timedelta(minutes=1)]
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["23:59"], max_entries=1,
            entry_times_utc=[entry_times_utc[0].isoformat()],
            provider_timezone="UTC", telegram_message_date_utc=datetime.now(timezone.utc).isoformat(),
        )
        # created_at is "now" - if anything still consulted it the way
        # the old resolver did, "23:59" would resolve to later today,
        # not due yet.
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["status"], "active")

    def test_execution_paused_provider_never_fires_a_pending_entry(self):
        """2026-07-27 Martin Trader timezone incident's safety hold -
        defense in depth in _fire_due_entries itself, alongside the
        listener's own check before create_series_from_signal."""
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        profile_id = database.create_provider_profile(163)
        database.update_provider_profile(profile_id, changed_by="test", reason="test", execution_paused=1)

        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_a_different_channel_is_completely_unaffected_by_another_channels_pause(self):
        """Other providers remain unchanged - pausing channel 163 must
        never affect a due entry on a different channel."""
        entry_times = self._due_entry_times_for_now()
        series_id = self._make_series(entry_times=entry_times)
        conn = database.get_connection()
        conn.execute("UPDATE trade_series SET channel_id = 999 WHERE id = ?", (series_id,))
        conn.commit()
        conn.close()

        profile_id = database.create_provider_profile(163)  # a DIFFERENT channel is paused
        database.update_provider_profile(profile_id, changed_by="test", reason="test", execution_paused=1)

        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=999))
        route_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["status"], "active")


class RestartReconciliationTests(unittest.TestCase):
    """Covers the two ways a series can be left 'active' by a crash: an
    entry that never actually became a real trade (recovery.py's
    mark_abandoned_preparations case - no trade.closed ever comes) must be
    retried, never silently replayed as a duplicate execution; an entry
    that DID resolve (win/loss/draw) before this process could react is
    reconciled using that already-real outcome, not re-executed."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _active_series_with_entry(self, execution_status, result=None, profit_loss=None):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00", "09:05"], max_entries=2,
        )
        database.advance_trade_series(series_id, 1, "active")
        database.record_signal_received(
            {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"},
            series_id=series_id, entry_number=1,
        )
        conn = database.get_connection()
        conn.execute(
            "UPDATE signals SET execution_status = ?, result = ?, profit_loss = ? "
            "WHERE series_id = ? AND entry_number = 1",
            (execution_status, result, profit_loss, series_id),
        )
        conn.commit()
        conn.close()
        return series_id

    def test_an_entry_that_never_executed_is_retried_not_replayed(self):
        series_id = self._active_series_with_entry("error", result="error:abandoned_on_restart")
        _run(engine.reconcile_stuck_series())
        series = database.get_trade_series(series_id)
        # Retried, not counted as a loss and not skipped ahead - same entry #1 comes up again.
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 0)

        # Confirms it's genuinely a fresh attempt, not a replay of the
        # dead one - a brand new signals row is created for entry #1.
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 555})
        entry_times_utc = [datetime.now(timezone.utc) - timedelta(minutes=1), datetime.now(timezone.utc)]
        conn = database.get_connection()
        conn.execute(
            "UPDATE trade_series SET entry_times_utc_json = ? WHERE id = ?",
            (json.dumps([dt.isoformat() for dt in entry_times_utc]), series_id),
        )
        conn.commit()
        conn.close()
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(object(), channel_id=163))
        route_mock.assert_awaited_once()

    def test_an_entry_that_already_resolved_win_is_reconciled_without_re_executing(self):
        series_id = self._active_series_with_entry("result_win", result="win", profit_loss=8.5)
        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 999})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine.reconcile_stuck_series())
        never_mock.assert_not_awaited()  # reconciliation must never itself place a trade
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 8.5)

    def test_an_entry_that_already_resolved_loss_schedules_the_next_one(self):
        series_id = self._active_series_with_entry("result_loss", result="loss", profit_loss=-10.0)
        _run(engine.reconcile_stuck_series())
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 1)  # entry #2 is next, not entry #1 again

    def test_reconciliation_is_a_no_op_for_series_that_are_not_stuck(self):
        pending_id = database.create_trade_series(
            channel_id=163, asset="EUR/USD OTC", direction="BUY", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        _run(engine.reconcile_stuck_series())
        self.assertEqual(database.get_trade_series(pending_id)["status"], "pending")


class SummaryReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_summary_counts_wins_losses_and_net_pl_across_series(self):
        won_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        database.advance_trade_series(won_id, 1, "won", result="win", net_profit_loss=8.5)

        lost_id = database.create_trade_series(
            channel_id=163, asset="EUR/USD OTC", direction="BUY", expiry="5 Minute",
            stake=10.0, entry_times=["10:00"], max_entries=1,
        )
        database.advance_trade_series(lost_id, 1, "lost_exhausted", result="loss", net_profit_loss=-10.0)

        pending_id = database.create_trade_series(
            channel_id=163, asset="GBP/USD OTC", direction="BUY", expiry="5 Minute",
            stake=10.0, entry_times=["11:00"], max_entries=1,
        )

        summary = database.get_trade_series_summary(channel_id=163)
        self.assertEqual(summary["signals_received"], 3)
        self.assertEqual(summary["series_completed"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["win_rate_by_signal"], 50.0)
        self.assertEqual(summary["net_profit_loss"], -1.5)
        self.assertEqual(len(summary["per_signal"]), 3)
        pending_entry = next(s for s in summary["per_signal"] if s["series_id"] == pending_id)
        self.assertEqual(pending_entry["status"], "pending")


class SeriesCancellationTests(unittest.TestCase):
    """Section B of the 2026-07-27 Martin Trader timezone incident: a
    cancelled-for-a-data-error series must never look like a trading
    loss and must never touch risk-counter state."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_cancel_trade_series_without_risk_impact(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00", "09:05", "09:10", "09:15"], max_entries=4,
        )
        database.cancel_trade_series(
            series_id, reason="CANCELLED_TIMEZONE_INTERPRETATION_ERROR: test",
            cancellation_audit={"original_published_schedule": ["09:00"], "deployed_commit": "abc123"},
            changed_by="test",
        )
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "CANCELLED_TIMEZONE_INTERPRETATION_ERROR")
        self.assertIsNotNone(series["cancelled_at"])
        self.assertIsNone(series["result"])  # never a win/loss
        self.assertIsNone(series["net_profit_loss"])
        conn = database.get_connection()
        signals_count = conn.execute(
            "SELECT COUNT(*) as n FROM signals WHERE series_id = ?", (series_id,),
        ).fetchone()["n"]
        conn.close()
        self.assertEqual(signals_count, 0)
        # No longer visible to the due-entries loop.
        self.assertEqual(database.list_pending_trade_series(163), [])

    def test_cancelling_an_already_terminal_series_is_refused(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        database.advance_trade_series(series_id, 1, "won", result="win", net_profit_loss=8.5)
        with self.assertRaises(ValueError):
            database.cancel_trade_series(series_id, reason="test")
        # The real win outcome must survive the refused cancellation attempt.
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")


if __name__ == "__main__":
    unittest.main()
