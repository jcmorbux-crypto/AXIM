import asyncio
import logging
import sys
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"

sys.path.insert(0, str(EXECUTION_DIR))

from browser_session import DEMO_URL, get_trading_page
import pocket_dom

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class BrowserWorker:
    def __init__(self, worker_id, page):
        self.worker_id = worker_id
        self.page = page
        self.lock = asyncio.Lock()


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
    """

    def __init__(self, warmup_service, num_workers=2):
        self.warmup_service = warmup_service
        self.num_workers = num_workers
        self.workers = []
        self._available = asyncio.Queue()

    async def start(self):
        context = self.warmup_service.get_context()
        for i in range(self.num_workers):
            page = await get_trading_page(context, DEMO_URL)
            await pocket_dom.dismiss_blocking_modals(page)
            worker = BrowserWorker(i, page)
            self.workers.append(worker)
            self._available.put_nowait(worker)
        logger.info("browser_worker_pool: started %d worker(s)", self.num_workers)

    async def acquire_worker(self, timeout=0):
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
        if worker.lock.locked():
            worker.lock.release()
        self._available.put_nowait(worker)

    async def _ensure_worker_healthy(self, worker):
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
