import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

from asset_cache import AssetCache


class ResolveExactNameTests(unittest.TestCase):
    def setUp(self):
        self.cache = AssetCache()
        self.cache._cache = {
            "Toncoin OTC": {"tradeable": True, "category": "Cryptocurrencies"},
            "WTI Crude Oil OTC": {"tradeable": True, "category": "Commodities"},
            "GameStop Corp OTC": {"tradeable": True, "category": "Stocks"},
            "EUR/NZD OTC": {"tradeable": True, "category": "Currencies"},
        }

    def test_exact_match_passthrough(self):
        self.assertEqual(self.cache.resolve_exact_name("Toncoin OTC"), "Toncoin OTC")

    def test_case_insensitive_match_corrects_to_exact_cached_name(self):
        self.assertEqual(self.cache.resolve_exact_name("TONCOIN OTC"), "Toncoin OTC")
        self.assertEqual(self.cache.resolve_exact_name("gamestop corp otc"), "GameStop Corp OTC")

    def test_extra_whitespace_still_resolves(self):
        self.assertEqual(self.cache.resolve_exact_name("WTI  Crude   Oil OTC"), "WTI Crude Oil OTC")

    def test_missing_otc_suffix_still_resolves(self):
        self.assertEqual(self.cache.resolve_exact_name("Toncoin"), "Toncoin OTC")

    def test_unknown_asset_returned_unchanged(self):
        self.assertEqual(self.cache.resolve_exact_name("Some Unknown Asset"), "Some Unknown Asset")

    def test_empty_cache_returns_input_unchanged(self):
        self.cache._cache = {}
        self.assertEqual(self.cache.resolve_exact_name("Toncoin OTC"), "Toncoin OTC")


class InstanceIsolationTests(unittest.TestCase):
    """Two accounts' caches must never see each other's data - the whole
    reason this became a class instead of a module-level global."""

    def test_two_instances_do_not_share_state(self):
        cache_a = AssetCache()
        cache_b = AssetCache()
        cache_a._cache = {"EUR/USD OTC": {"tradeable": True, "category": "Currencies"}}
        self.assertIsNone(cache_b.is_known_tradeable("EUR/USD OTC"))
        self.assertTrue(cache_a.is_known_tradeable("EUR/USD OTC"))


if __name__ == "__main__":
    unittest.main()
