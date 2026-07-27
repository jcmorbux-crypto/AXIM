import asyncio
import os
import sys
import time
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

from browser_session import DEMO_URL, get_trading_page
from browser_health import BrowserHealthManager, RetryableBrowserError
import pocket_dom
from logger import get_logger
import database

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

# How long a previous health check (whole-pool or per-worker) is trusted
# before re-verifying live, instead of probing on every single
# acquire_worker() call. Documented tradeoff (P0 latency sprint): this
# trades up to HEALTH_CHECK_TTL_SECONDS of extra detection delay for a
# crash that happens to land inside the TTL window, in exchange for
# skipping a redundant page.evaluate() IPC round trip on the hot path most
# of the time (previously measured at 0-31ms per acquire, Phase 5).
# page.is_closed() (a local, synchronous property - not an IPC call) is
# still always checked regardless of TTL, so an already-closed page is
# still caught instantly; only the live "is it actually responsive" probe
# is subject to the TTL.
HEALTH_CHECK_TTL_SECONDS = float(os.getenv("HEALTH_CHECK_TTL_SECONDS", 2))


class BrowserWorker:
    def __init__(self, worker_id, page, generation):
        self.worker_id = worker_id
        self.page = page
        self.lock = asyncio.Lock()
        # Which pool rebuild this worker belongs to - lets release_worker
        # detect and discard a worker whose page belongs to a browser
        # context that no longer exists (see BrowserWorkerPool docstring).
        self.generation = generation
        self.last_health_check = 0.0


