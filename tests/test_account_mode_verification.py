import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import account_mode_verification as mode_verification

SELECTOR = ".type-of-trade-label--real"
TEXT = "You are trading on Real account"
DEMO_CLASS = "is-chart-demo"
LIVE_URL = "https://pocketoption.com/en/cabinet/quick-high-low/"


def _run(coro):
    return asyncio.run(coro)


def _live_probe(url=LIVE_URL, demo_class_present=False, match_count=1,
                 visible=True, text=TEXT, class_name="type-of-trade-label type-of-trade-label--real"):
    out = {"url": url, "demo_class_present": demo_class_present, "match_count": match_count}
    if match_count == 1:
        out.update({"visible": visible, "text": text, "class_name": class_name})
    return out


def _mock_page(evaluate_return):
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=evaluate_return)
    return page


class VerifyDemoModeTests(unittest.TestCase):
    def test_passes_when_class_present(self):
        page = _mock_page(True)
        result = _run(mode_verification.verify_demo_mode(page, DEMO_CLASS))
        self.assertTrue(result.passed)
        self.assertEqual(result.mode, "demo")
        self.assertTrue(result.checks["demo_class_present"])

    def test_fails_when_class_missing(self):
        page = _mock_page(False)
        result = _run(mode_verification.verify_demo_mode(page, DEMO_CLASS))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "demo_class_present")
        self.assertFalse(result.checks["demo_class_present"])

    def test_fails_closed_on_page_evaluate_exception(self):
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("target closed"))
        result = _run(mode_verification.verify_demo_mode(page, DEMO_CLASS))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "page_evaluate")


class VerifyLiveModeTests(unittest.TestCase):
    """Covers execution/account_mode_verification.py's verify_live_mode -
    the 2026-07-31 fix replacing the old (structurally broken, since a
    real live cabinet has no demo/real/live class on <body>) body-class
    assumption with a positive, multi-condition, fail-closed check against
    the real element a live cabinet inspection confirmed:
    <div class="type-of-trade-label type-of-trade-label--real">You are
    trading on Real account</div>."""

    def _verify(self, probe, broker_account_id=None, account_lookup=None):
        page = _mock_page(probe)
        return _run(mode_verification.verify_live_mode(
            page, selector=SELECTOR, expected_text=TEXT, demo_class=DEMO_CLASS,
            live_url=LIVE_URL, broker_account_id=broker_account_id, account_lookup=account_lookup,
        ))

    def test_passes_with_correct_url_visible_element_exact_text_no_demo_class(self):
        result = self._verify(_live_probe())
        self.assertTrue(result.passed, result.as_log_dict())
        self.assertEqual(result.mode, "live")
        self.assertTrue(all(result.checks.values()))

    def test_passes_and_checks_broker_account_when_lookup_given(self):
        result = self._verify(_live_probe(), broker_account_id=13, account_lookup=lambda _id: True)
        self.assertTrue(result.passed)
        self.assertTrue(result.checks["broker_account_marked_live"])

    def test_fails_when_element_absent(self):
        result = self._verify(_live_probe(match_count=0))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "selector_absent")

    def test_fails_when_element_hidden(self):
        result = self._verify(_live_probe(visible=False))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "element_hidden")

    def test_fails_when_text_wrong(self):
        result = self._verify(_live_probe(text="You are trading on Demo account"))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "text_mismatch")

    def test_fails_when_demo_class_also_present(self):
        result = self._verify(_live_probe(demo_class_present=True))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "demo_class_present")

    def test_fails_on_wrong_url(self):
        result = self._verify(_live_probe(url="https://pocketoption.com/en/cabinet/demo-quick-high-low/"))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "url_mismatch")

    def test_fails_when_account_configuration_says_demo(self):
        result = self._verify(_live_probe(), broker_account_id=13, account_lookup=lambda _id: False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "account_not_live")

    def test_generic_element_elsewhere_with_real_in_it_does_not_pass(self):
        # The selector is exact (".type-of-trade-label--real") - a stray
        # unrelated element containing "real" in its text/class somewhere
        # else on the page simply never matches the querySelectorAll call
        # in the first place, so it can never masquerade as the real
        # verification target. Simulated here via match_count staying 0
        # (selector found nothing at the configured, specific target).
        result = self._verify(_live_probe(match_count=0))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "selector_absent")

    def test_multiple_conflicting_indicators_fail_closed(self):
        result = self._verify(_live_probe(match_count=2))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "selector_ambiguous")

    def test_fails_when_element_missing_expected_class(self):
        result = self._verify(_live_probe(class_name="type-of-trade-label type-of-trade-label--demo"))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "class_missing")

    def test_fails_closed_when_not_configured(self):
        page = _mock_page(_live_probe())
        result = _run(mode_verification.verify_live_mode(
            page, selector=None, expected_text=None, demo_class=DEMO_CLASS, live_url=LIVE_URL,
        ))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "not_configured")

    def test_account_lookup_exception_fails_closed(self):
        def boom(_id):
            raise RuntimeError("db unavailable")
        result = self._verify(_live_probe(), broker_account_id=13, account_lookup=boom)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "account_lookup_failed")

    def test_fails_closed_on_page_evaluate_exception(self):
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("target closed"))
        result = _run(mode_verification.verify_live_mode(
            page, selector=SELECTOR, expected_text=TEXT, demo_class=DEMO_CLASS, live_url=LIVE_URL,
        ))
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "page_evaluate")


class LoggingSafetyTests(unittest.TestCase):
    """No credentials or session secrets ever appear in a loggable result -
    as_log_dict() is built exclusively from booleans, check names, and
    this module's own short detail strings (selector/expected-vs-actual
    text/URL), never page HTML/cookies/tokens."""

    def test_log_dict_shape_is_limited_to_safe_fields(self):
        page = _mock_page(_live_probe())
        result = _run(mode_verification.verify_live_mode(
            page, selector=SELECTOR, expected_text=TEXT, demo_class=DEMO_CLASS, live_url=LIVE_URL,
        ))
        log_dict = result.as_log_dict()
        self.assertEqual(set(log_dict.keys()), {"mode", "passed", "checks", "failed_check", "detail"})
        self.assertTrue(result.passed)
        self.assertIsNone(log_dict["failed_check"])
        self.assertIsNone(log_dict["detail"])
        rendered = str(log_dict).lower()
        for forbidden in ("cookie", "token", "password", "secret"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
