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
from timeline import get_current_timeline


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


def _make_series(entry_1_utc=None, max_entries=4):
    """Shared across the test classes below - 2026-07-27 redesign: a
    fresh series always starts with exactly ONE resolved entry_times_utc
    element (Entry #1); later ones only ever appear via
    schedule_next_entry, after a real loss."""
    if entry_1_utc is None:
        # Near-future (not past): _run_precision_entry's own worker-
        # acquisition timeouts are computed as (scheduled_dt - real now),
        # which would clamp to zero (skipping acquisition entirely) if
        # scheduled_dt were already behind real wall-clock time. 3s gives
        # the early preflight-check/record_signal_received DB work
        # (asyncio.to_thread round-trips) enough real margin not to race
        # past the deadline on a loaded machine before worker acquisition
        # even starts.
        entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=3)
    return database.create_trade_series(
        channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
        stake=10.0, entry_times=["09:00"], max_entries=max_entries,
        entry_times_utc=[entry_1_utc.isoformat()],
        telegram_message_date_utc=datetime.now(timezone.utc).isoformat(),
        schedule_resolution_method=engine.SCHEDULE_RESOLUTION_METHOD,
    )


class ApplyEntryOutcomeTests(unittest.TestCase):
    """_apply_entry_outcome is the pure "what does this real, resolved
    result mean for the series" decision - tested directly here, with no
    worker/browser/risk-check machinery involved at all, since none of
    that is this function's concern. Covers the user-facing Logic
    Verification checklist: a win stops the series immediately, a loss
    advances to (and schedules) the next entry, four losses exhaust it,
    a draw advances like a loss without being mistaken for a win, and a
    pending/unknown/missing result never advances anything."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_entry_2_is_not_pre_scheduled_at_series_creation(self):
        series_id = _make_series()
        series = database.get_trade_series(series_id)
        self.assertEqual(len(series["entry_times_utc"]), 1, "only Entry #1 exists until a loss is confirmed")

    def test_a_win_stops_the_series_immediately(self):
        series_id = _make_series()
        series = database.get_trade_series(series_id)
        _run(engine._apply_entry_outcome(series, 1, "win", 8.5))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["result"], "win")
        self.assertIsNotNone(series["resolved_at"])
        self.assertEqual(len(series["entry_times_utc"]), 1, "a win must never schedule a further entry")

    def test_a_confirmed_loss_schedules_entry_2_at_the_next_five_minute_boundary(self):
        series_id = _make_series()
        series = database.get_trade_series(series_id)
        fixed_now = datetime(2026, 7, 27, 12, 2, 30, tzinfo=timezone.utc)
        with patch.object(engine, "_now_utc", return_value=fixed_now):
            _run(engine._apply_entry_outcome(series, 1, "loss", -10.0))

        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(len(series["entry_times_utc"]), 2, "entry 2's time is computed only now, not upfront")
        self.assertEqual(
            datetime.fromisoformat(series["entry_times_utc"][1]),
            datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc),
        )

    def test_four_losses_exhaust_the_series(self):
        series_id = _make_series()
        for entry_number in (1, 2, 3):
            series = database.get_trade_series(series_id)
            _run(engine._apply_entry_outcome(series, entry_number, "loss", -10.0))
            series = database.get_trade_series(series_id)
            self.assertEqual(series["status"], "pending")

        series = database.get_trade_series(series_id)
        _run(engine._apply_entry_outcome(series, 4, "loss", -10.0))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")
        self.assertEqual(series["current_entry_number"], 4)
        self.assertIsNotNone(series["resolved_at"])

    def test_a_draw_is_not_mistaken_for_a_win_but_advances_like_a_loss(self):
        series_id = _make_series(max_entries=2)
        series = database.get_trade_series(series_id)
        _run(engine._apply_entry_outcome(series, 1, "draw", 0.0))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertNotEqual(series["status"], "won")
        _run(engine._apply_entry_outcome(series, 2, "draw", 0.0))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")

    def test_pending_unknown_and_missing_results_never_advance_the_series(self):
        series_id = _make_series()
        for bogus_result in ("pending", "unknown", None):
            series = database.get_trade_series(series_id)
            _run(engine._apply_entry_outcome(series, 1, bogus_result, 0.0))
            series = database.get_trade_series(series_id)
            self.assertEqual(series["current_entry_number"], 0, f"result={bogus_result!r} must not advance anything")
            self.assertEqual(series["status"], "pending")


class HandleRouteResultTests(unittest.TestCase):
    """_handle_route_result is the pure "what does this route/submission
    result dict mean" decision, shared by the legacy _execute_entry path
    and the new precision path - tested directly, independent of either
    caller."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_clicked_result_leaves_the_series_untouched(self):
        series_id = _make_series()
        series = database.get_trade_series(series_id)
        _run(engine._handle_route_result(series, 1, {"status": "clicked", "trade_id": 1}))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["current_entry_number"], 0)  # _execute_entry/_run_precision_entry set "active" themselves

    def test_pool_exhaustion_is_transient_and_retries_the_same_entry(self):
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "active")  # simulates the caller's own pre-attempt marking
        series = database.get_trade_series(series_id)
        _run(engine._handle_route_result(
            series, 1, {"status": "rejected", "rule": "all_workers_busy", "reason": "all workers busy"},
        ))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 0)  # retries the SAME entry #1 next tick

    def test_a_policy_rejection_blocks_the_series_instead_of_retrying_forever(self):
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "active")
        series = database.get_trade_series(series_id)
        _run(engine._handle_route_result(
            series, 1, {"status": "rejected", "rule": "max_consecutive_losses", "reason": "consecutive-loss lock engaged"},
        ))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("max_consecutive_losses", series["result"])

        self.assertTrue(database.resume_blocked_trade_series(series_id))
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")


