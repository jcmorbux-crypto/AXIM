import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import browser_worker_pool
from browser_worker_pool import BrowserWorker, BrowserWorkerPool


class FakePage:
    def __init__(self, closed=False):
        self._closed = closed

    def is_closed(self):
        return self._closed

    async def evaluate(self, *args, **kwargs):
        return 1


class FakeWarmupService:
    def __init__(self, generation=1):
        self.generation = generation

    async def ensure_alive(self):
        return self.generation

    def get_context(self):
        return MagicMock()


def _run(coro):
    return asyncio.run(coro)


class BrowserWorkerPoolTests(unittest.TestCase):
    def setUp(self):
        # Avoid the real browser-dependent modal-cleanup helper touching
        # anything - this suite tests the pool's own queue/generation
        # logic, not pocket_dom's DOM interactions.
        self._patcher = patch.object(
            browser_worker_pool.pocket_dom, "_close_active_dropdown_modal", new=AsyncMock(),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _pool_with_workers(self, n=2, warmup_generation=1):
        """Builds a pool's internal state directly (bypassing start()/
        _build_workers(), which needs a real Playwright page) - workers are
        real BrowserWorker instances with a FakePage standing in for the
        Playwright page."""
        warmup = FakeWarmupService(generation=warmup_generation)
        pool = BrowserWorkerPool(warmup, num_workers=n)
        pool._warmup_generation = warmup_generation
        pool._pool_generation = 1
        pool.workers = [BrowserWorker(i, FakePage(), pool._pool_generation) for i in range(n)]
        for w in pool.workers:
            pool._available.put_nowait(w)
        return pool, warmup

    def test_acquire_returns_worker_and_release_makes_it_available_again(self):
        pool, _ = self._pool_with_workers(n=1)
        worker = _run(pool.acquire_worker(timeout=0))
        self.assertIsNotNone(worker)
        self.assertTrue(worker.lock.locked())

        # A second acquire with timeout=0 must fail instantly - the only
        # worker is checked out.
        second = _run(pool.acquire_worker(timeout=0))
        self.assertIsNone(second)

        pool.release_worker(worker)
        self.assertFalse(worker.lock.locked())
        third = _run(pool.acquire_worker(timeout=0))
        self.assertIs(third, worker)

    def test_acquire_with_timeout_returns_none_when_pool_exhausted(self):
        pool, _ = self._pool_with_workers(n=1)
        _run(pool.acquire_worker(timeout=0))  # take the only worker
        result = _run(pool.acquire_worker(timeout=0.05))
        self.assertIsNone(result)

    def test_release_discards_worker_from_a_stale_generation(self):
        """A worker whose generation predates a browser-crash rebuild must
        be discarded, not returned to the pool - its page belongs to a
        browser context that no longer exists."""
        pool, _ = self._pool_with_workers(n=1)
        worker = _run(pool.acquire_worker(timeout=0))
        pool._pool_generation = 2  # simulate a rebuild happening while checked out
        pool.release_worker(worker)
        self.assertFalse(worker.lock.locked())
        # Must NOT have gone back into the queue - the queue should be empty.
        immediate = _run(pool.acquire_worker(timeout=0))
        self.assertIsNone(immediate)

    def test_ensure_worker_healthy_respawns_a_closed_page(self):
        pool, _ = self._pool_with_workers(n=1)
        worker = pool.workers[0]
        worker.page = FakePage(closed=True)
        new_page = FakePage(closed=False)

        async def fake_get_trading_page(context, url):
            return new_page

        with patch.object(browser_worker_pool, "get_trading_page", new=AsyncMock(side_effect=fake_get_trading_page)), \
             patch.object(browser_worker_pool.pocket_dom, "dismiss_blocking_modals", new=AsyncMock()):
            healed = _run(pool._ensure_worker_healthy(worker))
        self.assertIs(healed.page, new_page)

    def test_acquire_clears_a_stray_dropdown_modal(self):
        pool, _ = self._pool_with_workers(n=1)
        _run(pool.acquire_worker(timeout=0))
        browser_worker_pool.pocket_dom._close_active_dropdown_modal.assert_awaited()


class EnsurePoolHealthyTests(unittest.TestCase):
    """_ensure_pool_healthy() is the whole-browser-crash detector: every
    acquire_worker() call asks warmup_service.ensure_alive() for the
    current generation (subject to a TTL cache) and rebuilds every worker
    if it doesn't match what this pool was last built from. Previously
    untested - only the release-side stale-generation discard was covered
    (test_release_discards_worker_from_a_stale_generation above)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _event_counts(self, event_type):
        stats = database.get_recovery_event_stats()
        return {row["outcome"]: row["n"] for row in stats if row["event_type"] == event_type}

    def _pool(self, warmup_generation=1):
        warmup = MagicMock()
        warmup.ensure_alive = AsyncMock(return_value=warmup_generation)
        pool = BrowserWorkerPool(warmup, num_workers=1)
        pool._warmup_generation = warmup_generation
        return pool, warmup

    def test_within_ttl_skips_the_check_entirely(self):
        pool, warmup = self._pool()
        pool._last_pool_health_check = time.monotonic()  # "just checked"
        _run(pool._ensure_pool_healthy())
        warmup.ensure_alive.assert_not_called()

    def test_expired_ttl_same_generation_does_not_rebuild(self):
        pool, warmup = self._pool(warmup_generation=1)
        pool._last_pool_health_check = 0.0  # force past TTL
        with patch.object(pool, "_build_workers", new=AsyncMock()) as mock_build:
            _run(pool._ensure_pool_healthy())
        warmup.ensure_alive.assert_awaited_once()
        mock_build.assert_not_called()
        self.assertEqual(self._event_counts("worker_pool_rebuild"), {})
        self.assertGreater(pool._last_pool_health_check, 0.0)  # TTL clock was refreshed

    def test_expired_ttl_changed_generation_rebuilds_and_records_succeeded(self):
        pool, warmup = self._pool(warmup_generation=1)
        warmup.ensure_alive = AsyncMock(return_value=2)  # browser reconnected under us
        pool._last_pool_health_check = 0.0
        with patch.object(pool, "_build_workers", new=AsyncMock()) as mock_build:
            _run(pool._ensure_pool_healthy())
        mock_build.assert_awaited_once()
        self.assertEqual(pool._warmup_generation, 2)
        self.assertEqual(self._event_counts("worker_pool_rebuild"), {"succeeded": 1})

    def test_rebuild_failure_records_failed_event_and_reraises(self):
        pool, warmup = self._pool(warmup_generation=1)
        warmup.ensure_alive = AsyncMock(return_value=2)
        pool._last_pool_health_check = 0.0
        with patch.object(pool, "_build_workers", new=AsyncMock(side_effect=RuntimeError("rebuild failed"))):
            with self.assertRaises(RuntimeError):
                _run(pool._ensure_pool_healthy())
        self.assertEqual(self._event_counts("worker_pool_rebuild"), {"failed": 1})

    def test_concurrent_caller_already_refreshed_ttl_after_lock_skips_rebuild(self):
        # Simulates two callers racing past the outer TTL check before
        # either acquires _health_lock - the second one's re-check inside
        # the lock finds the first already refreshed it, so it must not
        # call ensure_alive() or rebuild a second time.
        pool, warmup = self._pool(warmup_generation=1)
        pool._last_pool_health_check = 0.0

        real_acquire = pool._health_lock.acquire

        async def acquire_and_mark_fresh():
            await real_acquire()
            pool._last_pool_health_check = time.monotonic()
            return True

        with patch.object(pool._health_lock, "acquire", new=AsyncMock(side_effect=acquire_and_mark_fresh)):
            _run(pool._ensure_pool_healthy())
        warmup.ensure_alive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
