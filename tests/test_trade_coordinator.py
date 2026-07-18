import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import risk_manager
import session_manager
import trade_coordinator
import pocket_executor
from trade_coordinator import TradeCoordinator


class FakeWorker:
    def __init__(self, worker_id=0):
        self.worker_id = worker_id


class FakeWorkerPool:
    """Stands in for BrowserWorkerPool without touching a real browser -
    acquire_worker/release_worker are the only surface TradeCoordinator
    actually calls on it."""

    def __init__(self, num_workers=2, worker_to_return="default"):
        self.num_workers = num_workers
        self._worker_to_return = worker_to_return
        self.released = []

    async def acquire_worker(self, timeout=0):
        if self._worker_to_return is None:
            return None
        return FakeWorker()

    def release_worker(self, worker):
        self.released.append(worker)


def _run(coro):
    return asyncio.run(coro)


class TradeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self._original_preview_only = trade_coordinator.PREVIEW_ONLY
        self._original_auto_execute = trade_coordinator.AUTO_EXECUTE
        self._original_max_signal_age = trade_coordinator.MAX_SIGNAL_AGE

        # Isolated risk thresholds - a unit test of the coordinator's
        # orchestration shouldn't fail because whatever the real .env
        # happens to have configured trips a risk rule unrelated to the
        # thing under test.
        self._original_max_trade_amount = risk_manager.MAX_TRADE_AMOUNT
        self._original_max_trades_per_hour = risk_manager.MAX_TRADES_PER_HOUR
        self._original_max_consecutive_losses = risk_manager.MAX_CONSECUTIVE_LOSSES
        self._original_cooldown = risk_manager.COOLDOWN_AFTER_LOSS_SECONDS
        self._original_dup_window = risk_manager.DUPLICATE_SIGNAL_WINDOW_SECONDS
        risk_manager.MAX_TRADE_AMOUNT = 50
        risk_manager.MAX_TRADES_PER_HOUR = 1000
        risk_manager.MAX_CONSECUTIVE_LOSSES = 1000
        risk_manager.COOLDOWN_AFTER_LOSS_SECONDS = 0
        risk_manager.DUPLICATE_SIGNAL_WINDOW_SECONDS = 120

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        trade_coordinator.PREVIEW_ONLY = self._original_preview_only
        trade_coordinator.AUTO_EXECUTE = self._original_auto_execute
        trade_coordinator.MAX_SIGNAL_AGE = self._original_max_signal_age
        risk_manager.MAX_TRADE_AMOUNT = self._original_max_trade_amount
        risk_manager.MAX_TRADES_PER_HOUR = self._original_max_trades_per_hour
        risk_manager.MAX_CONSECUTIVE_LOSSES = self._original_max_consecutive_losses
        risk_manager.COOLDOWN_AFTER_LOSS_SECONDS = self._original_cooldown
        risk_manager.DUPLICATE_SIGNAL_WINDOW_SECONDS = self._original_dup_window

    def _signal(self, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute"):
        return {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"}

    def test_preview_only_short_circuits_before_worker_pool(self):
        trade_coordinator.PREVIEW_ONLY = True
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "preview")
        self.assertEqual(pool.released, [])  # never touched the pool at all

    def test_stale_signal_is_ignored(self):
        trade_coordinator.PREVIEW_ONLY = True
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)
        old_sent_at = datetime.now() - timedelta(seconds=trade_coordinator.MAX_SIGNAL_AGE + 30)
        result = _run(coordinator.handle_signal(self._signal(), sent_at=old_sent_at))
        self.assertEqual(result["status"], "ignored")
        self.assertEqual(result["reason"], "stale_signal")

    def test_risk_violation_rejects_before_worker_pool(self):
        trade_coordinator.PREVIEW_ONLY = True
        risk_manager.MAX_TRADE_AMOUNT = 0.01  # TRADE_AMOUNT will exceed this
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "max_trade_amount")
        self.assertEqual(pool.released, [])

    def test_emergency_stop_rejects_before_worker_pool(self):
        """A signal that's already inside handle_signal() when Emergency
        Stop is pressed must not reach execution - core/risk_manager.py's
        check_not_stopped() is the first check in the preflight sequence,
        specifically so this can't slip through the other checks (none of
        which look at control state)."""
        trade_coordinator.PREVIEW_ONLY = True
        database.set_control_state(emergency_stop=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "emergency_stop")
        self.assertEqual(pool.released, [])

    def test_broker_account_emergency_stop_rejects_before_worker_pool(self):
        """Same enforcement point as the global Emergency Stop, but scoped
        to one broker account - a signal routed to a stopped account must
        be rejected even though the global switch was never touched."""
        trade_coordinator.PREVIEW_ONLY = True
        account_id = database.create_broker_account("Stopped Account")
        database.update_broker_account(account_id, emergency_stopped=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal(), broker_account_id=account_id))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "account_emergency_stop")
        self.assertEqual(pool.released, [])

    def test_a_different_accounts_signal_is_unaffected_by_another_accounts_stop(self):
        trade_coordinator.PREVIEW_ONLY = True
        stopped_id = database.create_broker_account("Stopped Account")
        other_id = database.create_broker_account("Other Account")
        database.update_broker_account(stopped_id, emergency_stopped=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal(), broker_account_id=other_id))
        self.assertNotEqual(result.get("rule"), "account_emergency_stop")

    def test_paused_rejects_before_worker_pool(self):
        trade_coordinator.PREVIEW_ONLY = True
        database.set_control_state(paused=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "paused")

    def test_duplicate_signal_rejected(self):
        trade_coordinator.PREVIEW_ONLY = True
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)
        signal = self._signal()
        first = _run(coordinator.handle_signal(signal))
        second = _run(coordinator.handle_signal(signal))
        self.assertEqual(first["status"], "preview")
        self.assertEqual(second["status"], "rejected")
        self.assertEqual(second["rule"], "duplicate_signal")

    def test_asset_untradeable_cached_rejects_without_touching_pool(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        coordinator.asset_cache._cache = {"EUR/USD OTC": {"tradeable": False, "category": "Currencies"}}
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "asset_untradeable_cached")
        self.assertEqual(pool.released, [])  # rejected before ever acquiring a worker

    def test_all_workers_busy_rejects_cleanly(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        pool = FakeWorkerPool(worker_to_return=None)
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "all_workers_busy")

    def test_test_mode_short_circuits_before_worker_pool(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        database.set_control_state(test_mode=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertEqual(result["status"], "test_mode")
        self.assertEqual(pool.released, [])  # never touched the pool at all

    def test_test_mode_off_reaches_pocket_executor_normally(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        database.set_control_state(test_mode=False)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        mock_prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        pocket_executor.prepare_trade = mock_prepare_trade
        try:
            result = _run(coordinator.handle_signal(self._signal()))
        finally:
            pocket_executor.prepare_trade = original_prepare_trade

        self.assertEqual(result["status"], "clicked")
        mock_prepare_trade.assert_awaited_once()

    def test_session_limit_already_reached_rejects_before_worker_pool(self):
        trade_coordinator.PREVIEW_ONLY = True
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=10)
        database.update_session_pnl(session_id, 10)  # already at target
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal(), session_id=session_id))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "session_profit_target")
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_target")
        self.assertEqual(pool.released, [])

    def test_session_id_recorded_on_signal_and_increments_trade_count(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        session_id = database.start_trading_session("Test", [1], "DEMO", max_trades=5)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        pocket_executor.prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        try:
            result = _run(coordinator.handle_signal(self._signal(), session_id=session_id))
        finally:
            pocket_executor.prepare_trade = original_prepare_trade

        self.assertEqual(result["status"], "clicked")
        self.assertEqual(database.get_signal_session_id(result["trade_id"]), session_id)
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 1)

    def test_no_active_session_leaves_session_id_null(self):
        trade_coordinator.PREVIEW_ONLY = True
        coordinator = TradeCoordinator(FakeWorkerPool(), warmup_service=None)
        result = _run(coordinator.handle_signal(self._signal()))
        self.assertIsNone(database.get_signal_session_id(result["trade_id"]))

    def test_full_success_path_delegates_to_pocket_executor_and_releases_nothing_extra(self):
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        mock_prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        pocket_executor.prepare_trade = mock_prepare_trade
        try:
            result = _run(coordinator.handle_signal(self._signal()))
        finally:
            pocket_executor.prepare_trade = original_prepare_trade

        self.assertEqual(result["status"], "clicked")
        mock_prepare_trade.assert_awaited_once()
        # warmup_service must be threaded through to prepare_trade - a
        # regression here would silently break outcome-tracking's dedicated
        # page (see pocket_executor.track_outcome).
        call_args = mock_prepare_trade.call_args
        self.assertIn("fake-warmup", call_args.args)


