import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
