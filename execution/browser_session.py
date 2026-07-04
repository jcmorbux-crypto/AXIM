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


async def get_trading_page(context, url=DEMO_URL, ready_timeout=15000):
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await expect(page.locator(pocket_dom.SEL_ASSET_TRIGGER).first).to_be_visible(timeout=ready_timeout)
    return page
