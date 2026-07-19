import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import signal_pipeline_routes as routes
from signal_lifecycle import SignalLifecycleState

_FAKE_USER = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class SignalPipelineRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class ListJourneysTests(SignalPipelineRoutesTestCase):
    def test_empty_by_default(self):
        self.assertEqual(routes.list_journeys(user=_FAKE_USER), [])

    def test_returns_real_recorded_journeys(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED, channel_id=7)
        result = routes.list_journeys(user=_FAKE_USER)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["message_id"], 42)

    def test_filters_by_state(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.SKIPPED, channel_id=7, detail="fund_paused")
        database.record_pipeline_event(-1001, 43, SignalLifecycleState.PARSED, channel_id=7)
        skipped = routes.list_journeys(state=SignalLifecycleState.SKIPPED, user=_FAKE_USER)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["message_id"], 42)

    def test_rejects_invalid_state(self):
        with self.assertRaises(HTTPException) as ctx:
            routes.list_journeys(state="NOT_A_REAL_STATE", user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_respects_limit(self):
        for i in range(5):
            database.record_pipeline_event(-1001, i, SignalLifecycleState.RECEIVED, channel_id=7)
        self.assertEqual(len(routes.list_journeys(limit=2, user=_FAKE_USER)), 2)


class GetJourneyTests(SignalPipelineRoutesTestCase):
    def test_returns_every_event_for_the_message_in_order(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED, channel_id=7)
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.PARSED, channel_id=7)
        events = routes.get_journey(7, 42, user=_FAKE_USER)
        self.assertEqual([e["state"] for e in events],
                          [SignalLifecycleState.RECEIVED, SignalLifecycleState.PARSED])

    def test_404_for_unknown_message(self):
        with self.assertRaises(HTTPException) as ctx:
            routes.get_journey(7, 999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


class GetSignalJourneyTests(SignalPipelineRoutesTestCase):
    def test_returns_every_linked_event(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED, channel_id=7, signal_id=555)
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.SUBMITTING, channel_id=7, signal_id=555)
        events = routes.get_signal_journey(555, user=_FAKE_USER)
        self.assertEqual(len(events), 2)

    def test_404_for_a_signal_with_no_linked_events(self):
        with self.assertRaises(HTTPException) as ctx:
            routes.get_signal_journey(999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
