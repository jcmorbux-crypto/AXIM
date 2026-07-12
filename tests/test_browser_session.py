import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import browser_session
import pocket_dom


def _run(coro):
    return asyncio.run(coro)


# get_trading_page waits for pocket_dom.SEL_ASSET_TRIGGER to become visible
# before returning - a real Pocket Option page detail, not something these
# tests should depend on hitting over the network. A self-contained data:
# URL with a matching element satisfies that wait instantly and offline.
# Asserted (not just assumed) to be a plain ".class-name" selector, since
# that's what this construction requires - fails loudly here rather than
# silently building a data: URL that could never actually match if
# SEL_ASSET_TRIGGER ever changes to something more complex.
assert pocket_dom.SEL_ASSET_TRIGGER.startswith(".") and pocket_dom.SEL_ASSET_TRIGGER.count(".") == 1, (
    f"SEL_ASSET_TRIGGER {pocket_dom.SEL_ASSET_TRIGGER!r} is no longer a simple class selector - "
    f"update _TEST_PAGE_URL's construction below to match"
)
_TEST_PAGE_URL = f'data:text/html,<div class="{pocket_dom.SEL_ASSET_TRIGGER[1:]}">t</div>'


class GetTradingPageRealBrowserTests(unittest.TestCase):
    """Every other test in this suite mocks around real Playwright/browser
    interaction (established convention - see test_browser_worker_pool.py's
    FakePage/patched get_trading_page). This file is a deliberate exception:
    a real bug in get_trading_page's page-selection logic (context.pages[0]
    if context.pages else context.new_page()) meant every call after the
    very first one against a given context silently returned that SAME
    page object, since launch_persistent_context auto-opens one blank page
    and context.pages never goes back to empty - confirmed by direct
    Playwright object-identity testing, not assumed from reading the code.
    That meant every BrowserWorkerPool worker beyond the first, plus
    BrowserWarmupService's own dedicated page, secretly shared one browser
    tab - each worker's own asyncio.Lock protected nothing real, since it
    guards the worker, not the page multiple workers actually shared.

    A mock can't catch this class of bug - it only exists in real
    Playwright/Chromium object-identity semantics, not in anything this
    codebase's own logic controls. Uses a real headless Chromium instance
    (already a project dependency) against a throwaway temp profile
    directory, isolated per test and cleaned up after."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp_dir.cleanup()

    async def _fresh_context(self):
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path(self._tmp_dir.name) / "profile"),
            headless=True,
            no_viewport=True,
        )
        return self._context

    async def _close_context(self):
        await self._context.close()
        await self._playwright.stop()

    def test_fresh_persistent_context_auto_opens_exactly_one_page(self):
        # The premise the whole bug rested on - confirmed directly rather
        # than assumed, since Playwright's own documented behavior here is
        # exactly what made context.pages never reliably empty again.
        async def scenario():
            context = await self._fresh_context()
            try:
                self.assertEqual(len(context.pages), 1)
            finally:
                await self._close_context()
        _run(scenario())

    def test_reuse_existing_true_reuses_the_auto_opened_page(self):
        async def scenario():
            context = await self._fresh_context()
            try:
                page = await browser_session.get_trading_page(
                    context, url=_TEST_PAGE_URL, ready_timeout=3000, reuse_existing=True,
                )
                self.assertIs(page, context.pages[0])
                self.assertEqual(len(context.pages), 1)
            finally:
                await self._close_context()
        _run(scenario())

    def test_default_always_creates_a_genuinely_new_page(self):
        async def scenario():
            context = await self._fresh_context()
            try:
                page1 = await browser_session.get_trading_page(context, url=_TEST_PAGE_URL, ready_timeout=3000)
                page2 = await browser_session.get_trading_page(context, url=_TEST_PAGE_URL, ready_timeout=3000)
                self.assertIsNot(page1, page2)
                # The original auto-opened blank tab, plus these two -
                # three total, not one shared across every call.
                self.assertEqual(len(context.pages), 3)
            finally:
                await self._close_context()
        _run(scenario())

    def test_reuse_existing_true_then_false_does_not_collide(self):
        # Mirrors the real call sequence: BrowserWarmupService.start()
        # (reuse_existing=True) always runs before BrowserWorkerPool builds
        # its own workers (reuse_existing=False, the default) against that
        # SAME context - the exact interaction the real bug corrupted.
        async def scenario():
            context = await self._fresh_context()
            try:
                warmup_page = await browser_session.get_trading_page(
                    context, url=_TEST_PAGE_URL, ready_timeout=3000, reuse_existing=True,
                )
                worker_0_page = await browser_session.get_trading_page(context, url=_TEST_PAGE_URL, ready_timeout=3000)
                worker_1_page = await browser_session.get_trading_page(context, url=_TEST_PAGE_URL, ready_timeout=3000)
                pages = [warmup_page, worker_0_page, worker_1_page]
                self.assertEqual(len(set(id(p) for p in pages)), 3)
            finally:
                await self._close_context()
        _run(scenario())


if __name__ == "__main__":
    unittest.main()
