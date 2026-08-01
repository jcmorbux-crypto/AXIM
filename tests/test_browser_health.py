import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import browser_health
from browser_health import BrowserHealthManager, HealthCheckResult
from browser_worker_pool import BrowserWorker


class FakeLocator:
    def __init__(self, visible=True):
        self.first = self
        self._visible = visible

    async def is_visible(self, timeout=None):
        return self._visible


class FakePage:
    def __init__(self, closed=False, evaluate_result=1, dom_visible=True):
        self._closed = closed
        self._evaluate_result = evaluate_result
        self._dom_visible = dom_visible
        self.evaluate_calls = 0

    def is_closed(self):
        return self._closed

    async def evaluate(self, *args, **kwargs):
        self.evaluate_calls += 1
        return self._evaluate_result

    def locator(self, selector):
        return FakeLocator(visible=self._dom_visible)


def _run(coro):
    return asyncio.run(coro)


def _worker(page):
    return BrowserWorker(0, page, generation=1)


class PageResponsiveTests(unittest.TestCase):
    def test_closed_page_fails_immediately(self):
        mgr = BrowserHealthManager()
        worker = _worker(FakePage(closed=True))
        result = _run(mgr.check_worker(worker, "is-chart-demo"))
        self.assertFalse(result.healthy)
        self.assertEqual(result.failed_check, "page_responsive")

    def test_within_ttl_skips_the_evaluate_probe(self):
        # Prime _last_deep_check first (via one real deep check) so the
        # second call below is shallow-only - isolates the responsive-probe
        # TTL from the separate deep-check TTL, which also calls evaluate()
        # (for the session-authenticated check) and would otherwise make
        # this assertion meaningless.
        mgr = BrowserHealthManager()
        page = FakePage()
        worker = _worker(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)):
            _run(mgr.check_worker(worker, "is-chart-demo"))
        page.evaluate_calls = 0
        worker.last_health_check = time.monotonic()  # "just checked" - within the responsive TTL now
        _run(mgr.check_worker(worker, "is-chart-demo"))
        self.assertEqual(page.evaluate_calls, 0)

    def test_force_deep_bypasses_the_responsive_ttl(self):
        mgr = BrowserHealthManager()
        page = FakePage()
        worker = _worker(page)
        worker.last_health_check = time.monotonic()
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)):
            _run(mgr.check_worker(worker, "is-chart-demo", force_deep=True))
        self.assertGreaterEqual(page.evaluate_calls, 1)


class DeepCheckTests(unittest.TestCase):
    def setUp(self):
        self.mgr = BrowserHealthManager()

    def _worker_due_for_deep_check(self, page):
        worker = _worker(page)
        return worker

    def test_dom_not_ready_fails_the_deep_check(self):
        page = FakePage(dom_visible=False)
        worker = self._worker_due_for_deep_check(page)
        result = _run(self.mgr.check_worker(worker, "is-chart-demo"))
        self.assertFalse(result.healthy)
        self.assertEqual(result.failed_check, "dom_ready")

    def test_session_class_missing_fails_the_deep_check(self):
        page = FakePage(evaluate_result=False)  # every evaluate() call, including the session-class check, returns falsy
        worker = self._worker_due_for_deep_check(page)
        result = _run(self.mgr.check_worker(worker, "is-chart-demo"))
        self.assertFalse(result.healthy)
        self.assertEqual(result.failed_check, "session_authenticated")

    def test_unreadable_balance_fails_the_deep_check(self):
        page = FakePage()
        worker = self._worker_due_for_deep_check(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=None)):
            result = _run(self.mgr.check_worker(worker, "is-chart-demo"))
        self.assertFalse(result.healthy)
        self.assertEqual(result.failed_check, "live_data_flowing")

    def test_all_checks_passing_is_healthy_and_records_the_deep_check_timestamp(self):
        page = FakePage()
        worker = self._worker_due_for_deep_check(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)):
            result = _run(self.mgr.check_worker(worker, "is-chart-demo"))
        self.assertTrue(result.healthy)
        self.assertIn(worker.worker_id, self.mgr._last_deep_check)

    def test_within_deep_ttl_the_deep_checks_are_skipped(self):
        page = FakePage()
        worker = self._worker_due_for_deep_check(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)) as mock_balance:
            _run(self.mgr.check_worker(worker, "is-chart-demo"))  # first call - due, runs deep checks
            mock_balance.reset_mock()
            result = _run(self.mgr.check_worker(worker, "is-chart-demo"))  # second call - within TTL now
        self.assertTrue(result.healthy)
        mock_balance.assert_not_called()

    def test_note_page_replaced_forces_the_next_check_to_go_deep_again(self):
        page = FakePage()
        worker = self._worker_due_for_deep_check(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)) as mock_balance:
            _run(self.mgr.check_worker(worker, "is-chart-demo"))
            self.mgr.note_page_replaced(worker.worker_id)
            mock_balance.reset_mock()
            _run(self.mgr.check_worker(worker, "is-chart-demo"))
        mock_balance.assert_called_once()


class LiveModeDeepCheckTests(unittest.TestCase):
    """check_worker/_check_session_authenticated dispatch on the TYPE of
    the config passed in (see BrowserWarmupService.verification_config) -
    a bare string still means "demo, check this body class" (unchanged,
    covered above); a dict means "live" and is delegated to
    account_mode_verification.verify_live_mode instead of ever treating
    it as a body class (2026-07-31 live-verification fix)."""

    LIVE_CONFIG = {
        "mode": "live",
        "selector": ".type-of-trade-label--real",
        "expected_text": "You are trading on Real account",
        "demo_class": "is-chart-demo",
        "live_url": "https://pocketoption.com/en/cabinet/quick-high-low/",
        "broker_account_id": None,
        "account_lookup": None,
    }

    def _live_probe(self, text="You are trading on Real account"):
        return {
            "url": self.LIVE_CONFIG["live_url"], "demo_class_present": False, "match_count": 1,
            "visible": True, "text": text, "class_name": "type-of-trade-label type-of-trade-label--real",
        }

    def setUp(self):
        self.mgr = BrowserHealthManager()

    def test_live_config_passing_probe_is_healthy(self):
        page = FakePage(evaluate_result=self._live_probe())
        worker = _worker(page)
        with patch.object(browser_health.pocket_dom, "read_balance", new=AsyncMock(return_value=1000.0)):
            result = _run(self.mgr.check_worker(worker, self.LIVE_CONFIG))
        self.assertTrue(result.healthy)

    def test_live_config_wrong_text_fails_session_authenticated(self):
        page = FakePage(evaluate_result=self._live_probe(text="You are trading on Demo account"))
        worker = _worker(page)
        result = _run(self.mgr.check_worker(worker, self.LIVE_CONFIG))
        self.assertFalse(result.healthy)
        self.assertEqual(result.failed_check, "session_authenticated")
        self.assertIn("text_mismatch", result.detail)


class HealthCheckResultTests(unittest.TestCase):
    def test_repr_of_healthy_result(self):
        self.assertEqual(repr(HealthCheckResult(True)), "HealthCheckResult(healthy=True)")

    def test_repr_of_unhealthy_result_includes_check_and_detail(self):
        r = HealthCheckResult(False, "dom_ready", "asset trigger not visible")
        self.assertIn("dom_ready", repr(r))
        self.assertIn("asset trigger not visible", repr(r))


if __name__ == "__main__":
    unittest.main()
