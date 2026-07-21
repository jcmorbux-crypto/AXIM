import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

import signal_assembler as sa


class SingleMessageSignalTests(unittest.TestCase):
    def test_standalone_signal_is_ready_immediately(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(1, 100, "EUR/USD OTC BUY 5 MIN", now=0)
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(result["asset"], "EUR/USD OTC")
        self.assertEqual(result["direction"], "BUY")
        self.assertFalse(result["is_multi_message"])
        self.assertEqual(result["message_ids"], [100])

    def test_standalone_signal_carries_its_own_raw_message(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(1, 100, "EUR/USD OTC BUY 5 MIN", now=0)
        self.assertEqual(result["raw_message"], "EUR/USD OTC BUY 5 MIN")

    def test_noise_message_is_no_signal(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(1, 100, "Good morning traders! Big win yesterday", now=0)
        self.assertEqual(result["action"], "no_signal")

    def test_empty_message_is_no_signal(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(1, 100, "", now=0)
        self.assertEqual(result["action"], "no_signal")


class TwoStepAssemblyTests(unittest.TestCase):
    def test_asset_announcement_then_entry_produces_a_multi_message_signal(self):
        asm = sa.SignalAssembler()
        # _asset_only strips a trailing "OTC" qualifier before resolving
        # the pair (matches core/provider_language_learner.py's own
        # behavior) - the announced asset is the bare pair.
        announce = asm.process_message(1, 100, "EUR/USD OTC", now=0)
        self.assertEqual(announce["action"], "announced")
        self.assertEqual(announce["asset"], "EUR/USD")

        entry = asm.process_message(1, 101, "BUY now, 3 minutes", now=10)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "EUR/USD")
        self.assertEqual(entry["direction"], "BUY")
        self.assertTrue(entry["is_multi_message"])
        self.assertEqual(entry["message_ids"], [100, 101])

    def test_multi_message_raw_message_preserves_both_real_messages(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD OTC", now=0)
        entry = asm.process_message(1, 101, "BUY now, 3 minutes", now=10)
        self.assertEqual(entry["raw_message"], "EUR/USD OTC\nBUY now, 3 minutes")

    def test_completing_a_pending_signal_clears_it(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        self.assertEqual(asm.pending_count(1), 1)
        asm.process_message(1, 101, "BUY 3 min", now=5)
        self.assertEqual(asm.pending_count(1), 0)

    def test_generic_asset_only_shape_works_without_a_hardcoded_phrase(self):
        # No "Preparing trading asset" wording anywhere - proves this is
        # the GENERIC bare-asset shape, not one hardcoded provider phrase.
        asm = sa.SignalAssembler()
        announce = asm.process_message(1, 100, "GBP/JPY", now=0)
        self.assertEqual(announce["action"], "announced")
        entry = asm.process_message(1, 101, "SELL, 1 minute", now=5)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "GBP/JPY")


class PhraseWrappedAnnouncementRegressionTests(unittest.TestCase):
    """Real captured messages from "Pro Trading Robot" (channel_id 162 in
    production - AXIM's one currently demo_ready, actively-trading
    provider) and "OTC Pro Trading Robot". Both wrap their announcement
    in a full sentence ("Preparing trading asset X\nRobot has started
    peforming analysis") rather than sending a bare asset - too long for
    _asset_only's generic bare-asset shape (correctly rejected, it isn't
    one), so this only works via the parse_asset_announcement phrase-
    match fallback. Written after discovering live that the generic-only
    check alone would have silently stopped this provider from ever
    producing a signal via this path - not a hypothetical edge case."""

    def test_phrase_wrapped_announcement_is_recognized(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(
            162, 1, "Preparing trading asset GBP/USD\nRobot has started peforming analysis", now=0,
        )
        self.assertEqual(result["action"], "announced")
        self.assertEqual(result["asset"], "GBP/USD")

    def test_an_unrelated_middle_message_does_not_disrupt_the_pending_announcement(self):
        # This provider sends a real "Economical calendar..." filler
        # message between the announcement and the entry - it must be
        # recognized as no_signal without completing or dropping the
        # pending sequence.
        asm = sa.SignalAssembler()
        asm.process_message(162, 1, "Preparing trading asset GBP/USD\nRobot has started peforming analysis", now=0)
        filler = asm.process_message(
            162, 2, "Economical calendar: No news\n\nSupport levels:\n\nResistance levels:", now=63,
        )
        self.assertEqual(filler["action"], "no_signal")
        self.assertEqual(asm.pending_count(162), 1)
        entry = asm.process_message(162, 3, "Summary:  BUY OPTION\nExpiration time:  5 MINUTES \nOpening price: 1.34300", now=65)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "GBP/USD")
        self.assertEqual(entry["direction"], "BUY")
        self.assertEqual(entry["expiry"], "5 Minute")

    def test_the_real_safe_option_double_down_messages_each_produce_their_own_signal(self):
        # This provider's real "Result: FIRST/SECOND Safe OPTION BUY (x2
        # BET)" messages carry their own asset - each is a genuine,
        # separate, real signal (matching what's currently actually
        # executing live), not a duplicate/phantom re-trade of the
        # original entry.
        asm = sa.SignalAssembler()
        first = asm.process_message(
            162, 1, "Result: FIRST Safe OPTION  BUY (x2 BET)\n\nAUD/USD\nOpening price: 0.70044\nExpiration time: 1 minutes",
            now=0,
        )
        second = asm.process_message(
            162, 2, "Result: SECOND Safe OPTION  BUY (x2 BET)\n\nAUD/USD\nOpening price: 0.70042\nExpiration time: 1 minutes",
            now=60,
        )
        self.assertEqual(first["action"], "signal_ready")
        self.assertEqual(first["asset"], "AUD/USD")
        self.assertEqual(second["action"], "signal_ready")
        self.assertEqual(second["asset"], "AUD/USD")

    def test_the_real_closing_summary_message_never_produces_a_phantom_signal(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(162, 1, "Safe option has been completed!\nClosing price: 0.70049\nSummary: AUD/USD Profit", now=0)
        self.assertEqual(result["action"], "no_signal")


class StaleTimeoutTests(unittest.TestCase):
    def test_pending_signal_expires_after_the_configured_timeout(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.process_message(1, 101, "BUY 1 min", now=301, assembly_timeout_seconds=300)
        self.assertEqual(result["action"], "no_signal")

    def test_pending_signal_still_completes_just_under_the_timeout(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.process_message(1, 101, "BUY 1 min", now=299, assembly_timeout_seconds=300)
        self.assertEqual(result["action"], "signal_ready")

    def test_a_shorter_configured_timeout_is_honored(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.process_message(1, 101, "BUY 1 min", now=31, assembly_timeout_seconds=30)
        self.assertEqual(result["action"], "no_signal")


class ExpiredAssetsReportingTests(unittest.TestCase):
    def test_expired_asset_is_reported_on_the_call_that_notices_it(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.process_message(1, 101, "some unrelated chatter", now=400, assembly_timeout_seconds=300)
        self.assertEqual(result["expired_assets"], ["EUR/USD"])

    def test_no_expiry_reports_an_empty_list(self):
        asm = sa.SignalAssembler()
        result = asm.process_message(1, 100, "just chatting today", now=0)
        self.assertEqual(result["expired_assets"], [])

    def test_a_completing_message_still_reports_an_unrelated_expiry(self):
        # Expiry is detected (and reported) the moment a later call's own
        # expire_stale sweep notices it, not held pending until some
        # further-future call - EUR/USD (announced at t=0, 300s timeout)
        # is swept away by the t=350 call that announces GBP/JPY, so
        # THAT call is the one that reports it, not the one after.
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        second = asm.process_message(1, 101, "GBP/JPY", now=350, assembly_timeout_seconds=300)
        self.assertEqual(second["expired_assets"], ["EUR/USD"])

        result = asm.process_message(1, 102, "BUY 1 min", now=360, assembly_timeout_seconds=300)
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(result["asset"], "GBP/JPY")
        self.assertEqual(result["expired_assets"], [])


class MultiplePendingSequenceTests(unittest.TestCase):
    def test_two_different_assets_can_be_pending_at_once_on_the_same_channel(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.process_message(1, 101, "GBP/JPY", now=1)
        self.assertEqual(asm.pending_count(1), 2)

    def test_a_standalone_new_asset_signal_does_not_disturb_an_unrelated_pending_one(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)  # pending
        result = asm.process_message(1, 101, "GBP/JPY OTC SELL 5 MIN", now=1)  # unrelated standalone
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(asm.pending_count(1), 1)  # EUR/USD still pending, untouched

    def test_two_channels_never_share_pending_state(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        # Channel 2 has never seen an announcement - its own entry-only
        # message must NOT accidentally complete channel 1's pending EUR/USD.
        result = asm.process_message(2, 200, "BUY 1 min", now=1)
        self.assertEqual(result["action"], "no_signal")
        self.assertEqual(asm.pending_count(1), 1)

    def test_reply_to_a_specific_announcement_resolves_unambiguously(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.process_message(1, 101, "GBP/JPY", now=1)
        # Two pending - reply explicitly targets message 100 (EUR/USD)
        result = asm.process_message(1, 102, "BUY 1 min", reply_to_message_id=100, now=2)
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(result["asset"], "EUR/USD")
        # GBP/JPY (101) should still be pending, untouched
        self.assertEqual(asm.pending_count(1), 1)

    def test_no_reply_link_with_two_pending_falls_back_to_most_recent(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.process_message(1, 101, "GBP/JPY", now=5)
        result = asm.process_message(1, 102, "BUY 1 min", now=10)  # no reply link
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(result["asset"], "GBP/JPY")  # most recently announced


class ClearChannelTests(unittest.TestCase):
    def test_clear_channel_drops_pending_state(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.clear_channel(1)
        result = asm.process_message(1, 101, "BUY 1 min", now=1)
        self.assertEqual(result["action"], "no_signal")


class HandleEditTests(unittest.TestCase):
    """A real Telegram edit to a message this assembler has already seen
    (directive: providers correcting/cancelling a signal via edit). Only
    a still-pending (not yet completed) announcement can meaningfully be
    affected - a signal that already went "signal_ready" was already
    routed to execution synchronously in that same call, so there is no
    later point an edit could retroactively touch it."""

    def test_editing_an_unknown_message_id_is_a_safe_no_op(self):
        asm = sa.SignalAssembler()
        result = asm.handle_edit(1, 999, "whatever", now=0)
        self.assertEqual(result["action"], "not_pending")

    def test_editing_a_pending_announcement_to_a_different_asset_corrects_it(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.handle_edit(1, 100, "GBP/JPY", now=5)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["old_asset"], "EUR/USD")
        self.assertEqual(result["new_asset"], "GBP/JPY")
        # The corrected asset - not the original - is what a later entry
        # message completes against.
        entry = asm.process_message(1, 101, "BUY 1 min", now=10)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "GBP/JPY")
        self.assertEqual(asm.pending_count(1), 0)

    def test_editing_a_pending_announcement_to_gibberish_cancels_it(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        result = asm.handle_edit(1, 100, "Trade cancelled, sorry everyone", now=5)
        self.assertEqual(result["action"], "cancelled")
        self.assertEqual(result["asset"], "EUR/USD")
        self.assertEqual(asm.pending_count(1), 0)
        # No pending sequence left to complete - a later message that
        # would have completed it must not trade on stale, cancelled state.
        entry = asm.process_message(1, 101, "BUY 1 min", now=10)
        self.assertEqual(entry["action"], "no_signal")

    def test_editing_an_already_completed_signals_message_is_a_safe_no_op(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.process_message(1, 101, "BUY 1 min", now=5)  # completes and clears the pending sequence
        result = asm.handle_edit(1, 100, "GBP/JPY", now=10)
        self.assertEqual(result["action"], "not_pending")

    def test_editing_a_standalone_signals_message_is_a_safe_no_op(self):
        # A standalone (single-message) signal is never added to
        # pending_by_reply_to at all - nothing to edit.
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD OTC BUY 5 MIN", now=0)
        result = asm.handle_edit(1, 100, "GBP/USD OTC BUY 5 MIN", now=5)
        self.assertEqual(result["action"], "not_pending")

    def test_editing_one_of_two_pending_asset_announcements_leaves_the_other_untouched(self):
        asm = sa.SignalAssembler()
        asm.process_message(1, 100, "EUR/USD", now=0)
        asm.process_message(1, 101, "GBP/JPY", now=1)
        asm.handle_edit(1, 100, "Trade cancelled", now=2)
        self.assertEqual(asm.pending_count(1), 1)
        entry = asm.process_message(1, 102, "BUY 1 min", now=5)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "GBP/JPY")


if __name__ == "__main__":
    unittest.main()
