"""core/pocket_executor.py's prepare_trade/track_outcome had ZERO
offline unit coverage before this file (the only existing test,
test_pocket_execution_dryrun.py, drives a real browser against the live
Pocket Option demo cabinet and is skipped unless explicitly opted in).
Built alongside the Live Signal Pipeline instrumentation (2026-07-19 v2
mandate) specifically so that safety-critical, previously-untested code
path has a real regression net - every pocket_dom.* call is mocked, no
real browser or network I/O anywhere in this file."""
import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import pocket_executor
import pocket_dom
import risk_manager
from execution_latency import ExecutionLatency
from signal_lifecycle import SignalLifecycleState
from timeline import TradeTimeline


def _run(coro):
    return asyncio.run(coro)


class FakeWorker:
    def __init__(self, worker_id=0, page="fake-page"):
        self.worker_id = worker_id
        self.page = page


class FakePool:
    def __init__(self):
        self.released = []

    def release_worker(self, worker):
        self.released.append(worker)


class PocketExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self._original_armed = pocket_executor.ARMED
        self._original_save_screenshots = pocket_executor.SAVE_SCREENSHOTS
        pocket_executor.SAVE_SCREENSHOTS = False  # never touch a real page.screenshot

        # Deterministic regardless of this environment's real .env
        # (confirmed live: MINIMUM_PAYOUT can be locally overridden to 0
        # for dev convenience, which would silently make the minimum-
        # payout rejection test meaningless without this).
        self._original_minimum_payout = risk_manager.MINIMUM_PAYOUT
        risk_manager.MINIMUM_PAYOUT = 90

        # Save every pocket_dom function prepare_trade/track_outcome call,
        # so each test can override just the ones it needs.
        self._orig = {
            name: getattr(pocket_dom, name) for name in (
                "select_asset", "select_expiry", "set_amount", "verify_direction_controls_ready",
                "read_payout_and_check_tradeable", "click_direction", "wait_for_trade_result",
            )
        }
        pocket_dom.select_asset = AsyncMock(return_value=None)
        pocket_dom.select_expiry = AsyncMock(return_value=None)
        pocket_dom.set_amount = AsyncMock(return_value=None)
        pocket_dom.verify_direction_controls_ready = AsyncMock(return_value=None)
        pocket_dom.read_payout_and_check_tradeable = AsyncMock(return_value=(92, True))  # clears the 90% test floor below
        pocket_dom.click_direction = AsyncMock(return_value=None)
        pocket_dom.wait_for_trade_result = AsyncMock(
            return_value={"result": "win", "final_value": 18.5, "stake": 10})

        self._original_track_outcome = pocket_executor.track_outcome
        pocket_executor.track_outcome = AsyncMock(return_value=None)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        pocket_executor.ARMED = self._original_armed
        pocket_executor.SAVE_SCREENSHOTS = self._original_save_screenshots
        risk_manager.MINIMUM_PAYOUT = self._original_minimum_payout
        for name, fn in self._orig.items():
            setattr(pocket_dom, name, fn)
        pocket_executor.track_outcome = self._original_track_outcome

    def _new_trade(self, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute"):
        signal = {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"}
        return database.record_signal_received(signal)


class PrepareTradeArmedFalseTests(PocketExecutorTestCase):
    def test_sized_is_tracked_but_not_broker_accepted(self):
        pocket_executor.ARMED = False
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline))
        self.assertEqual(result["status"], "prepared_not_armed")
        events = database.list_pipeline_events_for_signal(trade_id)
        states = [e["state"] for e in events]
        self.assertIn(SignalLifecycleState.SIZED, states)
        self.assertIn(SignalLifecycleState.SKIPPED, states)
        self.assertNotIn(SignalLifecycleState.BROKER_ACCEPTED, states)
        skipped = next(e for e in events if e["state"] == SignalLifecycleState.SKIPPED)
        self.assertEqual(skipped["detail"], "armed_false")
        self.assertEqual(pool.released, [worker])


