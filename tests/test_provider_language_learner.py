"""Tests core/provider_language_learner.py - the Phase 2 automatic
signal-language detector. Fixtures mirror real message shapes from the
OPT SIGNALS research corpus (not invented formats) so a pass here means
the detector genuinely handles a real provider shape, not a synthetic
toy case. Cross-validated separately against the actual 12-provider
research database (see docs/AXIM_ENGINEERING_JOURNAL.md) - this file
covers the pure logic in isolation."""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import provider_language_learner as learner


def _messages(*id_text_pairs):
    return [{"message_id": mid, "text": text, "date_utc": f"2026-01-01T00:{i:02d}:00"}
            for i, (mid, text) in enumerate(id_text_pairs)]


class ClassifyResultTokenTests(unittest.TestCase):
    def test_checkmark_symbol_is_win(self):
        self.assertEqual(learner._classify_result_token("✅✅✅"), "win")

    def test_cross_symbol_is_loss(self):
        self.assertEqual(learner._classify_result_token("❎❎❎"), "loss")

    def test_refund_word_is_draw(self):
        self.assertEqual(learner._classify_result_token("Refund♻️♻️♻️"), "draw")

    def test_plain_win_word(self):
        self.assertEqual(learner._classify_result_token("Win ✅💯"), "win")

    def test_bare_plus_minus_equals(self):
        self.assertEqual(learner._classify_result_token("+"), "win")
        self.assertEqual(learner._classify_result_token("-"), "loss")
        self.assertEqual(learner._classify_result_token("="), "draw")

    def test_a_real_signal_line_is_not_a_result(self):
        self.assertIsNone(learner._classify_result_token("GBP/CAD HIGH ⬆️ 15 MIN"))

    def test_long_text_is_never_a_result_token(self):
        long_promo = "x" * 100
        self.assertIsNone(learner._classify_result_token(long_promo))


class CompactPatternTests(unittest.TestCase):
    def test_compact_dirfirst_matches_daniel_fx_trade_shape(self):
        parsed = learner._try_compact_dirfirst("GBP/CAD HIGH ⬆️ 15 MIN")
        self.assertEqual(parsed["asset"], "GBP/CAD")
        self.assertEqual(parsed["direction"], "BUY")
        self.assertEqual(parsed["expiry"], "15 Minutes")

    def test_compact_dirfirst_lower_direction(self):
        parsed = learner._try_compact_dirfirst("GBP/CHF LOWER ⬇️ 15 MIN")
        self.assertEqual(parsed["direction"], "SELL")

    def test_compact_buysell_matches_signals2_shape(self):
        parsed = learner._try_compact_buysell("GBP/CAD 15 min SELL")
        self.assertEqual(parsed["asset"], "GBP/CAD")
        self.assertEqual(parsed["direction"], "SELL")
        self.assertEqual(parsed["expiry"], "15 Minutes")

    def test_labeled_block_matches_vip_signals_shape(self):
        text = "📊 VIP Signals1️⃣\n🔸 Pair: ZAR/USD (OTC) — 12 MIN DOWN\n\n🔹 Entry shared"
        parsed = learner._try_labeled_block(text)
        self.assertEqual(parsed["asset"], "ZAR/USD")
        self.assertEqual(parsed["direction"], "SELL")
        self.assertEqual(parsed["expiry"], "12 Minutes")

    def test_asset_only_matches_ntrade_first_message_shape(self):
        self.assertEqual(learner._asset_only("EUR/USD"), "EUR/USD")

    def test_asset_only_rejects_a_line_with_a_direction_word(self):
        self.assertIsNone(learner._asset_only("EUR/USD BUY"))

    def test_direction_only_matches_ntrade_second_message_shape(self):
        info = learner._direction_only("BUY 5 minutes")
        self.assertEqual(info["direction"], "BUY")
        self.assertEqual(info["expiry"], "5 Minutes")


