import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

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

    # ---- _published_entry_times_for_audit (pure parsing, audit-only -
    # 2026-07-27 redesign: this output is never used for scheduling) ----

    def test_published_entry_times_combines_entry_time_and_scheduled_entries(self):
        signal = {
            "entry_time": "09:00",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
            ],
        }
        self.assertEqual(engine._published_entry_times_for_audit(signal), ["09:00", "09:05", "09:10", "09:15"])

    def test_published_entry_times_caps_at_four(self):
        signal = {
            "entry_time": "09:00",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
                {"entry_number": 5, "time": "09:20"},
            ],
        }
        self.assertEqual(len(engine._published_entry_times_for_audit(signal)), 4)

    def test_published_entry_times_returns_none_without_an_entry_time(self):
        self.assertIsNone(engine._published_entry_times_for_audit({"scheduled_entries": []}))

    def test_published_entry_times_handles_a_signal_with_no_re_entries(self):
        self.assertEqual(engine._published_entry_times_for_audit({"entry_time": "09:00"}), ["09:00"])

    # ---- _next_five_minute_boundary_utc (2026-07-27 product decision:
    # the ENTIRE Martin Trader scheduling model - no published clock
    # time, no provider timezone, no Martingale retry times) ----

    def test_a_signal_received_between_boundaries_schedules_the_next_boundary(self):
        cases = [
            ("12:00:01", "12:05:00"),
            ("12:02:30", "12:05:00"),
            ("12:04:59", "12:05:00"),
        ]
        for received, expected in cases:
            with self.subTest(received=received):
                h, m, s = (int(p) for p in received.split(":"))
                eh, em, es = (int(p) for p in expected.split(":"))
                ref = datetime(2026, 7, 27, h, m, s, tzinfo=timezone.utc)
                resolved = engine._next_five_minute_boundary_utc(ref)
                self.assertEqual(resolved, datetime(2026, 7, 27, eh, em, es, tzinfo=timezone.utc))

    def test_a_signal_received_one_second_after_a_boundary_schedules_the_following_boundary(self):
        # Defensive coverage for an ABNORMAL condition, not expected
        # Martin Trader behavior: the product's own stated timing is that
        # a signal always arrives a few minutes BEFORE its intended
        # candle, so a signal arriving this close to (or past) a boundary
        # represents unusual telemetry (delivery delay, clock skew) - not
        # a case the strategy is designed around. Still handled safely
        # (rolls forward, never misfires into the wrong candle) rather
        # than left to raise or guess.
        ref = datetime(2026, 7, 27, 12, 5, 1, tzinfo=timezone.utc)
        resolved = engine._next_five_minute_boundary_utc(ref)
        self.assertEqual(resolved, datetime(2026, 7, 27, 12, 10, 0, tzinfo=timezone.utc))
        self.assertNotEqual(resolved, datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc))

    def test_a_signal_received_exactly_on_a_boundary_is_too_late_for_it_and_schedules_the_next_one(self):
        # Defensive coverage for an ABNORMAL condition, not expected
        # Martin Trader behavior (see this module's own docstring on
        # _next_five_minute_boundary_utc): a signal always arrives before
        # its intended candle under normal operation, so landing exactly
        # on a boundary is treated as unexpected telemetry, not a
        # strategy branch. Explicit product rule (safer for browser
        # execution latency): a signal received exactly AT :05:00 is too
        # late for that same boundary - it schedules :10:00, never
        # :05:00 itself. This must never silently drift from the
        # implementation - if this test and _next_five_minute_boundary_utc
        # ever disagree, that is a real defect, not a test to "fix" by
        # loosening the assertion.
        ref = datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)
        resolved = engine._next_five_minute_boundary_utc(ref)
        self.assertEqual(resolved, datetime(2026, 7, 27, 12, 10, 0, tzinfo=timezone.utc))

    def test_hour_rollover(self):
        ref = datetime(2026, 7, 27, 12, 58, 0, tzinfo=timezone.utc)
        resolved = engine._next_five_minute_boundary_utc(ref)
        self.assertEqual(resolved, datetime(2026, 7, 27, 13, 0, 0, tzinfo=timezone.utc))

    def test_midnight_rollover(self):
        ref = datetime(2026, 7, 27, 23, 58, 0, tzinfo=timezone.utc)
        resolved = engine._next_five_minute_boundary_utc(ref)
        self.assertEqual(resolved, datetime(2026, 7, 28, 0, 0, 0, tzinfo=timezone.utc))

    def test_naive_reference_datetime_is_rejected(self):
        naive = datetime(2026, 7, 27, 12, 0, 0)  # no tzinfo
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._next_five_minute_boundary_utc(naive)

    def test_none_reference_datetime_is_rejected(self):
        with self.assertRaises(engine.ScheduleResolutionError):
            engine._next_five_minute_boundary_utc(None)

    # ---- create_series_from_signal / DB round trip ----

    def _telegram_now(self):
        return datetime.now(timezone.utc)

    def test_create_series_from_signal_schedules_entry_1_at_the_next_five_minute_boundary(self):
        signal = {
            "asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
            "entry_time": "09:00", "raw_message": "SIGNAL...",
            "scheduled_entries": [
                {"entry_number": 2, "time": "09:05"},
                {"entry_number": 3, "time": "09:10"},
                {"entry_number": 4, "time": "09:15"},
            ],
        }
        received_at = datetime(2026, 7, 27, 18, 45, 27, tzinfo=timezone.utc)
        series_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0,
            telegram_message_date_utc=received_at, signal_received_at_utc=received_at,
        ))
        series = database.get_trade_series(series_id)
        # Published times preserved for audit only - never for scheduling.
        self.assertEqual(series["entry_times"], ["09:00", "09:05", "09:10", "09:15"])
        self.assertEqual(series["published_entry_time"], "09:00")
        self.assertEqual(series["max_entries"], 4, "always 4 total entries - an AXIM-side rule, not derived")
        self.assertEqual(series["stake"], 10.0)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 0)
        self.assertEqual(series["schedule_resolution_method"], "next_five_minute_boundary_v1")
        # Only Entry 1 is computed at creation time - the real, dynamic schedule.
        self.assertEqual(len(series["entry_times_utc"]), 1)
        self.assertEqual(
            datetime.fromisoformat(series["entry_times_utc"][0]),
            datetime(2026, 7, 27, 18, 50, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(series["telegram_message_date_utc"], received_at.isoformat())

    def test_create_series_from_signal_returns_none_without_entry_time(self):
        signal = {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"}
        series_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, telegram_message_date_utc=self._telegram_now(),
        ))
        self.assertIsNone(series_id)

    def test_create_series_from_signal_defaults_signal_received_at_to_right_now(self):
        signal = {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
                  "entry_time": "09:00", "raw_message": "x"}
        before = datetime.now(timezone.utc)
        series_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, telegram_message_date_utc=self._telegram_now(),
        ))
        after = datetime.now(timezone.utc)
        series = database.get_trade_series(series_id)
        entry_1_utc = datetime.fromisoformat(series["entry_times_utc"][0])
        self.assertGreater(entry_1_utc, before)
        self.assertLessEqual(entry_1_utc, after + timedelta(minutes=5))

    def test_duplicate_telegram_message_does_not_create_a_second_series(self):
        signal = {
            "asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute",
            "entry_time": "09:00", "raw_message": "SIGNAL...",
        }
        first_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=99001,
            telegram_message_date_utc=self._telegram_now(),
        ))
        second_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=99001,
            telegram_message_date_utc=self._telegram_now(),
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
            telegram_message_date_utc=self._telegram_now(),
        ))
        second_id = _run(engine.create_series_from_signal(
            signal, channel_id=163, stake=10.0, source_message_id=2,
            telegram_message_date_utc=self._telegram_now(),
        ))
        self.assertNotEqual(first_id, second_id)