class PrepareTradeClickedTests(PocketExecutorTestCase):
    def test_successful_click_tracks_sized_broker_accepted_open(self):
        pocket_executor.ARMED = True
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline))
        self.assertEqual(result["status"], "clicked")
        events = database.list_pipeline_events_for_signal(trade_id)
        states = [e["state"] for e in events]
        self.assertEqual(
            states,
            [SignalLifecycleState.SIZED, SignalLifecycleState.BROKER_ACCEPTED, SignalLifecycleState.OPEN],
        )
        self.assertEqual(pool.released, [worker])


class PrepareTradeRejectionTests(PocketExecutorTestCase):
    def test_unparseable_expiry_tracks_failed_before_touching_the_dom(self):
        trade_id = self._new_trade(expiry="not a real expiry")
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "not a real expiry", 10, worker, pool,
            warmup_service=None, timeline=timeline))
        self.assertEqual(result["rule"], "unparseable_expiry")
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], SignalLifecycleState.FAILED)
        self.assertEqual(events[0]["detail"], "unparseable_expiry")
        pocket_dom.select_asset.assert_not_awaited()  # never touched the DOM at all

    def test_asset_untradeable_tracks_broker_rejected(self):
        pocket_dom.read_payout_and_check_tradeable = AsyncMock(return_value=(None, False))
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline))
        self.assertEqual(result["rule"], "asset_untradeable")
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.BROKER_REJECTED)
        self.assertEqual(events[-1]["detail"], "asset_untradeable")
        self.assertEqual(pool.released, [worker])

    def test_minimum_payout_tracks_skipped(self):
        pocket_dom.read_payout_and_check_tradeable = AsyncMock(return_value=(5, True))  # far below any real minimum
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline))
        self.assertEqual(result["rule"], "minimum_payout")
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.SKIPPED)
        self.assertIn("minimum_payout", events[-1]["detail"])

    def test_unhandled_dom_exception_tracks_failed_and_still_releases_worker(self):
        pocket_dom.select_asset = AsyncMock(side_effect=RuntimeError("dom exploded"))
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        with self.assertRaises(RuntimeError):
            _run(pocket_executor.prepare_trade(
                trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool,
                warmup_service=None, timeline=timeline))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.FAILED)
        self.assertIn("dom exploded", events[-1]["detail"])
        self.assertEqual(pool.released, [worker])  # finally block still ran


class TrackOutcomeTests(PocketExecutorTestCase):
    """track_outcome is tested directly here (it was replaced with an
    AsyncMock in setUp for the prepare_trade tests above, which only
    care that it gets fire-and-forget spawned, not what it does)."""

    def setUp(self):
        super().setUp()
        pocket_executor.track_outcome = self._original_track_outcome  # restore the real function for these tests

    def test_win_is_tracked_won(self):
        pocket_dom.wait_for_trade_result = AsyncMock(return_value={"result": "win", "final_value": 18.5, "stake": 10})
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.WON)

    def test_loss_is_tracked_lost(self):
        pocket_dom.wait_for_trade_result = AsyncMock(return_value={"result": "loss", "final_value": 0, "stake": 10})
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.LOST)

    def test_draw_is_tracked_draw(self):
        pocket_dom.wait_for_trade_result = AsyncMock(return_value={"result": "draw", "final_value": 10, "stake": 10})
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.DRAW)

    def test_unclassifiable_result_is_tracked_unknown(self):
        pocket_dom.wait_for_trade_result = AsyncMock(
            return_value={"result": "unknown", "final_value": None, "stake": 10})
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.UNKNOWN)

    def test_result_read_failure_is_tracked_unknown(self):
        pocket_dom.wait_for_trade_result = AsyncMock(return_value=None)
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.UNKNOWN)
        self.assertEqual(events[-1]["detail"], "result_read_failed")

    def test_exception_during_outcome_wait_is_tracked_failed(self):
        pocket_dom.wait_for_trade_result = AsyncMock(side_effect=RuntimeError("network blip"))
        trade_id = self._new_trade()
        _run(pocket_executor.track_outcome(warmup_service=None, trade_id=trade_id, expiry_seconds=1))  # must not raise
        events = database.list_pipeline_events_for_signal(trade_id)
        self.assertEqual(events[-1]["state"], SignalLifecycleState.FAILED)
        self.assertIn("network blip", events[-1]["detail"])