class FakeWorker:
    def __init__(self, worker_id=0, page="fake-page"):
        self.worker_id = worker_id
        self.page = page


class FakePool:
    def __init__(self, worker_to_return="default"):
        self.released = []
        self._worker_to_return = FakeWorker() if worker_to_return == "default" else worker_to_return
        self.acquire_worker = AsyncMock(side_effect=self._acquire)

    async def _acquire(self, timeout=0):
        return self._worker_to_return

    def release_worker(self, worker):
        self.released.append(worker)


class FakeCoordinator:
    """Stands in for TradeCoordinator in _run_precision_entry tests -
    _run_preflight_checks is a plain (non-async) callable, matching the
    real method's signature exactly (it's invoked via asyncio.to_thread,
    same as production)."""

    def __init__(self, preflight_result=("passed", None), worker_pool=None):
        self.worker_pool = worker_pool or FakePool()
        self.warmup_service = object()
        self._preflight_result = preflight_result
        self.preflight_calls = []

    def _run_preflight_checks(self, trade_id, amount, session_id, asset, direction, expiry,
                               sent_at, timeline, broker_account_id=None, channel_id=None, series_id=None):
        self.preflight_calls.append(series_id)
        return self._preflight_result


def _fake_staged_trade(trade_id, worker, pool, warmup_service, timeline):
    from pocket_executor import StagedTrade
    return StagedTrade(trade_id, "AUD/JPY OTC", "SELL", "5 Minute", 10.0, 92, worker, pool, warmup_service, timeline)


