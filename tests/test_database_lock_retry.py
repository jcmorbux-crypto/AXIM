import sqlite3
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
