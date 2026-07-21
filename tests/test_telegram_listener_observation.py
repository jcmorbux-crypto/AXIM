import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import telegram_listener


class ShadowObservationTestCase(unittest.TestCase):
    """core/telegram_listener.py's _observe_message - promoted 2026-07-20
    from a shadow-only observation hook to the real per-channel signal
    decision path (see its own docstring), while still building the same
    observation evidence core/provider_profile.py's graduation criteria
    need. These tests cover the observation/graduation bookkeeping side;
    tests/test_signal_assembler.py covers the assembly decision logic
    itself in isolation."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        database.upsert_channel(chat_id=555, username="obstest", title="Observation Test", kind="channel")
        self.channel_row = database.list_channels()[0]
        # Fresh assembler per test - the real one is a module-level
        # singleton and would leak pending state across tests otherwise.
        self._orig_assembler = telegram_listener._shadow_assembler
        telegram_listener._shadow_assembler = telegram_listener.signal_assembler.SignalAssembler()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        telegram_listener._shadow_assembler = self._orig_assembler

    def test_none_channel_row_is_a_safe_no_op(self):
        result = telegram_listener._observe_message(None, "EUR/USD BUY 5 MIN", 1)
        self.assertIsNone(result)

    def test_completed_standalone_signal_creates_a_profile_and_records_success(self):
        result = telegram_listener._observe_message(self.channel_row, "EUR/USD OTC BUY 5 MIN", 1)
        self.assertEqual(result["action"], "signal_ready")
        self.assertEqual(result["asset"], "EUR/USD OTC")
        self.assertEqual(result["direction"], "BUY")
        profile = database.get_provider_profile_by_channel_id(self.channel_row["id"])
        self.assertIsNotNone(profile)
        self.assertEqual(profile["observed_signal_count"], 1)
        self.assertEqual(profile["parse_success_count"], 1)

    def test_ordinary_chatter_does_not_move_the_counters(self):
        telegram_listener._observe_message(self.channel_row, "Good morning traders, great results yesterday!", 1)
        profile = database.get_provider_profile_by_channel_id(self.channel_row["id"])
        self.assertEqual(profile["observed_signal_count"], 0)

    def test_two_step_assembly_records_one_success_not_two(self):
        telegram_listener._observe_message(self.channel_row, "EUR/USD", 1)
        telegram_listener._observe_message(self.channel_row, "BUY now, 3 minutes", 2)
        profile = database.get_provider_profile_by_channel_id(self.channel_row["id"])
        self.assertEqual(profile["observed_signal_count"], 1)
        self.assertEqual(profile["parse_success_count"], 1)

    def test_exception_inside_observation_never_propagates(self):
        # A broken DB path (e.g. a stale DB_FILE) must not be able to
        # take down the real message handler that calls this - and must
        # fail CLOSED (return None, meaning no trade), not silently
        # fabricate a signal_ready result from corrupted state.
        database.DB_FILE = Path("Z:/definitely/does/not/exist/anywhere.db")
        try:
            result = telegram_listener._observe_message(self.channel_row, "EUR/USD BUY 5 MIN", 1)
            self.assertIsNone(result)
        finally:
            database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"


class RealExecutionDecisionTestCase(unittest.TestCase):
    """2026-07-20: _observe_message's return value is now what the real
    handler routes to broker_account_manager.route_signal - these tests
    cover the multi-message and raw_message-threading behavior that
    matters for real execution, beyond the observation bookkeeping
    ShadowObservationTestCase already covers."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        database.upsert_channel(chat_id=556, username="realexec", title="Real Execution Test", kind="channel")
        self.channel_row = database.list_channels()[0]
        self._orig_assembler = telegram_listener._shadow_assembler
        telegram_listener._shadow_assembler = telegram_listener.signal_assembler.SignalAssembler()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        telegram_listener._shadow_assembler = self._orig_assembler

    def test_multi_message_signal_is_ready_and_carries_both_messages(self):
        announce = telegram_listener._observe_message(self.channel_row, "EUR/USD OTC", 1)
        self.assertEqual(announce["action"], "announced")
        entry = telegram_listener._observe_message(self.channel_row, "BUY now, 3 minutes", 2)
        self.assertEqual(entry["action"], "signal_ready")
        self.assertEqual(entry["asset"], "EUR/USD")
        self.assertEqual(entry["direction"], "BUY")
        self.assertEqual(entry["raw_message"], "EUR/USD OTC\nBUY now, 3 minutes")
        self.assertEqual(entry["message_ids"], [1, 2])

    def test_a_second_pending_asset_no_longer_overwrites_the_first(self):
        # The real correctness fix over the old single-slot
        # _carried_assets_by_channel mechanism: two different assets can
        # each have their own pending announcement in flight at once
        # without one silently clobbering the other. A later, unrelated
        # announcement (GBP/JPY) must not lose the earlier one (EUR/USD) -
        # proven by correctly completing the OLDER pending signal via an
        # explicit reply to its own announcement message, even though a
        # newer pending sequence is also in flight.
        first = telegram_listener._observe_message(self.channel_row, "EUR/USD", 1)
        second = telegram_listener._observe_message(self.channel_row, "GBP/JPY", 2)
        self.assertEqual(first["action"], "announced")
        self.assertEqual(second["action"], "announced")
        first_entry = telegram_listener._observe_message(
            self.channel_row, "BUY now, 5 min", 3, reply_to_message_id=1,
        )
        self.assertEqual(first_entry["action"], "signal_ready")
        self.assertEqual(first_entry["asset"], "EUR/USD")

    def test_ordinary_chatter_yields_no_signal_action(self):
        result = telegram_listener._observe_message(self.channel_row, "Good morning traders!", 1)
        self.assertEqual(result["action"], "no_signal")


if __name__ == "__main__":
    unittest.main()
