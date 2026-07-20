import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
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

    def _run_start_with_mocks(self, warmup, page, target_urls, call_kwargs=None):
        """Patches every Playwright-facing dependency of start() with a
        plain (non-async) side_effect that just records the URL and
        returns the mock page directly - using an async helper here
        would return an unawaited coroutine as the "page", since
        get_trading_page is itself detected as async and auto-wrapped
        as AsyncMock by patch()."""
        def fake_get_trading_page(ctx, url, **kwargs):
            target_urls.append(url)
            if call_kwargs is not None:
                call_kwargs.append(kwargs)
            return page

        with patch("browser_warmup.PocketBrowserSession") as MockSession, \
             patch("browser_warmup.get_trading_page", side_effect=fake_get_trading_page), \
             patch("browser_warmup.pocket_dom.dismiss_blocking_modals", new=AsyncMock()):
            session_instance = MockSession.return_value
            session_instance.__aenter__ = AsyncMock(return_value=MagicMock())
            _run(warmup.start())

    def test_default_mode_uses_demo_url_and_verifies_is_chart_demo(self):
        target_urls = []
        call_kwargs = []
        page = self._mock_page(verification_class_present=True)
        warmup = BrowserWarmupService()
        warmup.asset_cache.build_cache = AsyncMock()

        self._run_start_with_mocks(warmup, page, target_urls, call_kwargs)

        self.assertEqual(target_urls, [browser_warmup.DEMO_URL])
        page.evaluate.assert_awaited_once()
        # Second positional arg to page.evaluate is the class name checked.
        self.assertEqual(page.evaluate.await_args.args[1], "is-chart-demo")
        # This is the one call site allowed to reuse launch_persistent_
        # context's own auto-opened blank tab (see get_trading_page's own
        # docstring for why every OTHER caller must not) - a real bug once
        # let every worker beyond the first, plus this service's own page,
        # silently collapse onto that same shared tab.
        self.assertEqual(call_kwargs, [{"reuse_existing": True}])

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


class HealthCheckTests(unittest.TestCase):
    """health_check() is the gate everything else (get_page, ensure_alive,
    _reconnect) relies on to decide whether the browser needs recovering -
    previously exercised only implicitly, and only for the success path,
    via test_default_mode_uses_demo_url_and_verifies_is_chart_demo above."""

    def test_none_page_is_unhealthy(self):
        warmup = BrowserWarmupService()
        warmup._page = None
        self.assertFalse(_run(warmup.health_check()))

    def test_closed_page_is_unhealthy(self):
        warmup = BrowserWarmupService()
        page = MagicMock()
        page.is_closed.return_value = True
        warmup._page = page
        self.assertFalse(_run(warmup.health_check()))

    def test_responsive_page_is_healthy(self):
        warmup = BrowserWarmupService()
        page = MagicMock()
        page.is_closed.return_value = False
        page.evaluate = AsyncMock(return_value=1)
        warmup._page = page
        self.assertTrue(_run(warmup.health_check()))

    def test_evaluate_exception_is_unhealthy(self):
        # The real-world crashed-tab case: page.is_closed() can still say
        # False for a page whose browser process just died, so the actual
        # IPC round trip (page.evaluate) is what catches it.
        warmup = BrowserWarmupService()
        page = MagicMock()
        page.is_closed.return_value = False
        page.evaluate = AsyncMock(side_effect=RuntimeError("target closed"))
        warmup._page = page
        self.assertFalse(_run(warmup.health_check()))


