import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
import log_reader


class LogParsingTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_log_dir = log_reader.LOG_DIR
        log_reader.LOG_DIR = Path(self._tmp_dir.name)
        self._original_log_files = log_reader.LOG_FILES
        log_reader.LOG_FILES = ["test.log"]
        # read_logs() also merges in admin_actions from the real DB -
        # isolate that too, or a leftover row from the production
        # database would break the "empty" assertions below.
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        log_reader.LOG_DIR = self._original_log_dir
        log_reader.LOG_FILES = self._original_log_files
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _write_log(self, content):
        (log_reader.LOG_DIR / "test.log").write_text(content, encoding="utf-8")

    def test_parses_simple_lines(self):
        self._write_log(
            "2026-07-06 10:00:00,123 INFO [axim.lifecycle] first message\n"
            "2026-07-06 10:00:01,456 ERROR [axim.ui] second message\n"
        )
        entries = log_reader.read_logs()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["message"], "second message")  # newest first
        self.assertEqual(entries[0]["level"], "ERROR")
        self.assertEqual(entries[1]["module"], "axim.lifecycle")

    def test_multiline_message_grouped_into_one_entry(self):
        self._write_log(
            "2026-07-06 10:00:00,123 ERROR [axim.lifecycle] failure detail:\n"
            "extra traceback line 1\n"
            "extra traceback line 2\n"
        )
        entries = log_reader.read_logs()
        self.assertEqual(len(entries), 1)
        self.assertIn("extra traceback line 1", entries[0]["message"])
        self.assertIn("extra traceback line 2", entries[0]["message"])

    def test_filter_by_level(self):
        self._write_log(
            "2026-07-06 10:00:00,123 INFO [axim.lifecycle] info message\n"
            "2026-07-06 10:00:01,456 ERROR [axim.lifecycle] error message\n"
        )
        entries = log_reader.read_logs(level="ERROR")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["message"], "error message")

    def test_filter_by_module_substring(self):
        self._write_log(
            "2026-07-06 10:00:00,123 INFO [axim.lifecycle] a\n"
            "2026-07-06 10:00:01,456 INFO [axim.ui] b\n"
        )
        entries = log_reader.read_logs(module="ui")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["message"], "b")

    def test_filter_by_search_text(self):
        self._write_log(
            "2026-07-06 10:00:00,123 INFO [axim.lifecycle] STAGE trade_id=5 channel=GoPlus\n"
            "2026-07-06 10:00:01,456 INFO [axim.lifecycle] STAGE trade_id=6 channel=Other\n"
        )
        entries = log_reader.read_logs(search="GoPlus")
        self.assertEqual(len(entries), 1)
        self.assertIn("trade_id=5", entries[0]["message"])

    def test_filter_by_date_range(self):
        self._write_log(
            "2026-07-01 10:00:00,123 INFO [axim.lifecycle] old\n"
            "2026-07-06 10:00:01,456 INFO [axim.lifecycle] new\n"
        )
        entries = log_reader.read_logs(since="2026-07-05T00:00:00")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["message"], "new")

    def test_missing_file_returns_empty(self):
        entries = log_reader.read_logs()
        self.assertEqual(entries, [])

    def test_limit_applied(self):
        lines = "".join(f"2026-07-06 10:00:{i:02d},000 INFO [axim.lifecycle] msg{i}\n" for i in range(10))
        self._write_log(lines)
        entries = log_reader.read_logs(limit=3)
        self.assertEqual(len(entries), 3)


if __name__ == "__main__":
    unittest.main()
