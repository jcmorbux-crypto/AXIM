import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        # Hardcoded to a temp dir, so this is safe today regardless - but
        # this is still a real launch_persistent_context call site that
        # bypasses PocketBrowserSession entirely, so it goes through the
        # same centralized guard as every other one in this codebase (see
        # execution/browser_session.py's guard_against_production_profile_
        # in_tests docstring) rather than relying on "this one happens to
        # already be safe" as the only thing stopping a future edit here
        # from pointing at the real profile.
        profile_dir = Path(self._tmp_dir.name) / "profile"
        browser_session.guard_against_production_profile_in_tests(profile_dir)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
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


class ProductionProfileGuardTests(unittest.TestCase):
    """execution/browser_session.py's hard safety guard (2026-07-31,
    following a verified incident where the production browser profile
    was found under contention during a full-suite pytest run) - a test
    process must never be able to construct a PocketBrowserSession
    against the real production Pocket Option profile, whether directly,
    via a relative-path trick, or via a child path inside it. See
    guard_against_production_profile_in_tests's own docstring for the
    full mechanism and the one deliberate, pre-existing exception."""

    def test_exact_production_path_is_rejected(self):
        with self.assertRaises(browser_session.ProductionProfileInTestError):
            browser_session.PocketBrowserSession(user_data_dir=browser_session.USER_DATA_DIR)

    def test_relative_path_resolving_to_production_is_rejected(self):
        relative = browser_session.PROJECT_ROOT / "sessions" / ".." / "sessions" / "pocket_browser"
        with self.assertRaises(browser_session.ProductionProfileInTestError):
            browser_session.PocketBrowserSession(user_data_dir=relative)

    def test_child_path_inside_production_profile_is_rejected(self):
        child = browser_session.USER_DATA_DIR / "Default"
        with self.assertRaises(browser_session.ProductionProfileInTestError):
            browser_session.PocketBrowserSession(user_data_dir=child)

    def test_temporary_profile_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Must not raise - construction alone doesn't launch anything.
            session = browser_session.PocketBrowserSession(user_data_dir=Path(tmp) / "profile")
            self.assertEqual(session.user_data_dir, Path(tmp) / "profile")

    def test_each_temporary_profile_call_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            session1 = browser_session.PocketBrowserSession(user_data_dir=Path(tmp1) / "profile")
            session2 = browser_session.PocketBrowserSession(user_data_dir=Path(tmp2) / "profile")
            self.assertNotEqual(session1.user_data_dir, session2.user_data_dir)

    def test_guard_is_a_noop_outside_pytest(self):
        # Simulates a real (non-test) process - must not raise even for the
        # exact production path, since this is the listener's own
        # legitimate, intended use. patch.dict restores sys.modules
        # afterward regardless of the mutation inside.
        with patch.dict(sys.modules):
            sys.modules.pop("pytest", None)
            browser_session.guard_against_production_profile_in_tests(browser_session.USER_DATA_DIR)

    def test_guard_defers_to_the_existing_dryrun_opt_in(self):
        # The one deliberate, pre-existing, human-opted-in exception
        # (tests/test_pocket_execution_dryrun.py) - must not raise.
        with patch.dict(os.environ, {"AXIM_RUN_LIVE_DOM_TESTS": "true"}):
            browser_session.guard_against_production_profile_in_tests(browser_session.USER_DATA_DIR)


class ContextManagerCleanupTests(unittest.TestCase):
    """__aexit__ must close the real browser context/playwright instance
    whether the `async with` body succeeded, raised, or was cancelled
    (asyncio's own timeout machinery cancels via exception, so this same
    guarantee covers a timed-out test too) - a leaked browser process from
    any of those three paths is exactly the kind of orphan that could
    later collide with a legitimate process for the same profile."""

    def test_context_is_closed_after_a_normal_exit(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                session = browser_session.PocketBrowserSession(
                    user_data_dir=Path(tmp) / "profile", headless=True,
                )
                async with session as context:
                    self.assertFalse(context.pages[0].is_closed())
                self.assertTrue(session._context is not None)  # object retained
                # is_closed() on a context isn't exposed directly by Playwright,
                # so the strongest available proof is that a page from it now
                # reports closed.
                self.assertTrue(context.pages == [] or context.pages[0].is_closed())
        _run(scenario())

    def test_context_is_still_closed_when_the_body_raises(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                session = browser_session.PocketBrowserSession(
                    user_data_dir=Path(tmp) / "profile", headless=True,
                )
                context_ref = {}
                with self.assertRaises(RuntimeError):
                    async with session as context:
                        context_ref["context"] = context
                        raise RuntimeError("simulated test failure mid-session")
                context = context_ref["context"]
                self.assertTrue(context.pages == [] or context.pages[0].is_closed())
        _run(scenario())

    def test_context_is_still_closed_on_a_real_timeout_cancellation(self):
        # asyncio.TimeoutError/CancelledError derive from BaseException, not
        # Exception, since Python 3.8 - a bare "except Exception" inside
        # __aexit__ would silently NOT run on a real pytest-timeout/
        # asyncio.wait_for timeout. __aexit__ here has no try/except at all
        # (see execution/browser_session.py) and relies on `async with`'s
        # own language guarantee that __aexit__ always runs on any exit
        # path - this test proves that guarantee holds for a REAL
        # asyncio.wait_for timeout, not just an ordinary exception, since a
        # timed-out test is exactly the third cleanup scenario RC1 requires
        # (pass / fail / timeout) and the other two tests above don't cover it.
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                session = browser_session.PocketBrowserSession(
                    user_data_dir=Path(tmp) / "profile", headless=True,
                )
                context_ref = {}

                async def hang_forever(context):
                    context_ref["context"] = context
                    await asyncio.sleep(3600)

                with self.assertRaises(asyncio.TimeoutError):
                    async with session as context:
                        await asyncio.wait_for(hang_forever(context), timeout=0.05)
                context = context_ref["context"]
                self.assertTrue(context.pages == [] or context.pages[0].is_closed())
        _run(scenario())


if __name__ == "__main__":
    unittest.main()
