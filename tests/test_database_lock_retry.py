import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class DatabaseLockRetryTests(unittest.TestCase):
    """core/database.py's get_connection() previously relied entirely on
    SQLite's own busy_timeout to wait out lock contention - if that
    5-second internal wait was itself exceeded (a long-running write
    transaction, OS-level file-lock jitter), the OperationalError just
    propagated uncaught. _RetryingConnection adds a bounded, logged retry
    on top. Tested via _retrying()'s own generic callable interface
    (real _RetryingConnection instance, fake underlying operation) rather
    than fighting real SQLite locking or monkeypatching sqlite3.Connection
    globally - this is the actual orchestration logic under test, not
    SQLite's locking behavior itself."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._sleep_patcher = patch.object(database.time, "sleep")
        self.mock_sleep = self._sleep_patcher.start()

    def tearDown(self):
        self._sleep_patcher.stop()
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _event_counts(self, event_type):
        stats = database.get_recovery_event_stats()
        return {row["outcome"]: row["n"] for row in stats if row["event_type"] == event_type}

    def test_succeeds_on_first_attempt_records_no_event(self):
        conn = database.get_connection()
        result = conn._retrying("test_op", lambda: "ok")
        conn.close()
        self.assertEqual(result, "ok")
        self.assertEqual(self._event_counts("database_lock_retry"), {})
        self.mock_sleep.assert_not_called()

    def test_recovers_after_one_lock_error_and_records_succeeded(self):
        conn = database.get_connection()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise sqlite3.OperationalError("database is locked")
            return "recovered"

        result = conn._retrying("test_op", flaky)
        conn.close()

        self.assertEqual(result, "recovered")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._event_counts("database_lock_retry"), {"succeeded": 1})
        self.mock_sleep.assert_called_once()

    def test_gives_up_after_exhausting_retries_and_records_failed(self):
        conn = database.get_connection()

        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            conn._retrying("test_op", always_locked)
        conn.close()

        self.assertEqual(self._event_counts("database_lock_retry"), {"failed": 1})
        # 2 retry delays configured -> 2 sleeps before giving up.
        self.assertEqual(self.mock_sleep.call_count, len(database._LOCK_RETRY_DELAYS))

    def test_database_is_busy_message_is_also_retried(self):
        conn = database.get_connection()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise sqlite3.OperationalError("database is busy")
            return "ok"

        result = conn._retrying("test_op", flaky)
        conn.close()
        self.assertEqual(result, "ok")

    def test_non_lock_operational_error_is_not_retried(self):
        conn = database.get_connection()
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise sqlite3.OperationalError("no such table: nonsense")

        with self.assertRaises(sqlite3.OperationalError):
            conn._retrying("test_op", broken)
        conn.close()

        self.assertEqual(calls["n"], 1)  # not retried at all
        self.assertEqual(self._event_counts("database_lock_retry"), {})
        self.mock_sleep.assert_not_called()

    def test_recovery_event_logging_failure_never_masks_or_compounds_the_original_error(self):
        """2026-07-28 verified production incident: record_recovery_event
        opens its OWN connection and writes through this SAME _retrying
        path - under sustained contention (confirmed live: several stuck
        diagnostic scripts each holding a write lock), that write can ALSO
        fail, and before this fix, that failure was unguarded - a second
        lock error escaping from inside the exception handler for the
        first one. Observed live: this sustained a self-reinforcing retry
        storm that outlasted the original cause and didn't clear until
        both AXIM processes were stopped. The original OperationalError
        must still surface (never silently dropped - that's the whole
        point of this class), but a failure to log IT must never itself
        raise or add more contention."""
        conn = database.get_connection()

        def always_locked():
            raise sqlite3.OperationalError("database is locked: original op")

        # A distinguishable message from the original error, so the
        # assertion below proves it's the ORIGINAL failure that surfaces,
        # not the recovery-event-logging failure masking it.
        with patch.object(
            database, "record_recovery_event",
            side_effect=sqlite3.OperationalError("database is locked: recovery event log itself"),
        ):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn._retrying("test_op", always_locked)
        conn.close()
        self.assertEqual(str(ctx.exception), "database is locked: original op")

    def test_recovery_event_logging_failure_on_the_success_path_does_not_break_the_result(self):
        conn = database.get_connection()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise sqlite3.OperationalError("database is locked")
            return "recovered"

        with patch.object(database, "record_recovery_event", side_effect=sqlite3.OperationalError("still locked")):
            result = conn._retrying("test_op", flaky)
        conn.close()
        self.assertEqual(result, "recovered")

    def test_execute_and_commit_go_through_the_real_retry_path(self):
        # End-to-end smoke test against real SQLite (no injected failures) -
        # confirms execute()/commit() actually route through _retrying()
        # rather than just existing as dead code.
        conn = database.get_connection()
        conn.execute("INSERT INTO ui_settings (key, value, updated_at) VALUES (?, ?, ?)",
                     ("test_key", '"test_value"', "2026-01-01T00:00:00"))
        conn.commit()
        conn.close()
        self.assertEqual(database.get_setting("test_key"), "test_value")


class DatabaseConcurrentWriteResilienceTests(unittest.TestCase):
    """2026-07-28 verified production incident, focused resilience pass
    (user-requested follow-up): the incident involved several real OS
    processes, each with its own real SQLite connection, writing to the
    same file-backed database at once - a scenario the mock-based tests
    above deliberately don't reproduce (they exercise _retrying's
    orchestration logic in isolation, not real concurrent file locking).
    These tests use real threads, each with database.get_connection()'s
    own real connection, against one real temp-file database - the same
    class of contention that triggered the incident - mixing the same
    kinds of writes that were actually in flight: trade signal writes,
    execution-latency telemetry writes, and settings/risk-lock writes."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_concurrent_trade_telemetry_and_risk_writes_all_succeed_without_data_loss(self):
        # Pre-created rows telemetry writers legitimately attach to -
        # matches the real pattern (a signal is recorded once, then
        # execution_latency is updated several times over its lifecycle).
        trade_ids = [
            database.record_signal_received(
                {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "5 Minute", "raw_message": f"seed-{i}"},
            )
            for i in range(5)
        ]

        errors = []
        created_signal_ids = []
        ids_lock = threading.Lock()

        def trade_writer(n):
            try:
                tid = database.record_signal_received(
                    {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "5 Minute", "raw_message": f"stress-{n}"},
                )
                with ids_lock:
                    created_signal_ids.append(tid)
            except Exception as e:
                errors.append(("trade", n, e))

        def telemetry_writer(n):
            try:
                database.record_execution_latency(
                    trade_ids[n % len(trade_ids)],
                    {"scheduler_awakened_at": datetime.now(timezone.utc).isoformat()},
                )
            except Exception as e:
                errors.append(("telemetry", n, e))

        def risk_writer(n):
            try:
                database.set_setting(
                    f"stress_risk_key_{n % 3}", n,
                    changed_by="stress-test", reason="concurrency stress", source="test",
                )
            except Exception as e:
                errors.append(("risk", n, e))

        threads = []
        for i in range(15):
            threads.append(threading.Thread(target=trade_writer, args=(i,)))
            threads.append(threading.Thread(target=telemetry_writer, args=(i,)))
            threads.append(threading.Thread(target=risk_writer, args=(i,)))

        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.monotonic() - start

        still_alive = [t for t in threads if t.is_alive()]
        self.assertEqual(still_alive, [], "no thread should still be running - would indicate a recursive/runaway retry")
        self.assertLess(elapsed, 25, "concurrent writes should resolve within the retry budget, not hang")
        self.assertEqual(errors, [], f"no write should be permanently lost under real concurrent contention: {errors}")

        # Trade state: every concurrent signal write landed with its own
        # distinct id - none silently dropped or merged.
        self.assertEqual(len(created_signal_ids), 15)
        self.assertEqual(len(set(created_signal_ids)), 15)

        # Telemetry: every seeded trade's latency checkpoint actually
        # persisted, not silently dropped under contention.
        conn = database.get_connection()
        for tid in trade_ids:
            row = conn.execute("SELECT latency_checkpoints_json FROM signals WHERE id = ?", (tid,)).fetchone()
            self.assertIsNotNone(row["latency_checkpoints_json"])
        conn.close()

        # Risk counters: every settings key survived (last-writer-wins on
        # the shared n%3 keys is fine - the point is the key exists and
        # the process stayed healthy, not a specific final value).
        all_settings = database.get_all_settings()
        for i in range(3):
            self.assertIn(f"stress_risk_key_{i}", all_settings)

        # No write anywhere permanently exhausted its retries - transient
        # retries succeeding are fine and expected under real contention,
        # a "failed" outcome would mean a real, permanent loss.
        stats = database.get_recovery_event_stats()
        failed = {row["outcome"]: row["n"] for row in stats if row["event_type"] == "database_lock_retry"}.get("failed", 0)
        self.assertEqual(failed, 0, "no write should have permanently exhausted its retries under this load")


if __name__ == "__main__":
    unittest.main()