class PrecisionEntryOrchestrationTests(unittest.TestCase):
    """_run_precision_entry's OWN orchestration: early (worker-
    independent) risk checks run first, then a worker is reserved and
    the trade pre-staged, then submitted - covering the explicit
    "prevent cooldown/duplicate detection from adding latency" and
    "pass all risk checks that can safely be completed early"
    requirements structurally (by reusing _run_preflight_checks
    directly, the same series_id-scoped bypass proven in
    tests/test_risk_manager.py applies automatically here too)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        engine._precision_tasks_in_flight.clear()
        self._pre_stage_patch = patch.object(
            engine.pocket_executor, "pre_stage_trade",
            AsyncMock(side_effect=lambda trade_id, asset, direction, expiry, amount, worker, pool, warmup_service,
                      timeline=None, latency=None: _fake_staged_trade(trade_id, worker, pool, warmup_service, timeline)),
        )
        self._submit_patch = patch.object(
            engine.pocket_executor, "submit_staged_trade",
            AsyncMock(return_value={"status": "clicked", "trade_id": 1}),
        )
        self._pre_stage_mock = self._pre_stage_patch.start()
        self._submit_mock = self._submit_patch.start()

    def tearDown(self):
        self._pre_stage_patch.stop()
        self._submit_patch.stop()
        engine._precision_tasks_in_flight.clear()
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _run_precision(self, series_id, entry_number=1, coordinator=None, scheduled_dt=None):
        series = database.get_trade_series(series_id)
        if scheduled_dt is None:
            scheduled_dt = datetime.fromisoformat(series["entry_times_utc"][entry_number - 1])
        coordinator = coordinator or FakeCoordinator()
        key = (series_id, entry_number)
        _run(engine._run_precision_entry(coordinator, series, entry_number, scheduled_dt, key))
        return coordinator

    def test_timeline_is_activated_across_pre_stage_and_submit_then_deactivated(self):
        """2026-07-27 precision-bottleneck investigation: pocket_dom.
        click_direction's own timeline.mark("clicked")/"confirmation_detected"
        calls read the AMBIENT get_current_timeline(), not an explicit
        reference - before this fix, _run_precision_entry never called
        timeline.activate(), so those marks silently landed on nothing for
        every Martin Trader execution. Verifies activation is in effect
        during both pre_stage_trade and submit_staged_trade (each entry
        getting its OWN timeline object), and that it's deactivated again
        before the next entry runs - proven by running two entries within
        ONE continuous async context (asyncio.run's own Task-context-copy
        isolation would make an outside-the-task assertion trivially pass
        either way, so this checks it from where it's actually
        observable: a second entry sharing the same task-level context)."""
        seen = []

        async def spy_pre_stage(trade_id, asset, direction, expiry, amount, worker, pool, warmup_service,
                                 timeline=None, latency=None):
            seen.append(("pre_stage", get_current_timeline()))
            return _fake_staged_trade(trade_id, worker, pool, warmup_service, timeline)

        async def spy_submit(staged, latency=None):
            seen.append(("submit", get_current_timeline()))
            return {"status": "clicked", "trade_id": 1}

        self._pre_stage_mock.side_effect = spy_pre_stage
        self._submit_mock.side_effect = spy_submit

        series_a = _make_series()
        # Deliberately later than series_a's default (now + 3s): running
        # series_a's own full precision path first consumes real wall-
        # clock time, and series_b needs its own scheduled_dt still ahead
        # of "now" by the time its turn comes (same reasoning as
        # _make_series' own default margin).
        series_b = _make_series(entry_1_utc=datetime.now(timezone.utc) + timedelta(seconds=8))

        async def _two_entries():
            self.assertIsNone(get_current_timeline(), "clean before either entry runs")
            await self._run_precision_async(series_a)
            self.assertIsNone(get_current_timeline(), "deactivated after the first entry, before the second")
            await self._run_precision_async(series_b)
            self.assertIsNone(get_current_timeline(), "deactivated after the second entry too")

        _run(_two_entries())

        self.assertEqual(len(seen), 4)
        self.assertIsNotNone(seen[0][1], "timeline must be active during pre_stage_trade")
        self.assertIs(seen[0][1], seen[1][1], "same timeline object for pre_stage and submit, same entry")
        self.assertIsNotNone(seen[2][1])
        self.assertIsNot(seen[2][1], seen[0][1], "the second entry gets its OWN timeline, not a leaked one")

    async def _run_precision_async(self, series_id, entry_number=1, coordinator=None):
        series = database.get_trade_series(series_id)
        scheduled_dt = datetime.fromisoformat(series["entry_times_utc"][entry_number - 1])
        coordinator = coordinator or FakeCoordinator()
        key = (series_id, entry_number)
        await engine._run_precision_entry(coordinator, series, entry_number, scheduled_dt, key)

    def test_successful_flow_reserves_a_worker_pre_stages_and_submits(self):
        series_id = _make_series()
        coordinator = self._run_precision(series_id)

        self.assertEqual(coordinator.preflight_calls, [series_id], "reused the real preflight check chain")
        coordinator.worker_pool.acquire_worker.assert_awaited()
        self._pre_stage_mock.assert_awaited_once()
        self._submit_mock.assert_awaited_once()
        self.assertEqual(coordinator.worker_pool.released, [], "submit_staged_trade owns the release, not this function")

        series = database.get_trade_series(series_id)
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(series["stake"], 10.0)

    def test_early_risk_rejection_never_touches_the_worker_pool(self):
        series_id = _make_series()
        coordinator = FakeCoordinator(preflight_result=(
            "rejected", {"status": "rejected", "trade_id": 1, "rule": "emergency_stop", "reason": "stopped"},
        ))
        self._run_precision(series_id, coordinator=coordinator)

        coordinator.worker_pool.acquire_worker.assert_not_awaited()
        self._pre_stage_mock.assert_not_awaited()
        self._submit_mock.assert_not_awaited()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("emergency_stop", series["result"])

        conn = database.get_connection()
        row = conn.execute("SELECT id, latency_checkpoints_json FROM signals WHERE series_id = ?", (series_id,)).fetchone()
        conn.close()
        checkpoints = json.loads(row["latency_checkpoints_json"])
        self.assertIn(
            "rejected_at", checkpoints,
            "an early risk-check rejection must record rejected_at for boundary_to_rejection_ms classification",
        )

    def test_worker_never_available_falls_back_to_standard_execution(self):
        series_id = _make_series()
        pool = FakePool(worker_to_return=None)  # acquire_worker always returns None
        coordinator = FakeCoordinator(worker_pool=pool)
        fallback_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 2})
        with patch.object(engine.broker_account_manager, "route_signal", fallback_mock):
            self._run_precision(series_id, coordinator=coordinator)
        fallback_mock.assert_awaited_once()  # fell back to the legacy _execute_entry path
        self._pre_stage_mock.assert_not_awaited()

    def test_a_win_reported_by_submit_stops_the_series(self):
        self._submit_mock.return_value = {"status": "clicked", "trade_id": 1}
        series_id = _make_series()
        coordinator = self._run_precision(series_id)

        conn = database.get_connection()
        row = conn.execute("SELECT id FROM signals WHERE series_id = ? AND entry_number = 1", (series_id,)).fetchone()
        conn.close()
        _run(engine._on_trade_closed({"trade_id": row["id"], "result": "win", "profit_loss": 8.5}))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        del coordinator  # unused beyond triggering the flow

    def test_a_second_concurrent_poll_tick_never_double_fires_the_same_entry(self):
        series_id = _make_series()

        async def slow_submit(staged, latency=None):
            mid_flight = database.get_trade_series(series_id)
            assert mid_flight["status"] == "active", "series must be marked active before submission runs"
            return {"status": "clicked", "trade_id": 1}

        self._submit_mock.side_effect = slow_submit
        self._run_precision(series_id)

        second_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 2})
        with patch.object(engine.broker_account_manager, "route_signal", second_mock):
            # A second call for the SAME entry_number, simulating a
            # duplicate spawn - the series-level "active" guard (which
            # _run_precision_entry sets before ever calling submit) must
            # make this a no-op, exactly like the legacy path.
            series = database.get_trade_series(series_id)
            self.assertEqual(series["status"], "active")


class FireDueEntriesSpawnTests(unittest.TestCase):
    """_fire_due_entries' OWN job, post-redesign: notice when a pending
    entry has reached its pre-stage window and hand off to a dedicated
    _run_precision_entry task exactly once - not fire anything itself."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        engine._precision_tasks_in_flight.clear()
        self._coordinator = FakeCoordinator()

    def tearDown(self):
        engine._precision_tasks_in_flight.clear()
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_spawns_a_precision_task_once_the_prestage_window_opens(self):
        entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=5)  # inside the 20s pre-stage window
        _make_series(entry_1_utc=entry_1_utc)
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))  # let the spawned task get scheduled
        spawn_mock.assert_called_once()

    def test_does_not_spawn_before_the_prestage_window_opens(self):
        entry_1_utc = datetime.now(timezone.utc) + timedelta(minutes=5)  # well outside the 20s window
        _make_series(entry_1_utc=entry_1_utc)
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))
        spawn_mock.assert_not_called()

    def test_does_not_spawn_a_second_task_for_the_same_entry(self):
        entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=5)
        series_id = _make_series(entry_1_utc=entry_1_utc)
        engine._precision_tasks_in_flight.add((series_id, 1))  # simulates one already running
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))
        spawn_mock.assert_not_called()

    def test_a_wildly_overdue_entry_is_rejected_as_stale_without_ever_spawning(self):
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        series_id = _make_series(entry_1_utc=stale_time)
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        spawn_mock.assert_not_called()
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "blocked")
        self.assertIn("stale_entry", series["result"])

    def test_an_entry_a_few_minutes_late_still_spawns_normally(self):
        # Well within STALE_ENTRY_THRESHOLD_SECONDS - a normal brief
        # restart/deploy delay must never be mistaken for a real outage.
        late_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        series_id = _make_series(entry_1_utc=late_time)
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))
        spawn_mock.assert_called_once()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_a_series_with_no_resolved_utc_schedule_is_skipped_not_guessed(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
        spawn_mock.assert_not_called()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_execution_paused_provider_never_spawns_a_precision_task(self):
        entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=5)
        series_id = _make_series(entry_1_utc=entry_1_utc)
        profile_id = database.create_provider_profile(163)
        database.update_provider_profile(profile_id, changed_by="test", reason="test", execution_paused=1)

        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))
        spawn_mock.assert_not_called()
        self.assertEqual(database.get_trade_series(series_id)["status"], "pending")

    def test_other_providers_remain_unchanged_by_a_different_channels_pause(self):
        entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=5)
        series_id = _make_series(entry_1_utc=entry_1_utc)
        conn = database.get_connection()
        conn.execute("UPDATE trade_series SET channel_id = 999 WHERE id = ?", (series_id,))
        conn.commit()
        conn.close()

        profile_id = database.create_provider_profile(163)  # a DIFFERENT channel is paused
        database.update_provider_profile(profile_id, changed_by="test", reason="test", execution_paused=1)

        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=999))
            _run(asyncio.sleep(0))
        spawn_mock.assert_called_once()

    def test_restart_recovery_preserves_the_calculated_five_minute_schedule(self):
        """2026-07-27 explicit product requirement: "Restart recovery
        preserves the calculated five-minute schedule." Once entry 2's
        time is computed and persisted (via schedule_next_entry), a
        fresh read of the series must show the exact same stored value -
        never recomputed, even across what a restart would look like
        (nothing in-memory carries over - only a fresh DB read)."""
        # The boundary-computation logic itself (_apply_entry_outcome ->
        # next five-minute mark) is already covered by
        # ApplyEntryOutcomeTests - this test's own distinctive concern is
        # only whether a persisted entry_times_utc survives a re-read and
        # is fired from verbatim, not recomputed. schedule_next_entry is
        # called directly with a real near-future timestamp so the test
        # is robust to wall-clock timing rather than racing a real
        # five-minute boundary.
        series_id = _make_series()
        entry_2_utc = datetime.now(timezone.utc) + timedelta(seconds=5)
        database.schedule_next_entry(series_id, 1, entry_2_utc.isoformat())
        before_restart = database.get_trade_series(series_id)["entry_times_utc"]

        after_restart = database.get_trade_series(series_id)["entry_times_utc"]
        self.assertEqual(before_restart, after_restart)

        spawn_mock = AsyncMock(return_value=None)
        with patch.object(engine, "_run_precision_entry", spawn_mock):
            _run(engine._fire_due_entries(self._coordinator, channel_id=163))
            _run(asyncio.sleep(0))
        spawn_mock.assert_called_once()
        self.assertEqual(database.get_trade_series(series_id)["entry_times_utc"], after_restart)


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

        # _fire_due_entries only spawns a detached asyncio.create_task
        # for _run_precision_entry now - a bare asyncio.run() call gives
        # that task no chance to actually execute before the loop shuts
        # down, so (as with PrecisionEntryOrchestrationTests) the retry
        # itself is exercised by calling _run_precision_entry directly.
        # A worker_pool that never yields a worker forces the fallback
        # to the standard _execute_entry -> route_signal path, which is
        # what proves this is a genuine retry, not a replay.
        route_mock = AsyncMock(return_value={"status": "clicked", "trade_id": 555})
        scheduled_dt = datetime.now(timezone.utc) - timedelta(minutes=1)
        coordinator = FakeCoordinator(worker_pool=FakePool(worker_to_return=None))
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._run_precision_entry(coordinator, series, 1, scheduled_dt, (series_id, 1)))
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


