import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
from signal_lifecycle import SignalLifecycleState


class SignalPipelineEventsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class RecordPipelineEventTests(SignalPipelineEventsTestCase):
    def test_records_a_real_event(self):
        event_id = database.record_pipeline_event(
            -1001, 42, SignalLifecycleState.RECEIVED, channel_id=7, detail="test")
        self.assertIsNotNone(event_id)
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], SignalLifecycleState.RECEIVED)
        self.assertEqual(events[0]["channel_id"], 7)
        self.assertEqual(events[0]["detail"], "test")
        self.assertEqual(events[0]["chat_id"], "-1001")

    def test_never_raises_when_the_db_is_unreachable(self):
        # The exact discipline _observe_message already establishes -
        # this is called from inside the live Telegram handler and must
        # never be able to take down real signal processing.
        database.DB_FILE = Path("Z:/definitely/does/not/exist/anywhere.db")
        try:
            result = database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
            self.assertIsNone(result)
        finally:
            database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"

    def test_chat_id_none_is_stored_as_none_not_the_string_none(self):
        database.record_pipeline_event(None, 42, SignalLifecycleState.RECEIVED)
        conn = database.get_connection()
        row = conn.execute("SELECT chat_id FROM signal_pipeline_events").fetchone()
        conn.close()
        self.assertIsNone(row["chat_id"])

    def test_signal_id_defaults_to_none(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertIsNone(events[0]["signal_id"])


class LinkPipelineEventsToSignalTests(SignalPipelineEventsTestCase):
    def test_links_every_prior_event_for_the_message(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.PARSED)
        database.link_pipeline_events_to_signal(-1001, 42, signal_id=999)
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertTrue(all(e["signal_id"] == 999 for e in events))

    def test_never_overwrites_an_already_linked_event(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        database.link_pipeline_events_to_signal(-1001, 42, signal_id=999)
        database.link_pipeline_events_to_signal(-1001, 42, signal_id=111)  # a second, wrong link attempt
        events = database.list_pipeline_events_for_message(-1001, 42)
        self.assertEqual(events[0]["signal_id"], 999)

    def test_does_not_touch_a_different_messages_events(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        database.record_pipeline_event(-1001, 43, SignalLifecycleState.RECEIVED)
        database.link_pipeline_events_to_signal(-1001, 42, signal_id=999)
        other = database.list_pipeline_events_for_message(-1001, 43)
        self.assertIsNone(other[0]["signal_id"])

    def test_never_raises_when_the_db_is_unreachable(self):
        database.DB_FILE = Path("Z:/definitely/does/not/exist/anywhere.db")
        try:
            database.link_pipeline_events_to_signal(-1001, 42, signal_id=999)  # must not raise
        finally:
            database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"


class ListPipelineEventsForSignalTests(SignalPipelineEventsTestCase):
    def test_returns_linked_events_in_order(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED, signal_id=999)
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.PARSED, signal_id=999)
        events = database.list_pipeline_events_for_signal(999)
        self.assertEqual([e["state"] for e in events], [SignalLifecycleState.RECEIVED, SignalLifecycleState.PARSED])

    def test_unlinked_events_are_excluded(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)  # no signal_id
        self.assertEqual(database.list_pipeline_events_for_signal(999), [])


class ListRecentPipelineJourneysTests(SignalPipelineEventsTestCase):
    def test_one_row_per_distinct_message_showing_the_latest_state(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.PARSED)
        database.record_pipeline_event(-1001, 43, SignalLifecycleState.RECEIVED)
        journeys = database.list_recent_pipeline_journeys()
        self.assertEqual(len(journeys), 2)
        journey_42 = next(j for j in journeys if j["message_id"] == 42)
        self.assertEqual(journey_42["state"], SignalLifecycleState.PARSED)

    def test_filters_by_state(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.SKIPPED, detail="channel_not_watched")
        database.record_pipeline_event(-1001, 43, SignalLifecycleState.PARSED)
        skipped = database.list_recent_pipeline_journeys(state=SignalLifecycleState.SKIPPED)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["message_id"], 42)

    def test_most_recent_first(self):
        database.record_pipeline_event(-1001, 42, SignalLifecycleState.RECEIVED)
        database.record_pipeline_event(-1001, 43, SignalLifecycleState.RECEIVED)
        journeys = database.list_recent_pipeline_journeys()
        self.assertEqual(journeys[0]["message_id"], 43)

    def test_respects_limit(self):
        for i in range(5):
            database.record_pipeline_event(-1001, i, SignalLifecycleState.RECEIVED)
        self.assertEqual(len(database.list_recent_pipeline_journeys(limit=2)), 2)

    def test_empty_when_nothing_recorded(self):
        self.assertEqual(database.list_recent_pipeline_journeys(), [])


if __name__ == "__main__":
    unittest.main()
