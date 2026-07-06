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
import asset_cache
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
        self._original_cache = asset_cache._cache
        asset_cache._cache = {}

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
        asset_cache._cache = self._original_cache
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
        asset_cache._cache = {"EUR/USD OTC": {"tradeable": False, "category": "Currencies"}}
        pool = FakeWorkerPool()
        coordinator = TradeCoordinator(pool, warmup_service=None)
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


if __name__ == "__main__":
    unittest.main()