class TradeConfirmationGateIntegrationTests(unittest.TestCase):
    """Confirms the gate is actually wired into the real pipeline, not
    just unit-tested in isolation - see tests/test_session_manager.py's
    TradeConfirmationGateTests for the gate's own logic."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self._original_preview_only = trade_coordinator.PREVIEW_ONLY
        self._original_auto_execute = trade_coordinator.AUTO_EXECUTE
        self._original_timeout = session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS
        trade_coordinator.PREVIEW_ONLY = False
        trade_coordinator.AUTO_EXECUTE = True
        database.set_control_state(test_mode=False)

        self._original_max_trade_amount = risk_manager.MAX_TRADE_AMOUNT
        risk_manager.MAX_TRADE_AMOUNT = 50

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        trade_coordinator.PREVIEW_ONLY = self._original_preview_only
        trade_coordinator.AUTO_EXECUTE = self._original_auto_execute
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = self._original_timeout
        risk_manager.MAX_TRADE_AMOUNT = self._original_max_trade_amount

    def _signal(self):
        return {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}

    def test_live_confirmation_required_blocks_then_proceeds_once_confirmed(self):
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 5
        session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        pocket_executor.prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})

        async def _confirm_soon():
            # Poll briefly for the pending row to appear, then confirm it -
            # avoids a fixed sleep racing against the coordinator's own setup.
            for _ in range(20):
                pending = database.list_pending_trade_confirmations()
                if pending:
                    database.decide_trade_confirmation(pending[0]["trade_id"], "confirmed", decided_by="tester@axim.local")
                    return
                await asyncio.sleep(0.05)
            self.fail("no pending confirmation appeared")

        async def _scenario():
            return await asyncio.gather(
                coordinator.handle_signal(self._signal(), session_id=session_id),
                _confirm_soon(),
            )

        try:
            result, _ = _run(_scenario())
        finally:
            pocket_executor.prepare_trade = original_prepare_trade

        self.assertEqual(result["status"], "clicked")

    def test_emergency_stop_pressed_during_confirmation_wait_still_blocks_execution(self):
        """wait_for_trade_confirmation can block on a real human for a
        while - long enough for an operator to hit Emergency Stop mid-
        wait. A trade confirmed AFTER that (the human hadn't seen the
        stop yet, or confirmed a moment too late) must still not reach
        execution - the re-check right after the confirmation gate
        (core/trade_coordinator.py) exists specifically for this window."""
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 5
        session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        pocket_executor.prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})

        async def _emergency_stop_then_confirm():
            for _ in range(20):
                pending = database.list_pending_trade_confirmations()
                if pending:
                    database.set_control_state(emergency_stop=True)
                    database.decide_trade_confirmation(pending[0]["trade_id"], "confirmed", decided_by="tester@axim.local")
                    return
                await asyncio.sleep(0.05)
            self.fail("no pending confirmation appeared")

        async def _scenario():
            return await asyncio.gather(
                coordinator.handle_signal(self._signal(), session_id=session_id),
                _emergency_stop_then_confirm(),
            )

        try:
            result, _ = _run(_scenario())
        finally:
            pocket_executor.prepare_trade = original_prepare_trade
            database.set_control_state(emergency_stop=False)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "emergency_stop")
        self.assertEqual(pool.released, [])  # never reached the worker pool

    def test_live_confirmation_timeout_rejects_before_worker_pool(self):
        session_manager.TRADE_CONFIRMATION_TIMEOUT_SECONDS = 0.3  # never answered
        session_id = database.start_trading_session("Test", [1], "LIVE", require_confirmation=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")
        result = _run(coordinator.handle_signal(self._signal(), session_id=session_id))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "trade_not_confirmed")
        self.assertEqual(pool.released, [])  # never reached the worker pool
        # rejected before the trade counted toward the session at all
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 0)

    def test_demo_session_confirmation_required_does_not_block(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", require_confirmation=True)
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service="fake-warmup")

        original_prepare_trade = pocket_executor.prepare_trade
        pocket_executor.prepare_trade = AsyncMock(return_value={"status": "clicked", "trade_id": 1})
        try:
            result = _run(coordinator.handle_signal(self._signal(), session_id=session_id))
        finally:
            pocket_executor.prepare_trade = original_prepare_trade

        self.assertEqual(result["status"], "clicked")
        self.assertEqual(database.list_pending_trade_confirmations(), [])


class EventLoopNotBlockedDuringRiskChecksTests(unittest.TestCase):
    """docs/AXIM_COMPETITIVE_BENCHMARK.md item 1: the Validation/Risk
    Manager/Session limits/Duplicate Detection stages used to run
    directly on the event loop thread as blocking sqlite3 calls -
    proven here to no longer be true, not just asserted. A slow
    (artificially delayed) risk check runs concurrently with a
    lightweight ticker coroutine; if the risk check still blocked the
    loop, the ticker would get zero ticks during the delay. This is a
    genuine concurrency test (real asyncio.sleep interleaving), not a
    mock of asyncio.to_thread itself - it would fail if handle_signal
    were reverted to calling _run_preflight_checks directly instead of
    through asyncio.to_thread."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._original_preview_only = trade_coordinator.PREVIEW_ONLY
        trade_coordinator.PREVIEW_ONLY = True

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        trade_coordinator.PREVIEW_ONLY = self._original_preview_only

    def test_ticker_keeps_running_during_a_slow_risk_check(self):
        import time as time_module

        SLOW_CHECK_SECONDS = 0.3
        original_check = risk_manager.check_max_trades_per_hour

        def slow_check_max_trades_per_hour():
            time_module.sleep(SLOW_CHECK_SECONDS)  # simulates a slow blocking DB round trip
            return original_check()

        ticks = []

        async def ticker():
            while True:
                ticks.append(time_module.monotonic())
                await asyncio.sleep(0.02)

        async def scenario():
            pool = FakeWorkerPool()
            coordinator = TradeCoordinator(pool, warmup_service=None)
            ticker_task = asyncio.create_task(ticker())
            try:
                result = await coordinator.handle_signal({
                    "asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
                })
            finally:
                ticker_task.cancel()
            return result

        risk_manager.check_max_trades_per_hour = slow_check_max_trades_per_hour
        try:
            result = _run(scenario())
        finally:
            risk_manager.check_max_trades_per_hour = original_check

        self.assertEqual(result["status"], "preview")
        # With a 0.3s blocking delay and a 0.02s ticker interval, a
        # genuinely non-blocked loop gets roughly 10+ ticks during the
        # delay alone. Asserting a conservative floor (not an exact
        # count) to stay robust against real scheduling jitter.
        self.assertGreaterEqual(len(ticks), 5)


if __name__ == "__main__":
    unittest.main()