class BrowserWorkerPool:
    """
    N warm, pre-logged-in pages (tabs) within the SAME persistent browser
    context that BrowserWarmupService verified as demo mode - not a
    separate browser or session per worker, and demo-mode enforcement is
    therefore inherited from that shared context rather than re-checked
    per worker (they cannot diverge - same login session).

    Each worker owns its own asyncio.Lock (not one global lock), so trades
    on different workers run fully in parallel.

    Worker selection, queueing, and rejection are one mechanism: an
    asyncio.Queue pre-filled with all workers. acquire_worker(timeout=0)
    rejects instantly if all are busy; timeout=N waits up to N seconds
    (queued, FIFO order via the queue itself); timeout=None waits
    indefinitely.

    Whole-browser-crash recovery: every acquire_worker() call first asks
    warmup_service.ensure_alive() for the current generation. If it
    doesn't match the generation this pool last built its workers from,
    the underlying browser was relaunched (a full crash, not just one
    tab) - every worker's page belongs to a now-nonexistent browser
    process, so the whole pool is rebuilt from the new context rather
    than trying to patch individual pages. A worker that was mid-trade
    during the crash and gets released later (via its own try/except)
    is recognized as stale by its own generation number and discarded
    instead of corrupting the freshly-rebuilt pool.
    """

    def __init__(self, warmup_service, num_workers=2):
        self.warmup_service = warmup_service
        self.num_workers = num_workers
        self.workers = []
        self._available = asyncio.Queue()
        self._warmup_generation = None
        self._pool_generation = 0
        self._health_lock = asyncio.Lock()
        self._last_pool_health_check = 0.0
        self._health_manager = BrowserHealthManager()

    async def start(self):
        self._warmup_generation = await self.warmup_service.ensure_alive()
        await self._build_workers()

    async def _build_workers(self):
        self._pool_generation += 1
        context = self.warmup_service.get_context()

        # Drain any stale entries defensively (e.g. rebuilding after a
        # crash that happened between calls).
        while True:
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.workers = []
        for i in range(self.num_workers):
            page = await get_trading_page(context, DEMO_URL)
            await pocket_dom.dismiss_blocking_modals(page)
            worker = BrowserWorker(i, page, self._pool_generation)
            self.workers.append(worker)
            self._available.put_nowait(worker)
        logger.info(
            "browser_worker_pool: built %d worker(s) at generation %s",
            self.num_workers, self._pool_generation,
        )

    async def _ensure_pool_healthy(self):
        if time.monotonic() - self._last_pool_health_check < HEALTH_CHECK_TTL_SECONDS:
            return

        async with self._health_lock:
            if time.monotonic() - self._last_pool_health_check < HEALTH_CHECK_TTL_SECONDS:
                return  # a concurrent caller already refreshed it while we waited for the lock

            current_generation = await self.warmup_service.ensure_alive()
            self._last_pool_health_check = time.monotonic()
            if current_generation != self._warmup_generation:
                logger.warning(
                    "browser_worker_pool: underlying browser reconnected "
                    "(warmup generation %s -> %s) - rebuilding all workers "
                    "from the new browser context",
                    self._warmup_generation, current_generation,
                )
                self._warmup_generation = current_generation
                try:
                    await self._build_workers()
                except Exception as e:
                    database.record_recovery_event("worker_pool_rebuild", "failed", str(e))
                    raise
                else:
                    database.record_recovery_event(
                        "worker_pool_rebuild", "succeeded",
                        f"generation={current_generation} workers={self.num_workers}",
                    )

    async def acquire_worker(self, timeout=0):
        await self._ensure_pool_healthy()

        try:
            if timeout == 0:
                worker = self._available.get_nowait()
            elif timeout is None:
                worker = await self._available.get()
            else:
                worker = await asyncio.wait_for(self._available.get(), timeout=timeout)
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None

        worker = await self._ensure_worker_healthy(worker)
        await worker.lock.acquire()
        await self._ensure_no_stray_modal(worker)
        return worker

    async def _ensure_no_stray_modal(self, worker):
        """A trade that fails mid-sequence (select_asset/select_expiry
        timing out) can leave that page's dropdown-style modal open - the
        asset picker and expiry picker both render into the same shared
        #modal-root wrapper (pocket_dom.SEL_ACTIVE_DROPDOWN_MODAL) -
        because prepare_trade's exception handler doesn't clean up before
        releasing the worker back to the pool (attempting cleanup there
        would risk masking the real error with a second one). Confirmed
        live: a failed select_expiry left its worker's modal open, and the
        very next trade that reused that same worker failed at select_asset
        because the leftover modal blocked the search field - this is the
        single chokepoint that catches that regardless of which prior
        operation caused it. Cheap when nothing is wrong (the modal
        presence check is a fast local DOM read, not a poll)."""
        try:
            await pocket_dom._close_active_dropdown_modal(worker.page)
        except Exception as e:
            logger.warning(
                "browser_worker_pool: worker_id=%s failed to clear a stray dropdown modal: %s",
                worker.worker_id, e,
            )

    def release_worker(self, worker):
        if worker.generation != self._pool_generation:
            logger.info(
                "browser_worker_pool: worker_id=%s is from a previous generation "
                "(%s != %s) - discarding instead of returning to the pool, its "
                "page belongs to a browser context that no longer exists",
                worker.worker_id, worker.generation, self._pool_generation,
            )
            if worker.lock.locked():
                worker.lock.release()
            return

        if worker.lock.locked():
            worker.lock.release()
        self._available.put_nowait(worker)

    async def _respawn_worker_page(self, worker):
        context = self.warmup_service.get_context()
        new_page = await get_trading_page(context, DEMO_URL)
        await pocket_dom.dismiss_blocking_modals(new_page)
        worker.page = new_page
        # last_health_check is deliberately left as-is (not reset to "now") -
        # the forced re-check right after this call (force_deep=True) always
        # runs the real responsiveness probe regardless of that timestamp,
        # and BrowserHealthManager itself updates it the moment that probe
        # actually passes. A fresh page also has no deep-check track record
        # yet - note_page_replaced clears that separately, so the very next
        # check_worker() call for this worker_id runs the full deep check
        # regardless of DEEP_HEALTH_CHECK_TTL_SECONDS too.
        self._health_manager.note_page_replaced(worker.worker_id)

    async def _ensure_worker_healthy(self, worker):
        """Handles a single worker's page acting up while the rest of the
        browser is fine - the whole-browser-crash case is caught earlier, in
        _ensure_pool_healthy(), before a worker is even pulled from the
        queue. Delegates the actual checks (page responsive, DOM ready,
        session authenticated, live data flowing) to BrowserHealthManager -
        see that module's docstring for the full layered design and why
        each check exists.

        Escalation, cheapest fix first: a failure first gets one respawn of
        JUST this worker's page (transparent to every OTHER worker - each
        has its own lock and its own page, so this never interrupts a
        healthy worker mid-trade). The respawned page is re-verified with
        the full deep check before being trusted. Only if it STILL fails -
        meaning the problem is at the context/session level, not this one
        stale page - does this escalate to warmup_service.force_reconnect()
        and raise RetryableBrowserError, which trade_coordinator.
        handle_signal already retries once with a freshly acquired worker;
        that retry's own acquire_worker() call goes through
        _ensure_pool_healthy() first, which will see the bumped generation
        and rebuild every worker from the new context. The full browser is
        therefore only ever recreated as a last resort, never as the first
        response to one worker's page."""
        result = await self._health_manager.check_worker(worker, self.warmup_service.verification_class)
        if result.healthy:
            return worker

        logger.warning(
            "browser_worker_pool: worker_id=%s unhealthy (%s: %s) - respawning its page",
            worker.worker_id, result.failed_check, result.detail,
        )
        await self._respawn_worker_page(worker)

        recheck = await self._health_manager.check_worker(worker, self.warmup_service.verification_class, force_deep=True)
        if recheck.healthy:
            return worker

        logger.error(
            "browser_worker_pool: worker_id=%s still unhealthy after respawn (%s: %s) - "
            "escalating to a full browser reconnect",
            worker.worker_id, recheck.failed_check, recheck.detail,
        )
        await self.warmup_service.force_reconnect(
            f"worker_id={worker.worker_id} failed {recheck.failed_check} even after a fresh page: {recheck.detail}",
        )
        raise RetryableBrowserError(
            f"worker_id={worker.worker_id} unrecoverable at the page level ({recheck.failed_check}: "
            f"{recheck.detail}) - browser reconnect triggered, retry with a fresh worker",
        )

    async def stop(self):
        for worker in self.workers:
            try:
                await worker.page.close()
            except Exception as e:
                logger.error("browser_worker_pool: error closing worker_id=%s: %s", worker.worker_id, e)
