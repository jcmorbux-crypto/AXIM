import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import pocket_dom


class ExpiryParsingTests(unittest.TestCase):
    """execution/pocket_dom.py's DOM interaction functions have no
    automated coverage (they need a real browser - see the manual
    tests/manual_click_test*.py scripts and docs/AXIM_LIVE_READINESS_CHECKLIST.md's
    known-limitations note). But the pure parsing/formatting helpers
    underneath them have zero dependency on a page and are fully
    testable - this narrows that gap for the parts that actually can be
    covered, without pretending to cover the DOM layer itself."""

    def test_expiry_to_seconds_minutes(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("1 Minute"), 60)
        self.assertEqual(pocket_dom.expiry_to_seconds("5 Minute"), 300)
        self.assertEqual(pocket_dom.expiry_to_seconds("15 Minutes"), 900)

    def test_expiry_to_seconds_seconds(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("30 Second"), 30)
        self.assertEqual(pocket_dom.expiry_to_seconds("45 Seconds"), 45)

    def test_expiry_to_seconds_case_insensitive(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("1 minute"), 60)
        self.assertEqual(pocket_dom.expiry_to_seconds("1 MINUTE"), 60)

    def test_expiry_to_hms_rolls_over_to_hours(self):
        # 90 minutes -> 1h 30m 0s, exercises the hours field select_expiry
        # actually fills - not just the raw total-seconds count.
        self.assertEqual(pocket_dom._expiry_to_hms("90 Minute"), (1, 30, 0))

    def test_expiry_to_seconds_rejects_unparseable(self):
        # The exact case found live in the soak-test log this session
        # (parsers/signal_parser.py's fallback when no expiry pattern
        # matches) - see the "Reject unparseable-expiry signals cleanly"
        # fix in execution/pocket_executor.py, which relies on this
        # raising ValueError rather than silently guessing a duration.
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("Unknown")

    def test_expiry_to_seconds_rejects_empty_and_none(self):
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("")
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds(None)

    def test_expiry_to_seconds_rejects_garbage(self):
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("banana")
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("Minute 5")


class FormatAmountTests(unittest.TestCase):
    def test_whole_numbers_have_no_decimal(self):
        self.assertEqual(pocket_dom._format_amount(1), "1")
        self.assertEqual(pocket_dom._format_amount(10.0), "10")

    def test_fractional_amounts_keep_two_decimals(self):
        self.assertEqual(pocket_dom._format_amount(1.5), "1.50")
        self.assertEqual(pocket_dom._format_amount(2.25), "2.25")

    def test_accepts_string_input(self):
        # set_amount's real callers pass whatever the risk engine computed,
        # which isn't always guaranteed to already be a float.
        self.assertEqual(pocket_dom._format_amount("5"), "5")


class ParseBalanceTextTests(unittest.TestCase):
    """Covers the balance-display feature added this session
    (docs/AXIM_LIVE_READINESS_CHECKLIST.md) - the parsing half of
    read_balance(), split out specifically so it doesn't need a real
    page/browser to test."""

    def test_parses_real_captured_value(self):
        # The exact string observed in logs/failures/*/page.html's
        # data-hd-show attribute - not a fabricated example.
        self.assertEqual(pocket_dom._parse_balance_text("49,973.92"), 49973.92)

    def test_strips_currency_symbol(self):
        self.assertEqual(pocket_dom._parse_balance_text("$1,000.00"), 1000.0)

    def test_handles_small_values_without_separators(self):
        self.assertEqual(pocket_dom._parse_balance_text("4.87"), 4.87)
        self.assertEqual(pocket_dom._parse_balance_text("0"), 0.0)

    def test_empty_or_none_returns_none_not_zero(self):
        # Never fabricate a balance of $0 from a missing reading.
        self.assertIsNone(pocket_dom._parse_balance_text(""))
        self.assertIsNone(pocket_dom._parse_balance_text(None))

    def test_masked_privacy_toggle_text_raises(self):
        # Pocket Option's "hide balance" toggle renders literal asterisks
        # ("*******") as the visible text - read_balance prefers the
        # data-hd-show attribute specifically to avoid ever parsing this,
        # but if it somehow got passed through, it must fail loudly
        # (ValueError, caught non-fatally by read_balance), never be
        # silently parsed as some fabricated number.
        with self.assertRaises(ValueError):
            pocket_dom._parse_balance_text("*******")


