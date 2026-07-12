import sys
from pathlib import Path

from playwright.async_api import async_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pocket_dom

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_DATA_DIR = PROJECT_ROOT / "sessions" / "pocket_browser"
DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"


class PocketBrowserSession:
    def __init__(self, user_data_dir=USER_DATA_DIR, headless=False, viewport="maximize"):
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        # "maximize" (default): no fixed content viewport, window opens
        # maximized to fit whatever the real screen is - fixes the window
        # being sized/positioned past the edges of small/non-1600x1000
        # screens. Pass an explicit {"width": w, "height": h} dict to force
        # a fixed viewport instead (e.g. for headless CI runs).
        self.viewport = viewport
        self._playwright = None
        self._context = None

    async def __aenter__(self):
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()

        if self.viewport == "maximize":
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                no_viewport=True,
                args=["--start-maximized"],
            )
        else:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                viewport=self.viewport,
            )
        return self._context

    async def __aexit__(self, exc_type, exc, tb):
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()


async def get_trading_page(context, url=DEMO_URL, ready_timeout=15000, reuse_existing=False):
    """reuse_existing=True reuses launch_persistent_context's own
    auto-opened blank tab (context.pages[0]) instead of leaving it idle
    and opening a redundant one - correct ONLY for the single call made
    immediately after a fresh context launch (browser_warmup.py's own
    dedicated page). Every other caller (BrowserWorkerPool building or
    respawning a worker) needs a genuinely separate page and must use
    the default False - context.pages stays non-empty forever after the
    first page exists, so `context.pages[0] if context.pages else ...`
    would otherwise hand out that SAME page object to every subsequent
    caller (confirmed empirically, not assumed): each worker's own
    asyncio.Lock would then protect nothing real, since it guards the
    worker, not the page multiple workers secretly shared - two
    concurrently acquired workers could manipulate the identical
    browser tab at once with no real mutual exclusion."""
    page = context.pages[0] if (reuse_existing and context.pages) else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await expect(page.locator(pocket_dom.SEL_ASSET_TRIGGER).first).to_be_visible(timeout=ready_timeout)
    return page
