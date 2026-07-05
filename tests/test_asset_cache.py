import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import asset_cache


class ResolveExactNameTests(unittest.TestCase):
    def setUp(self):
        self._original_cache = asset_cache._cache
        asset_cache._cache = {
            "Toncoin OTC": {"tradeable": True, "category": "Cryptocurrencies"},
            "WTI Crude Oil OTC": {"tradeable": True, "category": "Commodities"},
            "GameStop Corp OTC": {"tradeable": True, "category": "Stocks"},
            "EUR/NZD OTC": {"tradeable": True, "category": "Currencies"},
        }

    def tearDown(self):
        asset_cache._cache = self._original_cache

    def test_exact_match_passthrough(self):
        self.assertEqual(asset_cache.resolve_exact_name("Toncoin OTC"), "Toncoin OTC")

    def test_case_insensitive_match_corrects_to_exact_cached_name(self):
        self.assertEqual(asset_cache.resolve_exact_name("TONCOIN OTC"), "Toncoin OTC")
        self.assertEqual(asset_cache.resolve_exact_name("gamestop corp otc"), "GameStop Corp OTC")

    def test_extra_whitespace_still_resolves(self):
        self.assertEqual(asset_cache.resolve_exact_name("WTI  Crude   Oil OTC"), "WTI Crude Oil OTC")

    def test_missing_otc_suffix_still_resolves(self):
        self.assertEqual(asset_cache.resolve_exact_name("Toncoin"), "Toncoin OTC")

    def test_unknown_asset_returned_unchanged(self):
        self.assertEqual(asset_cache.resolve_exact_name("Some Unknown Asset"), "Some Unknown Asset")

    def test_empty_cache_returns_input_unchanged(self):
        asset_cache._cache = {}
        self.assertEqual(asset_cache.resolve_exact_name("Toncoin OTC"), "Toncoin OTC")


if __name__ == "__main__":
    unittest.main()
