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
    """core/telegram_listener.py's _observe_message - the Universal
    Signal Intelligence Engine's SHADOW observation hook, which must
    never affect real execution (checked via the module's OWN separate
    _carried_assets_by_channel / real parser path being completely
    untouched by any of this) while still building real observation
    evidence for core/provider_profile.py's graduation criteria."""

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
        telegram_listener._observe_message(None, "EUR/USD BUY 5 MIN", 1)  # must not raise

    def test_completed_standalone_signal_creates_a_profile_and_records_success(self):
        telegram_listener._observe_message(self.channel_row, "EUR/USD OTC BUY 5 MIN", 1)
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
        # take down the real message handler that calls this.
        database.DB_FILE = Path("Z:/definitely/does/not/exist/anywhere.db")
        try:
            telegram_listener._observe_message(self.channel_row, "EUR/USD BUY 5 MIN", 1)  # must not raise
        finally:
            database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"


if __name__ == "__main__":
    unittest.main()