class DetectPatternTests(unittest.TestCase):
    def test_detects_compact_dirfirst_for_a_daniel_fx_trade_like_batch(self):
        messages = _messages(
            (1, "GBP/CAD HIGH ⬆️ 15 MIN"), (2, "✅✅✅"),
            (3, "GBP/CHF LOWER ⬇️ 15 MIN"), (4, "❎❎❎"),
            (5, "Some unrelated promo text that never matches anything useful here"),
        )
        result = learner.detect_pattern(messages)
        self.assertIsNotNone(result)
        self.assertEqual(result["pattern"], "compact_dirfirst")

    def test_detects_two_step_pattern_for_an_ntrade_like_batch(self):
        pairs = []
        mid = 1
        for i in range(10):
            pairs.append((mid, "EUR/USD")); mid += 1
            pairs.append((mid, "BUY 5 minutes")); mid += 1
            pairs.append((mid, "some chatter that matches nothing")); mid += 1
        messages = _messages(*pairs)
        result = learner.detect_pattern(messages)
        self.assertIsNotNone(result)
        self.assertEqual(result["pattern"], "two_step_asset_then_direction")

    def test_returns_none_for_a_channel_with_no_real_signal_shape(self):
        messages = _messages(
            (1, "Welcome to our channel!"), (2, "Check out our promo code TODAY50"),
            (3, "Thanks everyone for a great session"),
        )
        self.assertIsNone(learner.detect_pattern(messages))

    def test_returns_none_for_an_empty_batch(self):
        self.assertIsNone(learner.detect_pattern([]))


class ParseWithPatternTests(unittest.TestCase):
    def test_compact_dirfirst_end_to_end(self):
        messages = _messages((1, "GBP/CAD HIGH ⬆️ 15 MIN"), (2, "✅✅✅"))
        signals, links = learner.parse_with_pattern("compact_dirfirst", messages)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["normalized_asset"], "GBP/CAD")
        self.assertEqual(signals[0]["direction"], "BUY")
        self.assertEqual(links[0]["result"], "win")
        self.assertEqual(links[0]["signal_message_id"], 1)

    def test_two_step_end_to_end(self):
        messages = _messages((1, "EUR/USD"), (2, "BUY 5 minutes"), (3, "Win"))
        signals, links = learner.parse_with_pattern("two_step_asset_then_direction", messages)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["normalized_asset"], "EUR/USD")
        self.assertEqual(signals[0]["direction"], "BUY")
        self.assertEqual(links[0]["result"], "win")

    def test_two_step_unresolved_when_no_result_follows(self):
        messages = _messages((1, "EUR/USD"), (2, "BUY 5 minutes"))
        signals, links = learner.parse_with_pattern("two_step_asset_then_direction", messages)
        self.assertEqual(len(signals), 1)
        self.assertEqual(links[0]["result"], "unresolved")


class AnalyzeProviderTests(unittest.TestCase):
    def test_full_pipeline_on_a_realistic_batch(self):
        messages = _messages(
            (1, "GBP/CAD HIGH ⬆️ 15 MIN"), (2, "✅✅✅"),
            (3, "GBP/CHF LOWER ⬇️ 15 MIN"), (4, "❎❎❎"),
            (5, "USD/JPY HIGH ⬆️ 10 MIN"), (6, "✅✅✅"),
        )
        result = learner.analyze_provider(messages)
        self.assertIsNotNone(result)
        self.assertEqual(result["pattern"], "compact_dirfirst")
        self.assertEqual(len(result["signal_records"]), 3)
        wins = [l for l in result["result_links"] if l["result"] == "win"]
        self.assertEqual(len(wins), 2)

    def test_returns_none_for_a_non_signal_channel(self):
        messages = _messages((1, "hello"), (2, "how are you"), (3, "great, thanks"))
        self.assertIsNone(learner.analyze_provider(messages))