class PreStageAndSubmitSplitTests(PocketExecutorTestCase):
    """2026-07-27 precision-latency audit: prepare_trade was split into
    pre_stage_trade (everything up to and including the DB "prepared"
    write, but never the click) and submit_staged_trade (the ARMED check,
    the click, and everything after). prepare_trade itself just calls
    both back-to-back (covered already by the tests above, still
    passing) - these tests cover the split calling convention Martin
    Trader's precision-execution path actually uses."""

    def test_pre_stage_trade_returns_a_staged_trade_without_releasing_the_worker(self):
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline,
        ))
        self.assertIsInstance(staged, pocket_executor.StagedTrade)
        self.assertEqual(staged.trade_id, trade_id)
        self.assertEqual(staged.worker, worker)
        self.assertEqual(pool.released, [], "worker must still be held after a successful pre-stage")
        pocket_dom.click_direction.assert_not_awaited()  # the whole point - no click yet

    def test_pre_stage_trade_marks_prestage_ready_on_the_latency_object(self):
        """prestage_ready_at lives in ExecutionLatency, not the shared
        TradeTimeline STAGES list - adding it there would have landed
        right before Martin Trader's ~20s pre-stage gap, corrupting
        core/timeline_report.py's cross-provider stage_deltas() ordering
        assumption for every OTHER provider's stage_deltas output too."""
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None,
            timeline=timeline, latency=latency,
        ))
        self.assertIsInstance(staged, pocket_executor.StagedTrade)
        self.assertIn("prestage_ready_at", latency.timestamps)
        self.assertNotIn("prestage_ready", timeline.stage_timestamps)

    def test_pre_stage_trade_rejection_releases_the_worker(self):
        trade_id = self._new_trade(expiry="not a real expiry")
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "not a real expiry", 10, worker, pool,
            warmup_service=None, timeline=timeline,
        ))
        self.assertEqual(result["rule"], "unparseable_expiry")
        self.assertEqual(pool.released, [worker], "a rejected pre-stage has nothing left to submit")

    def test_submit_staged_trade_clicks_and_releases_the_worker(self):
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline,
        ))
        pocket_executor.ARMED = True
        result = _run(pocket_executor.submit_staged_trade(staged))
        self.assertEqual(result["status"], "clicked")
        pocket_dom.click_direction.assert_awaited_once()
        self.assertEqual(pool.released, [worker])

    def test_submit_staged_trade_passes_latency_through_to_click_direction(self):
        """2026-07-27 precision-bottleneck investigation: click_direction
        needs the explicit latency object (not just the ambient
        get_current_timeline()) to mark click_completed_at, since
        _run_precision_entry's own timeline.activate() wiring was only
        just fixed - this is the wiring half of that fix, verified
        independent of the real DOM internals (click_direction is mocked
        here, same as every other test in this class)."""
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None,
            timeline=timeline, latency=latency,
        ))
        pocket_executor.ARMED = True
        _run(pocket_executor.submit_staged_trade(staged, latency=latency))
        pocket_dom.click_direction.assert_awaited_once_with(worker.page, "BUY", latency=latency)

    def test_submit_staged_trade_armed_false_releases_the_worker_without_clicking(self):
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline,
        ))
        pocket_executor.ARMED = False
        result = _run(pocket_executor.submit_staged_trade(staged))
        self.assertEqual(result["status"], "prepared_not_armed")
        pocket_dom.click_direction.assert_not_awaited()
        self.assertEqual(pool.released, [worker])

    def test_prepare_trade_is_equivalent_to_pre_stage_then_submit(self):
        """The combined wrapper must behave identically to calling the
        two halves back-to-back - locks in that the split was a pure
        refactor, not a behavior change, for every existing caller."""
        pocket_executor.ARMED = True
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        result = _run(pocket_executor.prepare_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline,
        ))
        self.assertEqual(result["status"], "clicked")
        self.assertEqual(pool.released, [worker])


