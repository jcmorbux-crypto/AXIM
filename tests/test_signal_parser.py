import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

from signal_parser import parse_signal, apply_signal_rules


class SignalParserTests(unittest.TestCase):
    def test_slash_format_unchanged(self):
        signal = parse_signal("EUR/USD OTC BUY M5")
        self.assertEqual(signal["asset"], "EUR/USD OTC")
        self.assertEqual(signal["direction"], "BUY")
        self.assertEqual(signal["expiry"], "5 Minute")

    def test_down_overrides_trailing_buy(self):
        signal = parse_signal("REAL | Trade 3/3\nNZDJPY OTC DOWN BUY")
        self.assertEqual(signal["asset"], "NZD/JPY OTC")
        self.assertEqual(signal["direction"], "SELL")

    def test_up_with_trailing_buy(self):
        signal = parse_signal("REAL | Trade 3/3\nNZDJPY OTC UP BUY")
        self.assertEqual(signal["asset"], "NZD/JPY OTC")
        self.assertEqual(signal["direction"], "BUY")

    def test_call_means_buy(self):
        signal = parse_signal("EURUSD OTC CALL")
        self.assertEqual(signal["asset"], "EUR/USD OTC")
        self.assertEqual(signal["direction"], "BUY")

    def test_put_means_sell(self):
        signal = parse_signal("EURUSD OTC PUT")
        self.assertEqual(signal["asset"], "EUR/USD OTC")
        self.assertEqual(signal["direction"], "SELL")

    def test_concat_asset_without_otc_suffix(self):
        signal = parse_signal("GBPJPY DOWN")
        self.assertEqual(signal["asset"], "GBP/JPY")
        self.assertEqual(signal["direction"], "SELL")

    def test_result_recap_with_profit_line_still_parses_correctly(self):
        signal = parse_signal(
            "REAL | Trade 3/3\nNZDJPY OTC DOWN BUY\n"
            "Step 1 -> 1.00 $ -> +1.92 $\nProfit: +0.92 $\nBalance: 40.68 USD"
        )
        self.assertEqual(signal["asset"], "NZD/JPY OTC")
        self.assertEqual(signal["direction"], "SELL")

    def test_no_asset_returns_none(self):
        self.assertIsNone(parse_signal("just some random text with no asset"))

    def test_no_direction_returns_none(self):
        self.assertIsNone(parse_signal("EUR/USD OTC M5"))

    def test_no_asset_logs_a_parse_failure(self):
        # AXIM Core directive requires parse failures to be logged (the
        # parser previously had no logger at all - docs/AXIM_RELEASE_CHECKLIST.md).
        with self.assertLogs("axim.parser", level="WARNING") as cm:
            parse_signal("just some random text with no asset")
        self.assertIn("no recognizable asset", cm.output[0])

    def test_no_direction_logs_a_parse_failure(self):
        with self.assertLogs("axim.parser", level="WARNING") as cm:
            parse_signal("EUR/USD OTC M5")
        self.assertIn("no direction", cm.output[0])

    def test_successful_parse_does_not_log_a_warning(self):
        with self.assertRaises(AssertionError):
            # assertLogs itself raises AssertionError if nothing was logged
            # at all - the intended outcome here (a clean parse shouldn't
            # emit a parse-failure warning).
            with self.assertLogs("axim.parser", level="WARNING"):
                parse_signal("EUR/USD OTC BUY M5")

    def test_signal_word_does_not_false_positive_without_a_label(self):
        # Regression: "Signal" is 6 letters and was previously matched as
        # a fake concatenated pair "SIG/NAL" when there was no real asset
        # in the message at all.
        self.assertIsNone(parse_signal("SIGNAL BUY M5"))

    def test_labeled_cryptocurrency_parses_with_platform_exact_name(self):
        # "Toncoin OTC" is a real, tradeable Pocket Option asset name -
        # confirmed live against execution/asset_cache.py's scanned list.
        # Case must be preserved exactly (platform display names are mixed
        # case, not reconstructable via .title()).
        message = (
            "✅ The analysis is complete!\n\n"
            "\U0001F4B8 Cryptocurrency: Toncoin OTC\n"
            "⏳ Expiration time: M6\n\n"
            "\U0001F4C8 Signal: BUY ⬆️"
        )
        signal = parse_signal(message)
        self.assertEqual(signal["asset"], "Toncoin OTC")
        self.assertEqual(signal["direction"], "BUY")
        self.assertEqual(signal["expiry"], "6 Minute")

    def test_labeled_stock_preserves_exact_platform_casing(self):
        # Regression: the old STOCK: path ran .title() on the captured name,
        # which mangles real Pocket Option stock names with internal caps
        # ("GameStop Corp OTC" -> "Gamestop Corp OTC", a mismatch against the
        # platform's exact-text asset search).
        message = (
            "✅ The analysis is complete!\n\n"
            "🍏 Stock: GameStop Corp OTC\n"
            "⏳ Expiration time: S10\n\n"
            "📈 Signal: SELL ⬇️"
        )
        signal = parse_signal(message)
        self.assertEqual(signal["asset"], "GameStop Corp OTC")
        self.assertEqual(signal["direction"], "SELL")

    def test_labeled_commodity_parses(self):
        signal = parse_signal("Commodity: Gold OTC\nSignal: BUY\nExpiration: M1")
        self.assertEqual(signal["asset"], "Gold OTC")
        self.assertEqual(signal["direction"], "BUY")

    def test_labeled_commodity_typo_variant_parses(self):
        # Regression: Go+ actually spells this label "Commoditi:", not
        # "Commodity:" - a real message was silently rejected before this
        # was caught against the live log.
        message = (
            "✅ The analysis is complete!\n\n"
            "🛢 Commoditi: WTI Crude Oil OTC\n"
            "⏳ Expiration time: S20\n\n"
            "📈 Signal: BUY ⬆️"
        )
        signal = parse_signal(message)
        self.assertEqual(signal["asset"], "WTI Crude Oil OTC")
        self.assertEqual(signal["direction"], "BUY")
        self.assertEqual(signal["expiry"], "20 Seconds")

    def test_labeled_index_parses(self):
        signal = parse_signal("Index: US100 OTC\nSignal: SELL\nExpiration: M1")
        self.assertEqual(signal["asset"], "US100 OTC")
        self.assertEqual(signal["direction"], "SELL")

    def test_labeled_currency_pair_normalizes_like_unlabeled(self):
        signal = parse_signal("Currency pair: EUR/NZD OTC\nSignal: BUY\nExpiration: S55")
        self.assertEqual(signal["asset"], "EUR/NZD OTC")
        self.assertEqual(signal["direction"], "BUY")
        self.assertEqual(signal["expiry"], "55 Seconds")

    def test_labeled_currency_word_variants_parse_as_forex(self):
        for label in ("Currency", "Currencies", "Pair"):
            with self.subTest(label=label):
                signal = parse_signal(f"{label}: EUR/NZD OTC\nSignal: BUY\nExpiration: S55")
                self.assertEqual(signal["asset"], "EUR/NZD OTC")

    def test_labeled_index_word_variants_parse(self):
        for label in ("Indices", "Indice", "Index"):
            with self.subTest(label=label):
                signal = parse_signal(f"{label}: US100 OTC\nSignal: SELL\nExpiration: M1")
                self.assertEqual(signal["asset"], "US100 OTC")
                self.assertEqual(signal["direction"], "SELL")

    def test_labeled_crypto_word_variants_parse(self):
        for label in ("Crypto", "Cryptocurrency", "Cryptocurrencies"):
            with self.subTest(label=label):
                signal = parse_signal(f"{label}: Toncoin OTC\nSignal: BUY\nExpiration: M1")
                self.assertEqual(signal["asset"], "Toncoin OTC")


