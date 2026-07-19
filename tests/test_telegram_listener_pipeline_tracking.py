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
from signal_lifecycle import SignalLifecycleState


class TrackPipelineEventTestCase(unittest.TestCase):
    """core/telegram_listener.py's _track_pipeline_event - the Live
    Signal Pipeline's (2026-07-19 v2 mandate) instrumentation hook,
    which must never affect real signal processing (same discipline as
    _observe_message - see that class's own test for the precedent)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_records_a_real_event(self):
        telegram_listener._track_pipeline_event(-1001, 42, 7, SignalLifecycleState.RECEIVED)
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], SignalLifecycleState.RECEIVED)
        self.assertEqual(events[0]["channel_id"], 7)

    def test_records_detail(self):
        telegram_listener._track_pipeline_event(-1001, 42, 7, SignalLifecycleState.SKIPPED, detail="fund_paused")
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertEqual(events[0]["detail"], "fund_paused")

    def test_exception_inside_tracking_never_propagates(self):
        # A broken DB path must not be able to take down the real
        # message handler that calls this - the exact same guarantee
        # _observe_message already provides for shadow observation.
        database.DB_FILE = Path("Z:/definitely/does/not/exist/anywhere.db")
        try:
            telegram_listener._track_pipeline_event(-1001, 42, 7, SignalLifecycleState.RECEIVED)  # must not raise
        finally:
            database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"


if __name__ == "__main__":
    unittest.main()