class GetPageAndEnsureAliveTests(unittest.TestCase):
    """get_page() and ensure_alive() are the two public entry points that
    gate a reconnect on health_check() - covers that they skip _reconnect
    when healthy and both trigger + surface its result when not."""

    def test_get_page_healthy_skips_reconnect(self):
        warmup = BrowserWarmupService()
        page = MagicMock()
        warmup._page = page
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=True)), \
             patch.object(warmup, "_reconnect", new=AsyncMock()) as mock_reconnect:
            result = _run(warmup.get_page())
        mock_reconnect.assert_not_called()
        self.assertIs(result, page)

    def test_get_page_unhealthy_reconnects_then_returns_new_page(self):
        warmup = BrowserWarmupService()
        warmup._page = None
        new_page = MagicMock()

        async def fake_reconnect():
            warmup._page = new_page

        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "_reconnect", new=AsyncMock(side_effect=fake_reconnect)) as mock_reconnect:
            result = _run(warmup.get_page())
        mock_reconnect.assert_awaited_once()
        self.assertIs(result, new_page)

    def test_ensure_alive_healthy_returns_generation_without_reconnect(self):
        warmup = BrowserWarmupService()
        warmup.generation = 3
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=True)), \
             patch.object(warmup, "_reconnect", new=AsyncMock()) as mock_reconnect:
            result = _run(warmup.ensure_alive())
        mock_reconnect.assert_not_called()
        self.assertEqual(result, 3)

    def test_ensure_alive_unhealthy_reconnects_and_returns_new_generation(self):
        warmup = BrowserWarmupService()
        warmup.generation = 3

        async def fake_reconnect():
            warmup.generation = 4

        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "_reconnect", new=AsyncMock(side_effect=fake_reconnect)) as mock_reconnect:
            result = _run(warmup.ensure_alive())
        mock_reconnect.assert_awaited_once()
        self.assertEqual(result, 4)


class ReconnectAndStopTests(unittest.TestCase):
    """_reconnect() is the actual recovery action behind a whole-browser
    crash - previously untested despite being, per the Phase 2 hardening
    audit, the most mature recovery code in the system. Real DB (temp
    file) so assertions go through database.get_recovery_event_stats(),
    the same audit trail Settings/Performance surface, not a re-implementation."""

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

    def test_reconnect_skips_stop_and_start_if_already_healthy_after_lock(self):
        # Simulates a concurrent caller having already reconnected while
        # this one waited for _reconnect_lock - the re-check inside the
        # lock (health_check()) finds it's already fine.
        warmup = BrowserWarmupService()
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=True)), \
             patch.object(warmup, "stop", new=AsyncMock()) as mock_stop, \
             patch.object(warmup, "start", new=AsyncMock()) as mock_start:
            _run(warmup._reconnect())
        mock_stop.assert_not_called()
        mock_start.assert_not_called()
        self.assertEqual(self._event_counts("browser_reconnect"), {})

    def test_reconnect_success_calls_stop_before_start_and_records_succeeded(self):
        warmup = BrowserWarmupService()
        warmup.generation = 5
        call_order = []

        async def fake_stop():
            call_order.append("stop")

        async def fake_start():
            call_order.append("start")

        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "stop", new=AsyncMock(side_effect=fake_stop)), \
             patch.object(warmup, "start", new=AsyncMock(side_effect=fake_start)):
            _run(warmup._reconnect())

        self.assertEqual(call_order, ["stop", "start"])
        self.assertEqual(self._event_counts("browser_reconnect"), {"succeeded": 1})

    def test_reconnect_start_failure_records_failed_event_and_reraises(self):
        warmup = BrowserWarmupService()
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "stop", new=AsyncMock()), \
             patch.object(warmup, "start", new=AsyncMock(side_effect=RuntimeError("relaunch failed"))):
            with self.assertRaises(RuntimeError):
                _run(warmup._reconnect())
        self.assertEqual(self._event_counts("browser_reconnect"), {"failed": 1})

    def test_stop_with_active_session_exits_and_clears_state(self):
        warmup = BrowserWarmupService()
        session = MagicMock()
        session.__aexit__ = AsyncMock()
        warmup._session = session
        warmup._context = MagicMock()
        warmup._page = MagicMock()

        _run(warmup.stop())

        session.__aexit__.assert_awaited_once_with(None, None, None)
        self.assertIsNone(warmup._session)
        self.assertIsNone(warmup._context)
        self.assertIsNone(warmup._page)

    def test_stop_with_no_session_is_a_noop(self):
        warmup = BrowserWarmupService()
        _run(warmup.stop())  # must not raise
        self.assertIsNone(warmup._session)

    def test_stop_swallows_aexit_exception_and_still_clears_state(self):
        warmup = BrowserWarmupService()
        session = MagicMock()
        session.__aexit__ = AsyncMock(side_effect=RuntimeError("close failed"))
        warmup._session = session

        _run(warmup.stop())  # must not raise

        self.assertIsNone(warmup._session)