class ApplySignalRulesTests(unittest.TestCase):
    def test_single_rule_transforms_before_parse(self):
        rules = [{"find_pattern": r"Signal:\s*", "replace_with": "Direction: "}]
        transformed = apply_signal_rules("Currency pair: EUR/NZD OTC\nSignal: BUY", rules)
        self.assertIn("Direction: BUY", transformed)

    def test_no_rules_returns_message_unchanged(self):
        self.assertEqual(apply_signal_rules("unchanged text", []), "unchanged text")

    def test_multiple_rules_applied_in_order(self):
        rules = [
            {"find_pattern": "FOO", "replace_with": "BAR"},
            {"find_pattern": "BAR", "replace_with": "BAZ"},
        ]
        self.assertEqual(apply_signal_rules("FOO", rules), "BAZ")

    def test_invalid_regex_rule_is_skipped_not_raised(self):
        rules = [{"find_pattern": "(unclosed", "replace_with": "x"}]
        # Should not raise, and the message passes through unmodified.
        self.assertEqual(apply_signal_rules("hello", rules), "hello")

    def test_invalid_regex_rule_logs_a_warning(self):
        rules = [{"id": 7, "find_pattern": "(unclosed", "replace_with": "x"}]
        with self.assertLogs("axim.parser", level="WARNING") as cm:
            apply_signal_rules("hello", rules)
        self.assertIn("invalid regex", cm.output[0])
        self.assertIn("id=7", cm.output[0])


if __name__ == "__main__":
    unittest.main()
