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


if __name__ == "__main__":
    unittest.main()