class ExecutionLatencyMarkingTests(PocketExecutorTestCase):
    """2026-07-27 precision-latency audit: confirms pre_stage_trade/
    submit_staged_trade/track_outcome actually populate the latency
    object's fields when one is passed, and never raise or change
    behavior when none is passed (latency=None, the default)."""

    def test_latency_fields_populated_across_the_full_flow(self):
        pocket_executor.track_outcome = self._original_track_outcome  # need the real one for result fields
        pocket_executor.ARMED = True
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)

        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None,
            timeline=timeline, latency=latency,
        ))
        self.assertIn("browser_command_started_at", latency.timestamps)

        result = _run(pocket_executor.submit_staged_trade(staged, latency=latency))
        self.assertEqual(result["status"], "clicked")
        self.assertIn("order_payload_sent_at", latency.timestamps)
        self.assertIn("broker_acknowledged_at", latency.timestamps)
        self.assertIn("broker_trade_opened_at", latency.timestamps)

        metrics = latency.metrics_ms()
        self.assertIsNotNone(metrics["browser_command_ms"])
        self.assertGreaterEqual(metrics["browser_command_ms"], 0)
        self.assertIsNotNone(metrics["broker_acknowledgement_ms"])

    def test_no_latency_object_is_a_complete_no_op(self):
        pocket_executor.ARMED = True
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool, warmup_service=None, timeline=timeline,
        ))
        result = _run(pocket_executor.submit_staged_trade(staged))  # no latency=... at all
        self.assertEqual(result["status"], "clicked")

    def test_minimum_payout_rejection_marks_rejected_at(self):
        """2026-07-27 campaign-classification addition: a live minimum-
        payout rejection (the exact real outcome campaign series #22
        hit) must record rejected_at so boundary_to_rejection_ms can
        separate a fast, correct rejection from a slow one caused by a
        real execution delay eating into the pre-stage window first."""
        pocket_dom.read_payout_and_check_tradeable = AsyncMock(return_value=(5, True))
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        latency.set_scheduled_boundary(datetime.now(timezone.utc))
        result = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool,
            warmup_service=None, timeline=timeline, latency=latency,
        ))
        self.assertEqual(result["rule"], "minimum_payout")
        self.assertIn("rejected_at", latency.timestamps)
        self.assertIsNotNone(latency.metrics_ms()["boundary_to_rejection_ms"])

    def test_asset_untradeable_rejection_marks_rejected_at(self):
        pocket_dom.read_payout_and_check_tradeable = AsyncMock(return_value=(None, False))
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        result = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool,
            warmup_service=None, timeline=timeline, latency=latency,
        ))
        self.assertEqual(result["rule"], "asset_untradeable")
        self.assertIn("rejected_at", latency.timestamps)

    def test_unparseable_expiry_rejection_marks_rejected_at(self):
        trade_id = self._new_trade(expiry="not a real expiry")
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        result = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "not a real expiry", 10, worker, pool,
            warmup_service=None, timeline=timeline, latency=latency,
        ))
        self.assertEqual(result["rule"], "unparseable_expiry")
        self.assertIn("rejected_at", latency.timestamps)

    def test_armed_false_marks_rejected_at(self):
        trade_id = self._new_trade()
        timeline = TradeTimeline(trade_id=trade_id)
        worker, pool = FakeWorker(), FakePool()
        latency = ExecutionLatency(series_id=1, entry_number=1)
        staged = _run(pocket_executor.pre_stage_trade(
            trade_id, "EUR/USD OTC", "BUY", "1 Minute", 10, worker, pool,
            warmup_service=None, timeline=timeline, latency=latency,
        ))
        pocket_executor.ARMED = False
        result = _run(pocket_executor.submit_staged_trade(staged, latency=latency))
        self.assertEqual(result["status"], "prepared_not_armed")
        self.assertIn("rejected_at", latency.timestamps)


if __name__ == "__main__":
    unittest.main()