class TylerVipFlowTests(unittest.TestCase):
    """TYLER VIP CLUB's real vocabulary ("BUY/SELL NOW X (OTC)" signals,
    "WIN"/"Bad luck" results - neither generic result word) ported from
    the OPT SIGNALS research branch's hand-built adapter, which was
    grounded in an exhaustive read of that provider's real message dump.
    Verified live against the real channel's actual current history
    (20.94% coverage, 338 signal records, 323 decided results,
    end-to-end through core/provider_onboarding.py) before adding this
    offline regression coverage."""

    def test_buy_now_signal_is_parsed(self):
        parsed = learner._tyler_parse_signal("BUY NOW EUR/USD (OTC)")
        self.assertEqual(parsed["direction"], "BUY")
        self.assertEqual(parsed["asset"], "EUR/USD")
        self.assertEqual(parsed["expiry"], "2 Minutes")

    def test_sell_now_signal_is_parsed(self):
        parsed = learner._tyler_parse_signal("SELL NOW GBP/JPY (OTC)")
        self.assertEqual(parsed["direction"], "SELL")
        self.assertEqual(parsed["asset"], "GBP/JPY")

    def test_crypto_asset_name_is_trusted_verbatim(self):
        # Not a currency pair - _resolve_pair won't match it, so the raw
        # source text is trusted as-is (matches the ported adapter's own
        # documented behavior for non-forex assets).
        parsed = learner._tyler_parse_signal("BUY NOW Bitcoin (OTC)")
        self.assertEqual(parsed["asset"], "Bitcoin")

    def test_bad_luck_is_a_loss_not_recognized_by_the_generic_classifier(self):
        # The whole reason this needs its own named pattern: "Bad luck"
        # contains none of _classify_result_token's generic loss words.
        self.assertIsNone(learner._classify_result_token("Bad luck"))
        self.assertTrue(learner._tyler_is_loss("Bad luck"))

    def test_plain_win_is_a_win(self):
        self.assertTrue(learner._tyler_is_win("WIN"))
        self.assertTrue(learner._tyler_is_win("WIN\nDouble profit!"))

    def test_recovery_instruction_lines_are_neither_signal_nor_result(self):
        self.assertIsNone(learner._tyler_parse_signal("Raise your stake x2.2"))
        self.assertFalse(learner._tyler_is_win("Go back to the initial trade size"))
        self.assertFalse(learner._tyler_is_loss("Go back to the initial trade size"))

    def test_detect_pattern_identifies_a_tyler_like_batch(self):
        messages = _messages(
            (1, "Next pair: EUR/USD (OTC)\nTrade time: 2⃣"),
            (2, "BUY NOW EUR/USD (OTC)"), (3, "WIN"),
            (4, "SELL NOW GBP/JPY (OTC)"), (5, "Bad luck"),
            (6, "Raise your stake x2"),
            (7, "BUY NOW AUD/CAD (OTC)"), (8, "WIN\nDouble profit!"),
        )
        detection = learner.detect_pattern(messages)
        self.assertIsNotNone(detection)
        self.assertEqual(detection["pattern"], "tyler_vip_flow")
        # None of the generic reusable templates should fire on this
        # provider-specific vocabulary - confirms adding this named
        # pattern carries no regression risk for the others.
        for name, score in detection["all_scores"].items():
            if name != "tyler_vip_flow":
                self.assertEqual(score, 0.0, f"{name} unexpectedly matched Tyler-style text")

    def test_parse_with_pattern_end_to_end(self):
        messages = _messages(
            (1, "BUY NOW EUR/USD (OTC)"), (2, "WIN"),
            (3, "SELL NOW GBP/JPY (OTC)"), (4, "Bad luck"),
        )
        signals, links = learner.parse_with_pattern("tyler_vip_flow", messages)
        self.assertEqual(len(signals), 2)
        results = [l["result"] for l in links]
        self.assertEqual(results, ["win", "loss"])


if __name__ == "__main__":
    unittest.main()