class NeedsReauthTests(unittest.TestCase):
    """A reconnect's mode-verification failing (DemoModeVerificationError,
    not a generic crash/timeout) usually means the persisted Pocket
    Option login session is no longer valid - previously this just
    retried forever via the caller's own backoff with no operator-visible
    signal. Covers the detection in _reconnect() and _flag_needs_reauth's
    own DB/notification side effects."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@axim.local", "pw12345678", role="owner", access_state="active")
        self.account_id = database.create_broker_account("Test Account", mode="demo")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_flag_needs_reauth_updates_account_and_notifies_owner(self):
        warmup = BrowserWarmupService(broker_account_id=self.account_id)
        warmup._flag_needs_reauth("is-chart-demo class missing")

        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "needs_reauth")
        self.assertIn("is-chart-demo class missing", account["last_error"])
        self.assertIsNotNone(account["last_error_at"])

        notifications = database.list_notifications(self.owner_id)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["source"], "browser_warmup:reauth_required")
        self.assertIn("session was logged out", notifications[0]["message"])

    def test_flag_needs_reauth_does_not_raise_without_an_owner(self):
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim_no_owner.db"
        database.initialize_database()
        account_id = database.create_broker_account("Ownerless Account", mode="demo")
        warmup = BrowserWarmupService(broker_account_id=account_id)
        warmup._flag_needs_reauth("boom")  # must not raise even with no owner to notify
        self.assertEqual(database.get_broker_account(account_id)["connection_status"], "needs_reauth")

    def test_reconnect_demo_mode_verification_error_flags_needs_reauth(self):
        warmup = BrowserWarmupService(broker_account_id=self.account_id)
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "stop", new=AsyncMock()), \
             patch.object(warmup, "start", new=AsyncMock(side_effect=browser_warmup.DemoModeVerificationError("mode mismatch"))):
            with self.assertRaises(browser_warmup.DemoModeVerificationError):
                _run(warmup._reconnect())

        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "needs_reauth")
        # Still records the same browser_reconnect/failed event as any
        # other reconnect failure - this is additive, not a replacement.
        self.assertEqual(self._event_counts("browser_reconnect"), {"failed": 1})

    def test_reconnect_generic_error_does_not_flag_needs_reauth(self):
        warmup = BrowserWarmupService(broker_account_id=self.account_id)
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "stop", new=AsyncMock()), \
             patch.object(warmup, "start", new=AsyncMock(side_effect=RuntimeError("crashed"))):
            with self.assertRaises(RuntimeError):
                _run(warmup._reconnect())

        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "disconnected")  # unchanged from create_broker_account's default

    def test_reconnect_demo_mode_verification_error_without_account_id_does_not_touch_db(self):
        # The legacy single-shared-connection path (broker_account_id=None)
        # has no broker_accounts row to attribute this to - must not try.
        warmup = BrowserWarmupService()  # no broker_account_id
        with patch.object(warmup, "health_check", new=AsyncMock(return_value=False)), \
             patch.object(warmup, "stop", new=AsyncMock()), \
             patch.object(warmup, "start", new=AsyncMock(side_effect=browser_warmup.DemoModeVerificationError("mode mismatch"))), \
             patch.object(database, "update_broker_account") as mock_update:
            with self.assertRaises(browser_warmup.DemoModeVerificationError):
                _run(warmup._reconnect())
        mock_update.assert_not_called()

    def _event_counts(self, event_type):
        stats = database.get_recovery_event_stats()
        return {row["outcome"]: row["n"] for row in stats if row["event_type"] == event_type}


if __name__ == "__main__":
    unittest.main()
