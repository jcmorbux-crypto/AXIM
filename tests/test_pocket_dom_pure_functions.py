import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import pocket_dom


class ExpiryParsingTests(unittest.TestCase):
    """execution/pocket_dom.py's _expiry_to_hms/expiry_to_seconds - real
    trade-timing calculations (used to schedule outcome-detection) with
    zero prior test coverage, unlike the DOM-interaction functions in the
    same file which genuinely can't be unit-tested without a real
    browser."""

    def test_minute_expiries(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("1 Minute"), 60)
        self.assertEqual(pocket_dom.expiry_to_seconds("5 Minute"), 300)
        self.assertEqual(pocket_dom.expiry_to_seconds("15 Minute"), 900)

    def test_second_expiries(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("30 Second"), 30)

    def test_case_insensitive(self):
        self.assertEqual(pocket_dom.expiry_to_seconds("1 minute"), 60)
        self.assertEqual(pocket_dom.expiry_to_seconds("1 MINUTE"), 60)

    def test_hms_breakdown_for_over_an_hour(self):
        # not a real Pocket Option expiry today, but the hms breakdown
        # itself should still be arithmetically correct
        self.assertEqual(pocket_dom._expiry_to_hms("90 Minute"), (1, 30, 0))

    def test_unrecognized_format_raises(self):
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("garbage")

    def test_none_or_empty_raises(self):
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds(None)
        with self.assertRaises(ValueError):
            pocket_dom.expiry_to_seconds("")


class FormatAmountTests(unittest.TestCase):
    def test_whole_number_has_no_decimals(self):
        self.assertEqual(pocket_dom._format_amount(10), "10")
        self.assertEqual(pocket_dom._format_amount(10.0), "10")

    def test_fractional_amount_keeps_two_decimals(self):
        self.assertEqual(pocket_dom._format_amount(10.5), "10.50")
        self.assertEqual(pocket_dom._format_amount(1.234), "1.23")

    def test_string_input_coerced_to_float(self):
        self.assertEqual(pocket_dom._format_amount("25"), "25")


class AssetSearchTermTests(unittest.TestCase):
    """The search field only reliably matches the compact symbol with no
    separators and no OTC suffix - confirmed by direct testing per this
    function's own docstring."""

    def test_strips_slash_and_otc_suffix(self):
        self.assertEqual(pocket_dom._asset_search_term("EUR/USD OTC"), "EURUSD")

    def test_strips_dot_and_otc_suffix(self):
        self.assertEqual(pocket_dom._asset_search_term("EURUSD.OTC"), "EURUSD")

    def test_non_otc_asset_unchanged_besides_separators(self):
        self.assertEqual(pocket_dom._asset_search_term("EUR/USD"), "EURUSD")

    def test_already_compact_asset_untouched(self):
        self.assertEqual(pocket_dom._asset_search_term("BTCUSD"), "BTCUSD")


class WantsOtcTests(unittest.TestCase):
    def test_otc_asset_detected(self):
        self.assertTrue(pocket_dom._wants_otc("EUR/USD OTC"))

    def test_non_otc_asset_not_detected(self):
        self.assertFalse(pocket_dom._wants_otc("EUR/USD"))

    def test_case_insensitive(self):
        self.assertTrue(pocket_dom._wants_otc("eur/usd otc"))


class ClosestClosedItemTests(unittest.TestCase):
    """The disambiguation logic behind matching a just-placed trade to its
    Closed-list entry - explicitly flagged elsewhere (docs/
    AXIM_PRODUCTION_READINESS_REPORT.md section 4.5) as a real, residual
    source of outcome-matching ambiguity under concurrency. Zero test
    coverage existed on the actual matching algorithm before this."""

    def test_no_candidates_returns_none(self):
        items = [{"asset": "GBP/USD", "direction": "BUY", "time_text": "10:00"}]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", datetime(2026, 1, 1, 10, 0))
        self.assertIsNone(result)

    def test_single_candidate_returned_directly(self):
        items = [{"asset": "EUR/USD", "direction": "BUY", "time_text": "10:00", "id": "only"}]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", datetime(2026, 1, 1, 10, 0))
        self.assertEqual(result["id"], "only")

    def test_filters_by_asset_and_direction(self):
        items = [
            {"asset": "EUR/USD", "direction": "SELL", "time_text": "10:00", "id": "wrong_direction"},
            {"asset": "GBP/USD", "direction": "BUY", "time_text": "10:00", "id": "wrong_asset"},
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "10:00", "id": "correct"},
        ]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", datetime(2026, 1, 1, 10, 0))
        self.assertEqual(result["id"], "correct")

    def test_picks_the_time_closest_to_expected_close(self):
        expected = datetime(2026, 1, 1, 10, 5, 0)
        items = [
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "10:00", "id": "5min_away"},
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "10:05", "id": "exact_match"},
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "10:20", "id": "15min_away"},
        ]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", expected)
        self.assertEqual(result["id"], "exact_match")

    def test_handles_day_boundary_wraparound(self):
        # expected close at 23:59; a closed item showing 00:01 the next
        # day is actually only 2 minutes away, not ~24 hours away - the
        # naive .replace(hour, minute) on the same calendar day would get
        # this wrong without the +/-1 day check
        expected = datetime(2026, 1, 1, 23, 59, 0)
        items = [
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "00:01", "id": "just_after_midnight"},
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "12:00", "id": "half_day_away"},
        ]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", expected)
        self.assertEqual(result["id"], "just_after_midnight")

    def test_malformed_time_text_does_not_crash_and_loses_the_tiebreak(self):
        expected = datetime(2026, 1, 1, 10, 0, 0)
        items = [
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "not-a-time", "id": "malformed"},
            {"asset": "EUR/USD", "direction": "BUY", "time_text": "10:00", "id": "valid"},
        ]
        result = pocket_dom._closest_closed_item(items, "EUR/USD", "BUY", expected)
        self.assertEqual(result["id"], "valid")


if __name__ == "__main__":
    unittest.main()
