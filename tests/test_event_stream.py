import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import event_stream
import risk_manager
import trade_coordinator
from event_bus import EventBus
from trade_coordinator import TradeCoordinator


class ServerEventOutboxTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_record_and_list_since(self):
        database.record_server_event("trade.closed", {"trade_id": 1})
        database.record_server_event("trade.closed", {"trade_id": 2})
        rows = database.list_server_events_since(0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["payload"], {"trade_id": 1})
        self.assertEqual(rows[1]["payload"], {"trade_id": 2})

    def test_list_since_only_returns_newer_rows(self):
        first_id = database.record_server_event("trade.closed", {"trade_id": 1})
        database.record_server_event("trade.closed", {"trade_id": 2})
        rows = database.list_server_events_since(first_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"], {"trade_id": 2})

    def test_payload_none_round_trips_as_none(self):
        database.record_server_event("notification.created")
        rows = database.list_server_events_since(0)
        self.assertIsNone(rows[0]["payload"])

    def test_ids_are_monotonic_and_usable_as_resume_cursor(self):
        ids = [database.record_server_event("trade.closed", {"i": i}) for i in range(5)]
        self.assertEqual(ids, sorted(ids))

    def test_oldest_server_event_id(self):
        self.assertIsNone(database.oldest_server_event_id())
        first_id = database.record_server_event("trade.closed", {"i": 1})
        database.record_server_event("trade.closed", {"i": 2})
        self.assertEqual(database.oldest_server_event_id(), first_id)

    def test_prune_removes_only_old_rows(self):
        conn = database.get_connection()
        old_time = (datetime.now() - timedelta(hours=100)).isoformat()
        conn.execute(
            "INSERT INTO server_events (event_type, payload_json, created_at) VALUES (?, ?, ?)",
            ("trade.closed", None, old_time),
        )
        conn.commit()
        conn.close()
        database.record_server_event("trade.closed", {"i": "recent"})
        self.assertEqual(len(database.list_server_events_since(0)), 2)
        database.prune_server_events(older_than_hours=72)
        remaining = database.list_server_events_since(0)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["payload"], {"i": "recent"})

    def test_create_notification_also_writes_a_server_event(self):
        user_id = database.create_user("a@axim.local", "password123")
        database.create_notification(user_id, "hello")
        rows = database.list_server_events_since(0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "notification.created")
        self.assertEqual(rows[0]["payload"]["message"], "hello")


class EventStreamBridgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_register_bridges_all_five_known_events(self):
        bus = EventBus()
        event_stream.register(bus)
        for event_name in event_stream._BRIDGED_EVENTS:
            self._run(bus.publish(event_name, {"marker": event_name}))
        rows = database.list_server_events_since(0)
        self.assertEqual(len(rows), len(event_stream._BRIDGED_EVENTS))
        recorded_types = {r["event_type"] for r in rows}
        self.assertEqual(recorded_types, set(event_stream._BRIDGED_EVENTS))

    def test_unregistered_event_names_are_not_bridged(self):
        bus = EventBus()
        event_stream.register(bus)
        self._run(bus.publish("some.other.event", {"x": 1}))
        self.assertEqual(database.list_server_events_since(0), [])

    def test_a_failing_writer_does_not_break_the_bus(self):
        """database.record_server_event raising must not prevent OTHER
        subscribers on the same event from running - event_bus.publish
        already catches per-subscriber exceptions; this proves
        event_stream's writer doesn't somehow escape that."""
        bus = EventBus()
        event_stream.register(bus)
        other_calls = []
        bus.subscribe("trade.closed", lambda payload: other_calls.append(payload))
        original_db_file = database.DB_FILE
        database.DB_FILE = Path("/nonexistent/path/that/cannot/be/opened/axim.db")
        try:
            self._run(bus.publish("trade.closed", {"trade_id": 1}))
        finally:
            database.DB_FILE = original_db_file
        self.assertEqual(other_calls, [{"trade_id": 1}])


class RealPipelineIntegrationTests(unittest.TestCase):
    """Proves the bridge works through the REAL trade_coordinator
    pipeline (preview mode, no real broker touched), not just a
    hand-crafted bus.publish() call - the same PREVIEW_ONLY/FakeWorkerPool
    pattern tests/test_trade_coordinator.py already establishes as this
    codebase's way of exercising the real pipeline without external
    credentials."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self._original_preview_only = trade_coordinator.PREVIEW_ONLY
        self._original_max_trade_amount = risk_manager.MAX_TRADE_AMOUNT
        trade_coordinator.PREVIEW_ONLY = True
        risk_manager.MAX_TRADE_AMOUNT = 50

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        trade_coordinator.PREVIEW_ONLY = self._original_preview_only
        risk_manager.MAX_TRADE_AMOUNT = self._original_max_trade_amount

    def test_a_real_signal_through_the_pipeline_lands_in_server_events(self):
        bus = EventBus()
        event_stream.register(bus)
        coordinator = TradeCoordinator(worker_pool=None, warmup_service=None, event_bus=bus)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}

        result = asyncio.run(coordinator.handle_signal(signal))

        self.assertEqual(result["status"], "preview")
        rows = database.list_server_events_since(0)
        event_types = [r["event_type"] for r in rows]
        self.assertIn("trade.signal_received", event_types)
        signal_row = next(r for r in rows if r["event_type"] == "trade.signal_received")
        self.assertEqual(signal_row["payload"]["signal"]["asset"], "EUR/USD OTC")


if __name__ == "__main__":
    unittest.main()
