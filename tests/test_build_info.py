import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import build_info
import database


class BuildInfoTests(unittest.TestCase):
    """2026-07-25 Execution Reliability lesson: a long-running process
    (telegram_listener.py, the API server) keeps executing whatever was
    loaded in memory when it started, even after new commits land on
    disk. Confirmed live this session: the listener ran 5 days of stale
    code across this entire session's fixes before this was caught.
    These tests lock in the mechanism that makes that fact queryable
    instead of assumed."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_get_repo_head_commit_returns_a_real_looking_hash(self):
        # This repo IS a real git checkout in every environment these
        # tests run in - a 40-char hex string, not None, is the only
        # honest expectation here.
        commit = build_info.get_repo_head_commit()
        self.assertIsNotNone(commit)
        self.assertEqual(len(commit), 40)
        int(commit, 16)  # raises ValueError if not valid hex

    def test_record_process_startup_persists_commit_and_timestamp(self):
        commit = build_info.record_process_startup(database, "listener")
        self.assertEqual(database.get_setting("listener_running_commit", default=None), commit)
        self.assertIsNotNone(database.get_setting("listener_started_at", default=None))

    def test_record_process_startup_is_attributed_to_the_process_name(self):
        # Two different processes recording startup must never clobber
        # each other's own commit/timestamp - the entire point is being
        # able to tell "is the API current" independently of "is the
        # listener current".
        build_info.record_process_startup(database, "api")
        build_info.record_process_startup(database, "listener")
        self.assertIsNotNone(database.get_setting("api_running_commit", default=None))
        self.assertIsNotNone(database.get_setting("listener_running_commit", default=None))


if __name__ == "__main__":
    unittest.main()
