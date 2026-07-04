import asyncio
import logging
import sys
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"

sys.path.insert(0, str(EXECUTION_DIR))

from browser_session import PocketBrowserSession, get_trading_page, DEMO_URL
import pocket_dom
import asset_cache

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class DemoModeVerificationError(Exception):
    pass


class BrowserWarmupService:
    """
    Long-lived, single persistent Pocket Option browser context. Launches
    once at AXIM startup and stays open for the life of the process.

    Owns the shared browser context, demo-mode verification, and the
    asset cache scan (all context/account-level facts, not per-page).
    Trade execution itself now happens through execution/browser_worker_pool.py's
    BrowserWorkerPool, which opens additional pages (tabs) from
    get_context() - each with its own lock, enabling real concurrency.
    This service's own `get_page()`/`.lock` (the bootstrap page) remain
    available for single-page use (e.g. the asset cache scan) but are no
    longer the primary trade-execution interface.
    """

    def __init__(self):
        self._session = None
        self._context = None
        self._page = None
        self.lock = asyncio.Lock()

    async def start(self):
        self._session = PocketBrowserSession()
        self._context = await self._session.__aenter__()
        self._page = await get_trading_page(self._context, DEMO_URL)
        await pocket_dom.dismiss_blocking_modals(self._page)
        await self._verify_demo_mode()
        logger.info("browser_warmup: session started and verified (demo mode)")

        await asset_cache.build_cache(self._page)

    async def _verify_demo_mode(self):
        is_demo = await self._page.evaluate("() => document.body.classList.contains('is-chart-demo')")
        if not is_demo:
            logger.error("browser_warmup: page is NOT showing demo mode - refusing to proceed")
            raise DemoModeVerificationError(
                "Pocket Option page is not showing demo mode (is-chart-demo class missing on <body>)"
            )
        logger.info("browser_warmup: demo mode verified (is-chart-demo present)")

    async def health_check(self):
        try:
            await asyncio.wait_for(self._page.evaluate("() => 1"), timeout=3)
            return True
        except Exception as e:
            logger.warning("browser_warmup: health check failed: %s", e)
            return False

    async def get_page(self):
        if self._page is None or self._page.is_closed() or not await self.health_check():
            await self._reconnect()
        return self._page

    def get_context(self):
        return self._context

    async def _reconnect(self):
        logger.warning("browser_warmup: reconnecting after crashed/closed page")
        await self.stop()
        await self.start()

    async def stop(self):
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as e:
                logger.error("browser_warmup: error closing session: %s", e)
        self._session = None
        self._context = None
        self._page = None
