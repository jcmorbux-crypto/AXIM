import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import browser_warmup
from browser_warmup import BrowserWarmupService, LiveModeNotConfiguredError, DemoModeVerificationError


def _run(coro):
    return asyncio.run(coro)


class BrowserWarmupModeTests(unittest.TestCase):
    """Covers the mode-aware cabinet selection added alongside the
    live-trading gating work - which URL gets loaded and which DOM class
    gets checked, for 'demo' (default, proven) vs 'live' (structurally
    refuses to guess - see LiveModeNotConfiguredError). Mocks the
    Playwright-facing pieces (PocketBrowserSession, get_trading_page,
    dismiss_blocking_modals, asset cache) since this suite tests the
    mode-selection LOGIC, not a real browser - manual live-fire scripts
    (tests/manual_click_test_warm.py etc.) cover the real thing."""

    def _mock_page(self, verification_class_present):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=verification_class_present)
        return page

    def _run_start_with_mocks(self, warmup, page, target_urls):
        """Patches every Playwright-facing dependency of start() with a
        plain (non-async) side_effect that just records the URL and
        returns the mock page directly - using an async helper here
        would return an unawaited coroutine as the "page", since
        get_trading_page is itself detected as async and auto-wrapped
        as AsyncMock by patch()."""
        def fake_get_trading_page(ctx, url):
            target_urls.append(url)
            return page

        with patch("browser_warmup.PocketBrowserSession") as MockSession, \
             patch("browser_warmup.get_trading_page", side_effect=fake_get_trading_page), \
             patch("browser_warmup.pocket_dom.dismiss_blocking_modals", new=AsyncMock()):
            session_instance = MockSession.return_value
            session_instance.__aenter__ = AsyncMock(return_value=MagicMock())
            _run(warmup.start())

    def test_default_mode_uses_demo_url_and_verifies_is_chart_demo(self):
        target_urls = []
        page = self._mock_page(verification_class_present=True)
        warmup = BrowserWarmupService()
        warmup.asset_cache.build_cache = AsyncMock()

        self._run_start_with_mocks(warmup, page, target_urls)

        self.assertEqual(target_urls, [browser_warmup.DEMO_URL])
        page.evaluate.assert_awaited_once()
        # Second positional arg to page.evaluate is the class name checked.
        self.assertEqual(page.evaluate.await_args.args[1], "is-chart-demo")

    def test_live_mode_without_configured_url_raises_before_touching_browser(self):
        warmup = BrowserWarmupService(mode="live")
        with patch("browser_warmup.LIVE_URL", None), \
             patch("browser_warmup.LIVE_MODE_VERIFICATION_CLASS", None), \
             patch("browser_warmup.PocketBrowserSession") as MockSession:
            with self.assertRaises(LiveModeNotConfiguredError):
                _run(warmup.start())
            MockSession.assert_not_called()  # fails BEFORE any browser action

    def test_live_mode_with_configured_url_uses_it_and_verifies_configured_class(self):
        target_urls = []
        page = self._mock_page(verification_class_present=True)
        warmup = BrowserWarmupService(mode="live")
        warmup.asset_cache.build_cache = AsyncMock()

        with patch("browser_warmup.LIVE_URL", "https://example.test/live-cabinet/"), \
             patch("browser_warmup.LIVE_MODE_VERIFICATION_CLASS", "is-chart-live"):
            self._run_start_with_mocks(warmup, page, target_urls)

        self.assertEqual(target_urls, ["https://example.test/live-cabinet/"])
        self.assertEqual(page.evaluate.await_args.args[1], "is-chart-live")

    def test_verification_failure_raises_demo_mode_verification_error(self):
        target_urls = []
        page = self._mock_page(verification_class_present=False)  # class NOT present
        warmup = BrowserWarmupService()

        with self.assertRaises(DemoModeVerificationError):
            self._run_start_with_mocks(warmup, page, target_urls)


if __name__ == "__main__":
    unittest.main()