class DueEntryFiringTests(unittest.TestCase):
    """Covers the user-facing Logic Verification checklist directly:
    initial entry fires at its scheduled time, a loss schedules the next
    entry (at the next five-minute boundary, computed fresh, never
    precomputed), a win terminates the series (later entries never
    fire), four losses exhaust it, every entry keeps the same fixed
    stake, and a duplicate poll tick never fires the same entry twice.

    2026-07-27 redesign: every entry after #1 is scheduled the moment
    the PREVIOUS one's loss becomes known, via _apply_entry_outcome's
    call to _now_utc() - these tests patch that one seam to a fixed
    point safely in the past (so every computed boundary is already due
    by the time _fire_due_entries checks against the REAL clock),
    rather than mocking datetime itself."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._coordinator = object()  # never actually used - route_signal is mocked
        self._now_patch = patch.object(engine, "_now_utc", return_value=datetime.now(timezone.utc) - timedelta(minutes=20))
        self._now_patch.start()

    def tearDown(self):
        self._now_patch.stop()
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_series(self, entry_1_utc=None, max_entries=4):
        if entry_1_utc is None:
            entry_1_utc = engine._next_five_minute_boundary_utc(engine._now_utc())
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=max_entries,
            entry_times_utc=[entry_1_utc.isoformat()],
            telegram_message_date_utc=datetime.now(timezone.utc).isoformat(),
            schedule_resolution_method=engine.SCHEDULE_RESOLUTION_METHOD,
        )
        return series_id

    def test_entry_1_fires_when_due_with_the_configured_stake(self):
        series_id = self._make_series()
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 501})

        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        route_mock.assert_awaited_once()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "active")
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(series["stake"], 10.0)  # stake is fixed at series creation, never touched by firing

    def test_a_loss_on_entry_1_schedules_entry_2_at_the_next_five_minute_boundary(self):
        series_id = self._make_series()
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        database.record_signal_received(
            {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"},
            series_id=series_id, entry_number=1,
        )
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
        self.assertEqual(len(series["entry_times_utc"]), 2, "entry 2's time is computed only now, not upfront")
        expected_entry_2 = engine._next_five_minute_boundary_utc(engine._now_utc())
        self.assertEqual(datetime.fromisoformat(series["entry_times_utc"][1]), expected_entry_2)

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

    def test_a_win_stops_the_series_immediately(self):
        series_id = self._make_series()
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "win", 8.5)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")
        self.assertIsNotNone(series["resolved_at"])

    def test_a_win_on_entry_2_terminates_the_series_and_entries_3_4_never_fire(self):
        series_id = self._make_series()
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "loss", -10.0)
        self._simulate_full_entry_and_outcome(series_id, 2, self._coordinator, "win", 8.5)

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")
        self.assertIsNotNone(series["resolved_at"])

        route_mock3 = AsyncMock(return_value={"status": "clicked", "trade_id": 999})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock3):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock3.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["current_entry_number"], 2)

    def test_a_loss_advances_to_the_next_entry(self):
        series_id = self._make_series()
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "loss", -10.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 1)

    def test_four_losses_exhaust_the_series(self):
        series_id = self._make_series()
        for entry_number in (1, 2, 3):
            self._simulate_full_entry_and_outcome(series_id, entry_number, self._coordinator, "loss", -10.0)
            series = database.get_trade_series(series_id)
            self.assertEqual(series["status"], "pending")  # more entries remain

        self._simulate_full_entry_and_outcome(series_id, 4, self._coordinator, "loss", -10.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")
        self.assertEqual(series["current_entry_number"], 4)
        self.assertIsNotNone(series["resolved_at"])

    def test_no_fifth_entry_can_execute(self):
        series_id = self._make_series()
        for entry_number in (1, 2, 3, 4):
            self._simulate_full_entry_and_outcome(series_id, entry_number, self._coordinator, "loss", -10.0)
        self.assertEqual(database.get_trade_series(series_id)["status"], "lost_exhausted")

        route_mock5 = AsyncMock(return_value={"status": "clicked", "trade_id": 999})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock5):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock5.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["current_entry_number"], 4)

    def test_a_pending_result_does_not_advance_the_series(self):
        series_id = self._make_series()
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        database.record_signal_received(
            {"asset": "AUD/JPY OTC", "direction": "SELL", "expiry": "5 Minute", "raw_message": "x"},
            series_id=series_id, entry_number=1,
        )
        conn = database.get_connection()
        row = conn.execute("SELECT id FROM signals WHERE series_id = ? AND entry_number = 1", (series_id,)).fetchone()
        conn.close()

        for bogus_result in ("pending", "unknown", None):
            _run(engine._on_trade_closed({"trade_id": row["id"], "result": bogus_result, "profit_loss": 0.0}))
            series = database.get_trade_series(series_id)
            self.assertEqual(series["status"], "active", f"result={bogus_result!r} must not advance the series")
            self.assertEqual(series["current_entry_number"], 1)

    def test_a_draw_is_not_automatically_treated_as_a_win_but_advances_like_a_loss(self):
        series_id = self._make_series(max_entries=2)
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "draw", 0.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertNotEqual(series["status"], "won", "a draw must never be mistaken for a win")
        self._simulate_full_entry_and_outcome(series_id, 2, self._coordinator, "draw", 0.0)
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")

    def test_pool_exhaustion_is_transient_and_retries_the_same_entry(self):
        series_id = self._make_series()
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
        series_id = self._make_series()
        blocked_mock = AsyncMock(return_value={
            "status": "rejected", "rule": "max_consecutive_losses", "reason": "consecutive-loss lock engaged",
        })
        with patch.object(engine.broker_account_manager, "route_signal", blocked_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("max_consecutive_losses", series["result"])

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
        series_id = self._make_series()

        async def slow_route(*args, **kwargs):
            mid_flight = database.get_trade_series(series_id)
            assert mid_flight["status"] == "active", "series must be marked active before route_signal runs"
            return {"status": "clicked", "trade_id": 1}

        with patch.object(engine.broker_account_manager, "route_signal", AsyncMock(side_effect=slow_route)):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        second_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 2})
        with patch.object(engine.broker_account_manager, "route_signal", second_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        second_mock.assert_not_awaited()

    def test_a_wildly_overdue_entry_is_rejected_as_stale_not_fired(self):
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        series_id = self._make_series(entry_1_utc=stale_time)

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
        late_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        series_id = self._make_series(entry_1_utc=late_time)

        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))

        route_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["status"], "active")

    def test_a_series_with_no_resolved_utc_schedule_is_skipped_not_guessed(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_restart_recovery_preserves_the_calculated_five_minute_schedule(self):
        """2026-07-27 explicit product requirement: "Restart recovery
        preserves the calculated five-minute schedule." Once entry 2's
        time is computed and persisted (via schedule_next_entry), a
        fresh read of the series must show the exact same stored value -
        never recomputed."""
        series_id = self._make_series()
        self._simulate_full_entry_and_outcome(series_id, 1, self._coordinator, "loss", -10.0)
        before_restart = database.get_trade_series(series_id)["entry_times_utc"]

        # Simulate a restart: nothing in-memory carries over, only a
        # fresh read from the database.
        after_restart = database.get_trade_series(series_id)["entry_times_utc"]
        self.assertEqual(before_restart, after_restart)

        # And it still fires at exactly that stored time, not a
        # recomputed one.
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        route_mock.assert_awaited_once()
        self.assertEqual(database.get_trade_series(series_id)["entry_times_utc"], after_restart)

    def test_execution_paused_provider_never_fires_a_pending_entry(self):
        series_id = self._make_series()
        profile_id = database.create_provider_profile(163)
        database.update_provider_profile(profile_id, changed_by="test", reason="test", execution_paused=1)

        never_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        with patch.object(engine.broker_account_manager, "route_signal", never_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        never_mock.assert_not_awaited()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_other_providers_remain_unchanged_by_a_different_channels_pause(self):
        series_id = self._make_series()
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
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 0)

        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 555})
        conn = database.get_connection()
        conn.execute(
            "UPDATE trade_series SET entry_times_utc_json = ? WHERE id = ?",
            (json.dumps([(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()]), series_id),
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
        never_mock.assert_not_awaited()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 8.5)

    def test_an_entry_that_already_resolved_loss_schedules_the_next_one(self):
        series_id = self._active_series_with_entry("result_loss", result="loss", profit_loss=-10.0)
        with patch.object(engine, "_now_utc", return_value=datetime.now(timezone.utc)):
            _run(engine.reconcile_stuck_series())
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 1)

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
    """A cancelled-for-a-data-error series must never look like a
    trading loss and must never touch risk-counter state."""

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
        self.assertIsNone(series["result"])
        self.assertIsNone(series["net_profit_loss"])
        conn = database.get_connection()
        signals_count = conn.execute(
            "SELECT COUNT(*) as n FROM signals WHERE series_id = ?", (series_id,),
        ).fetchone()["n"]
        conn.close()
        self.assertEqual(signals_count, 0)
        self.assertEqual(database.list_pending_trade_series(163), [])

    def test_cancelling_an_already_terminal_series_is_refused(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        database.advance_trade_series(series_id, 1, "won", result="win", net_profit_loss=8.5)
        with self.assertRaises(ValueError):
            database.cancel_trade_series(series_id, reason="test")
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")


if __name__ == "__main__":
    unittest.main()