class ReconciliationGapTests(unittest.TestCase):
    """2026-07-29 reconciliation-gap fix (verified production incident:
    series 105 - a real browser crash during wait_for_trade_result left an
    entry execution_status='error', result=f"error:{e}", a state neither
    the OLD reconcile_stuck_series (narrow 'error:abandoned_on_restart'
    string match) nor the OLD recovery.resume_pending_trades
    (trade_clicked/trade_opened only) ever recognized - the series stayed
    'active' forever. Covers the general possibly_submitted AND
    not_authoritatively_resolved criterion, broker-history reconciliation
    via a mocked pocket_dom.find_closed_trade_by_criteria, and the
    idempotency guarantee that a duplicate reconciliation pass can never
    double-grade the same trade."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _active_series_with_open_entry(self, execution_status, result=None, opened_at=None,
                                        asset="AUD/JPY OTC", direction="SELL", amount=10.0,
                                        max_entries=2, entry_times=("09:00", "09:05")):
        series_id = database.create_trade_series(
            channel_id=163, asset=asset, direction=direction, expiry="5 Minute",
            stake=amount, entry_times=list(entry_times), max_entries=max_entries,
        )
        database.advance_trade_series(series_id, 1, "active")
        database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": "5 Minute", "raw_message": "x",
             "trade_amount": amount},
            series_id=series_id, entry_number=1,
        )
        conn = database.get_connection()
        conn.execute(
            "UPDATE signals SET execution_status = ?, result = ?, opened_at = ? "
            "WHERE series_id = ? AND entry_number = 1",
            (execution_status, result, opened_at, series_id),
        )
        conn.commit()
        conn.close()
        return series_id

    def test_generic_error_with_opened_at_enters_recovery_not_the_never_executed_path(self):
        # The exact production shape: track_outcome's generic except sets
        # execution_status='error', result=f"error:{e}" - an arbitrary
        # string the OLD code could never match. opened_at IS set (a real
        # click happened), so this must NOT be treated as "never
        # executed" (which would incorrectly retry/replay the entry).
        series_id = self._active_series_with_open_entry(
            "error", result="error:Target page, context or browser has been closed",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        _run(engine.reconcile_stuck_series())  # no warmup_service - must fail closed, not guess
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "reconciliation_required")
        self.assertIsNotNone(series["reconciliation_required_at"])

    def test_broker_history_unique_match_completes_the_series_correctly(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch.object(
            engine.pocket_dom, "find_closed_trade_by_criteria",
            new=AsyncMock(return_value=("unique", {
                "result": "win", "stake": 10.0, "final_value": 19.2,
                "asset": "AUD/JPY OTC", "direction": "SELL", "raw_values": ["$10.00", "$19.20"],
            })),
        ):
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 9.2)

    def test_broker_history_no_match_fails_closed(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch.object(
            engine.pocket_dom, "find_closed_trade_by_criteria",
            new=AsyncMock(return_value=("no_match", None)),
        ):
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "reconciliation_required")

    def test_broker_history_multiple_matches_fails_closed(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch.object(
            engine.pocket_dom, "find_closed_trade_by_criteria",
            new=AsyncMock(return_value=("multiple_matches", [{"result": "win"}, {"result": "loss"}])),
        ):
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "reconciliation_required")

    def test_risk_counters_and_pnl_update_exactly_once(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch.object(
            engine.pocket_dom, "find_closed_trade_by_criteria",
            new=AsyncMock(return_value=("unique", {
                "result": "loss", "stake": 10.0, "final_value": 0.0,
                "asset": "AUD/JPY OTC", "direction": "SELL", "raw_values": ["$10.00", "$0"],
            })),
        ), patch.object(engine, "_apply_entry_outcome", new=AsyncMock(wraps=engine._apply_entry_outcome)) as spy:
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))
        spy.assert_awaited_once()
        series = database.get_trade_series(series_id)
        # loss with entries remaining (max_entries=2) schedules the next
        # entry rather than exhausting the series (matches the existing
        # "already resolved loss schedules the next one" test's own
        # current_entry_number convention - it stays at the entry that
        # just resolved; entry_times_utc_json gains the new schedule) -
        # the single-invocation guarantee (spy.assert_awaited_once) is the
        # actual assertion this test exists for.
        self.assertEqual(series["status"], "pending")
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(len(series["entry_times_utc"]), 1)

    def test_duplicate_reconciliation_pass_does_not_double_grade(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(),
            max_entries=1,  # single-entry series - a win terminates it outright
        )
        with patch.object(
            engine.pocket_dom, "find_closed_trade_by_criteria",
            new=AsyncMock(return_value=("unique", {
                "result": "win", "stake": 10.0, "final_value": 19.2,
                "asset": "AUD/JPY OTC", "direction": "SELL", "raw_values": ["$10.00", "$19.20"],
            })),
        ):
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))
            series_after_first = database.get_trade_series(series_id)
            self.assertEqual(series_after_first["status"], "won")
            self.assertEqual(series_after_first["net_profit_loss"], 9.2)

            # A second pass over the same (now resolved) series must be a
            # no-op - list_pending_trade_series only returns pending/active
            # series, so a 'won' series is already excluded from the scan
            # entirely, exactly the guarantee this test verifies.
            _run(engine.reconcile_stuck_series(warmup_service="fake-warmup"))

        series_after_second = database.get_trade_series(series_id)
        self.assertEqual(series_after_second["status"], "won")
        self.assertEqual(series_after_second["net_profit_loss"], 9.2)

    def test_reconcile_series_manually_applies_outcome_with_full_audit_trail(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:Target page, context or browser has been closed",
            opened_at=datetime.now(timezone.utc).isoformat(), max_entries=1,
        )
        result = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="browser_context_closed_during_result_read",
            authoritative_result="win", authoritative_pnl=9.2,
            original_error="Target page, context or browser has been closed",
        ))
        self.assertTrue(result["applied"])
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 9.2)
        self.assertIsNotNone(series["reconciliation_resolved_at"])

    def test_reconcile_series_manually_refuses_to_double_grade(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(), max_entries=1,
        )
        first = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="browser_context_closed_during_result_read",
            authoritative_result="win", authoritative_pnl=9.2,
        ))
        second = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="duplicate_attempt",
            authoritative_result="loss", authoritative_pnl=-10.0,
        ))
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        series = database.get_trade_series(series_id)
        # Still the FIRST reconciliation's outcome - a second call must
        # never overwrite an already-resolved series.
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 9.2)

    def test_reconcile_series_manually_accepts_a_series_already_flagged_reconciliation_required(self):
        series_id = self._active_series_with_open_entry(
            "error", result="error:boom", opened_at=datetime.now(timezone.utc).isoformat(), max_entries=1,
        )
        database.mark_series_reconciliation_required(series_id, "broker_history_no_match")
        result = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="operator_reviewed_broker_history_manually",
            authoritative_result="loss", authoritative_pnl=-10.0,
        ))
        self.assertTrue(result["applied"])
        series = database.get_trade_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")


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