class _FakeExpectation:
    """Stands in for playwright.async_api.expect(locator) - only
    to_be_visible() is ever awaited by the code under test here."""
    async def to_be_visible(self, timeout=None):
        return None


def _make_page(closed_items=None, click_side_effect=None, evaluate_side_effect=None):
    """A fake page matching exactly the locator/get_by_text/click/evaluate
    chain both wait_for_trade_result and find_closed_trade_by_criteria
    use - real Playwright objects aren't available in unit tests (see
    ExpiryParsingTests' own docstring on that gap), but this narrow
    surface is small and stable enough to fake directly."""
    page = MagicMock()
    closed_tab = MagicMock()
    closed_tab.click = AsyncMock(side_effect=click_side_effect)
    page.locator.return_value.get_by_text.return_value.first = closed_tab
    page.locator.return_value.first = MagicMock()
    if evaluate_side_effect is not None:
        page.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    else:
        page.evaluate = AsyncMock(return_value=closed_items or [])
    return page


def _make_warmup(pages):
    warmup = MagicMock()
    warmup.get_page = AsyncMock(side_effect=pages)
    warmup.ensure_alive = AsyncMock()
    warmup.outcome_lock = asyncio.Lock()
    return warmup


class WaitForTradeResultTransientRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """2026-07-29 verified production incident (series 105): a browser
    crash/reconnect mid-read here previously propagated straight past
    wait_for_trade_result with no handling at all, leaving the trade
    'error' and invisible to every recovery path (see database.
    get_unresolved_submitted_trades' own docstring). These cover the fix:
    one retry, after confirming recovery via warmup_service.ensure_alive(),
    for exactly the two points in the read where series 105-style crashes
    are possible - and confirm a genuine (non-transient) bug is still
    never silently retried."""

    def setUp(self):
        self._patch_tab = patch.object(pocket_dom, "_ensure_opened_tab_active", new=AsyncMock(return_value=True))
        self._patch_probe = patch.object(pocket_dom, "_probe_state", new=AsyncMock(return_value=(True, True, True)))
        self._patch_expect = patch.object(pocket_dom, "expect", new=lambda locator: _FakeExpectation())
        self._patch_tab.start()
        self._patch_probe.start()
        self._patch_expect.start()
        self.addCleanup(self._patch_tab.stop)
        self.addCleanup(self._patch_probe.stop)
        self.addCleanup(self._patch_expect.stop)

    async def test_context_closed_after_click_before_result_read_retries_once(self):
        # First attempt: closed_tab.click() succeeds, but the result read
        # (page.evaluate) is where the crash happens - the exact window
        # series 105 crashed in.
        page1 = _make_page(evaluate_side_effect=Exception(
            "Target page, context or browser has been closed"))
        page2 = _make_page(closed_items=[
            {"asset": "AUD/JPY OTC", "direction": "SELL", "time_text": "09:05", "values": ["$1.00", "$1.92"]},
        ])
        warmup = _make_warmup([page1, page2])

        result = await pocket_dom.wait_for_trade_result(
            warmup, expiry_seconds=0, asset="AUD/JPY OTC", direction="SELL", settlement_buffer_seconds=0,
        )

        warmup.ensure_alive.assert_awaited_once()
        self.assertEqual(warmup.get_page.await_count, 2)
        self.assertEqual(result["result"], "win")

    async def test_context_closed_while_switching_to_closed_tab_retries_once(self):
        # First attempt: the click() on the Closed tab itself is where the
        # crash happens.
        page1 = _make_page(click_side_effect=Exception(
            "Target page, context or browser has been closed"))
        page2 = _make_page(closed_items=[
            {"asset": "AUD/JPY OTC", "direction": "SELL", "time_text": "09:05", "values": ["$1.00", "$0"]},
        ])
        warmup = _make_warmup([page1, page2])

        result = await pocket_dom.wait_for_trade_result(
            warmup, expiry_seconds=0, asset="AUD/JPY OTC", direction="SELL", settlement_buffer_seconds=0,
        )

        warmup.ensure_alive.assert_awaited_once()
        self.assertEqual(warmup.get_page.await_count, 2)
        self.assertEqual(result["result"], "loss")

    async def test_non_transient_exception_is_never_retried(self):
        # A real bug (not a browser crash) must still surface immediately
        # - retrying it would risk silently masking a regression.
        page1 = _make_page(click_side_effect=RuntimeError("some real bug"))
        warmup = _make_warmup([page1])

        with self.assertRaises(RuntimeError):
            await pocket_dom.wait_for_trade_result(
                warmup, expiry_seconds=0, asset="AUD/JPY OTC", direction="SELL", settlement_buffer_seconds=0,
            )
        warmup.ensure_alive.assert_not_awaited()


