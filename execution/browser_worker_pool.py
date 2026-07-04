import asyncio
import sys
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

from browser_session import DEMO_URL, get_trading_page
import pocket_dom
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class BrowserWorker:
    def __init__(self, worker_id, page, generation):
        self.worker_id = worker_id
        self.page = page
        self.lock = asyncio.Lock()
        # Which pool rebuild this worker belongs to - lets release_worker
        # detect and discard a worker whose page belongs to a browser
        # context that no longer exists (see BrowserWorkerPool docstring).
        self.generation = generation


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
        async with self._health_lock:
            current_generation = await self.warmup_service.ensure_alive()
            if current_generation != self._warmup_generation:
                logger.warning(
                    "browser_worker_pool: underlying browser reconnected "
                    "(warmup generation %s -> %s) - rebuilding all workers "
                    "from the new browser context",
                    self._warmup_generation, current_generation,
                )
                self._warmup_generation = current_generation
                await self._build_workers()

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
        return worker

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

    async def _ensure_worker_healthy(self, worker):
        """Handles a single tab dying while the rest of the browser is
        fine - the whole-browser-crash case is caught earlier, in
        _ensure_pool_healthy(), before a worker is even pulled from the
        queue."""
        try:
            if worker.page.is_closed():
                raise RuntimeError("page closed")
            await asyncio.wait_for(worker.page.evaluate("() => 1"), timeout=3)
            return worker
        except Exception as e:
            logger.warning(
                "browser_worker_pool: worker_id=%s unhealthy (%s) - respawning its page",
                worker.worker_id, e,
            )
            context = self.warmup_service.get_context()
            new_page = await get_trading_page(context, DEMO_URL)
            await pocket_dom.dismiss_blocking_modals(new_page)
            worker.page = new_page
            return worker

    async def stop(self):
        for worker in self.workers:
            try:
                await worker.page.close()
            except Exception as e:
                logger.error("browser_worker_pool: error closing worker_id=%s: %s", worker.worker_id, e)
