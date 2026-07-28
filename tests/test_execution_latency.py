import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
from execution_latency import ExecutionLatency, FIELDS


class ExecutionLatencyRecordTests(unittest.TestCase):
    def test_mark_records_now_by_default(self):
        latency = ExecutionLatency(series_id=1, entry_number=1)
        before = datetime.now(timezone.utc)
        latency.mark("worker_requested_at")
        after = datetime.now(timezone.utc)
        recorded = datetime.fromisoformat(latency.timestamps["worker_requested_at"])
        self.assertGreaterEqual(recorded, before)
        self.assertLessEqual(recorded, after)

    def test_mark_accepts_an_explicit_timestamp(self):
        latency = ExecutionLatency()
        fixed = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
        latency.mark("worker_acquired_at", at=fixed)
        self.assertEqual(latency.timestamps["worker_acquired_at"], fixed.isoformat())

    def test_unknown_field_is_rejected(self):
        latency = ExecutionLatency()
        with self.assertRaises(ValueError):
            latency.mark("not_a_real_field")

    def test_set_scheduled_boundary_records_a_future_target_not_now(self):
        latency = ExecutionLatency()
        future = datetime.now(timezone.utc) + timedelta(minutes=3)
        latency.set_scheduled_boundary(future)
        self.assertEqual(latency.timestamps["scheduled_boundary_at"], future.isoformat())

    def test_all_fields_are_recognized(self):
        latency = ExecutionLatency()
        for field in FIELDS:
            latency.mark(field)
        self.assertEqual(len(latency.timestamps), len(FIELDS))

    def test_metrics_ms_computes_all_six_when_fully_populated(self):
        latency = ExecutionLatency()
        base = datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)
        latency.set_scheduled_boundary(base)
        latency.mark("scheduler_awakened_at", at=base - timedelta(seconds=19))
        latency.mark("worker_requested_at", at=base - timedelta(seconds=19))
        latency.mark("worker_acquired_at", at=base - timedelta(seconds=18, milliseconds=950))
        latency.mark("browser_command_started_at", at=base - timedelta(seconds=18))
        latency.mark("order_payload_sent_at", at=base + timedelta(milliseconds=10))
        latency.mark("broker_acknowledged_at", at=base + timedelta(milliseconds=260))
        latency.mark("broker_trade_closed_at", at=base + timedelta(seconds=300))
        latency.mark("result_detected_at", at=base + timedelta(seconds=300, milliseconds=200))

        metrics = latency.metrics_ms()
        # Negative is CORRECT and expected here: 2026-07-27's precision-
        # latency redesign deliberately wakes the scheduler ~20s BEFORE
        # the boundary (pre-staging), not after it - a positive value
        # would mean the OLD coarse-poll-noticed-it-late behavior this
        # redesign exists to eliminate.
        self.assertAlmostEqual(metrics["scheduler_lateness_ms"], -19000, delta=1)
        self.assertAlmostEqual(metrics["worker_acquisition_ms"], 50, delta=1)
        self.assertAlmostEqual(metrics["browser_command_ms"], 18010, delta=1)
        self.assertAlmostEqual(metrics["broker_acknowledgement_ms"], 250, delta=1)
        self.assertAlmostEqual(metrics["total_boundary_to_broker_acceptance_ms"], 260, delta=1)
        self.assertAlmostEqual(metrics["broker_close_to_result_detection_ms"], 200, delta=1)

    def test_metrics_ms_is_none_for_any_pair_not_yet_recorded(self):
        latency = ExecutionLatency()
        latency.set_scheduled_boundary(datetime.now(timezone.utc))
        metrics = latency.metrics_ms()
        self.assertIsNone(metrics["worker_acquisition_ms"])
        self.assertIsNone(metrics["broker_acknowledgement_ms"])
        self.assertIsNone(metrics["broker_close_to_result_detection_ms"])
        # scheduled_boundary_at is set, but scheduler_awakened_at is not.
        self.assertIsNone(metrics["scheduler_lateness_ms"])
        self.assertIsNone(metrics["boundary_to_rejection_ms"])

    def test_boundary_to_rejection_ms_distinguishes_fast_from_slow_rejections(self):
        base = datetime(2026, 7, 27, 12, 5, 0, tzinfo=timezone.utc)
        fast = ExecutionLatency()
        fast.set_scheduled_boundary(base)
        fast.mark("rejected_at", at=base + timedelta(milliseconds=150))
        self.assertAlmostEqual(fast.metrics_ms()["boundary_to_rejection_ms"], 150, delta=1)

        slow = ExecutionLatency()
        slow.set_scheduled_boundary(base)
        slow.mark("rejected_at", at=base + timedelta(seconds=37))
        self.assertAlmostEqual(slow.metrics_ms()["boundary_to_rejection_ms"], 37000, delta=1)

    def test_to_dict_always_flags_broker_timestamps_as_not_authoritative(self):
        """Verified limitation (core/execution_latency.py's own module
        docstring): Pocket Option exposes no sub-minute broker timestamp
        anywhere AXIM can read - this must never silently flip to True."""
        latency = ExecutionLatency(series_id=7, entry_number=2)
        latency.mark("broker_acknowledged_at")
        d = latency.to_dict()
        self.assertFalse(d["broker_timestamp_authoritative"])
        self.assertEqual(d["series_id"], 7)
        self.assertEqual(d["entry_number"], 2)


class RecordExecutionLatencyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _trade_id(self):
        return database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "5 Minute", "raw_message": "test"},
        )

    def test_record_execution_latency_persists_to_the_signals_row(self):
        trade_id = self._trade_id()
        database.record_execution_latency(trade_id, {"scheduler_awakened_at": "2026-07-27T12:00:00+00:00"})
        conn = database.get_connection()
        row = conn.execute("SELECT latency_checkpoints_json FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        stored = json.loads(row["latency_checkpoints_json"])
        self.assertEqual(stored["scheduler_awakened_at"], "2026-07-27T12:00:00+00:00")

    def test_record_execution_latency_merges_rather_than_overwrites(self):
        trade_id = self._trade_id()
        database.record_execution_latency(trade_id, {"scheduler_awakened_at": "2026-07-27T12:00:00+00:00"})
        database.record_execution_latency(trade_id, {"worker_acquired_at": "2026-07-27T12:00:00.050000+00:00"})
        conn = database.get_connection()
        row = conn.execute("SELECT latency_checkpoints_json FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        stored = json.loads(row["latency_checkpoints_json"])
        self.assertIn("scheduler_awakened_at", stored, "the first call's field must survive the second call")
        self.assertIn("worker_acquired_at", stored)

    def test_record_execution_latency_a_later_call_can_update_a_field(self):
        trade_id = self._trade_id()
        database.record_execution_latency(trade_id, {"worker_requested_at": "2026-07-27T12:00:00+00:00"})
        database.record_execution_latency(trade_id, {"worker_requested_at": "2026-07-27T12:00:01+00:00"})
        conn = database.get_connection()
        row = conn.execute("SELECT latency_checkpoints_json FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        stored = json.loads(row["latency_checkpoints_json"])
        self.assertEqual(stored["worker_requested_at"], "2026-07-27T12:00:01+00:00")


if __name__ == "__main__":
    unittest.main()