class FindClosedTradeByCriteriaTests(unittest.IsolatedAsyncioTestCase):
    """The broker-history strict-tuple matcher used by startup
    reconciliation (core/trade_series_engine.py's reconcile_stuck_series
    and core/recovery.py's _resume_one) when live tracking never resolved
    a trade - covers the three outcomes reconciliation must distinguish:
    a unique match (safe to auto-apply), no match, and multiple matches
    (both of the latter must fail closed, never guess)."""

    def setUp(self):
        self._patch_tab = patch.object(pocket_dom, "_ensure_opened_tab_active", new=AsyncMock(return_value=True))
        self._patch_expect = patch.object(pocket_dom, "expect", new=lambda locator: _FakeExpectation())
        self._patch_tab.start()
        self._patch_expect.start()
        self.addCleanup(self._patch_tab.stop)
        self.addCleanup(self._patch_expect.stop)

    async def test_unique_match_is_returned(self):
        page = _make_page(closed_items=[
            {"asset": "EUR/USD OTC", "direction": "BUY", "time_text": "10:05", "values": ["$10.00", "$19.20"]},
        ])
        warmup = _make_warmup([page])

        status, match = await pocket_dom.find_closed_trade_by_criteria(
            warmup, "EUR/USD OTC", "BUY", 10.0, datetime(2026, 7, 29, 10, 0), expiry_seconds=300,
        )

        self.assertEqual(status, "unique")
        self.assertEqual(match["result"], "win")

    async def test_no_candidates_returns_no_match(self):
        page = _make_page(closed_items=[
            {"asset": "GBP/USD OTC", "direction": "SELL", "time_text": "10:05", "values": ["$10.00", "$0"]},
        ])
        warmup = _make_warmup([page])

        status, match = await pocket_dom.find_closed_trade_by_criteria(
            warmup, "EUR/USD OTC", "BUY", 10.0, datetime(2026, 7, 29, 10, 0), expiry_seconds=300,
        )

        self.assertEqual(status, "no_match")
        self.assertIsNone(match)

    async def test_multiple_candidates_fails_closed_rather_than_guessing(self):
        page = _make_page(closed_items=[
            {"asset": "EUR/USD OTC", "direction": "BUY", "time_text": "10:05", "values": ["$10.00", "$19.20"]},
            {"asset": "EUR/USD OTC", "direction": "BUY", "time_text": "10:06", "values": ["$10.00", "$0"]},
        ])
        warmup = _make_warmup([page])

        status, matches = await pocket_dom.find_closed_trade_by_criteria(
            warmup, "EUR/USD OTC", "BUY", 10.0, datetime(2026, 7, 29, 10, 0), expiry_seconds=300,
        )

        self.assertEqual(status, "multiple_matches")
        self.assertEqual(len(matches), 2)

    async def test_read_failure_fails_closed(self):
        page = _make_page(click_side_effect=Exception(
            "Target page, context or browser has been closed"))
        warmup = _make_warmup([page])

        status, match = await pocket_dom.find_closed_trade_by_criteria(
            warmup, "EUR/USD OTC", "BUY", 10.0, datetime(2026, 7, 29, 10, 0), expiry_seconds=300,
        )

        self.assertEqual(status, "read_failed")
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
