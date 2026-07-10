import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import soak_snapshot


class CountNewErrorLinesTests(unittest.TestCase):
    """core/logger.py rotates axim.log via RotatingFileHandler once it
    hits MAX_BYTES - exactly the kind of event a real multi-hour soak
    test run will hit. Before this fix, count_new_error_lines silently
    reported 0 new errors forever after a rotation (last_count from
    before rotation exceeds the fresh file's line count, so
    lines[last_count:] always returns []), defeating the whole point of
    the script during the one scenario (a long-running soak test) it's
    actually meant to catch."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_log = soak_snapshot.AXIM_LOG
        self._original_state = soak_snapshot.STATE_FILE
        soak_snapshot.AXIM_LOG = Path(self._tmp_dir.name) / "axim.log"
        soak_snapshot.STATE_FILE = Path(self._tmp_dir.name) / ".soak_state"

    def tearDown(self):
        soak_snapshot.AXIM_LOG = self._original_log
        soak_snapshot.STATE_FILE = self._original_state
        self._tmp_dir.cleanup()

    def test_missing_log_returns_zero(self):
        self.assertEqual(soak_snapshot.count_new_error_lines(), 0)

    def test_counts_error_and_critical_lines_on_first_run(self):
        soak_snapshot.AXIM_LOG.write_text(
            "2026-01-01 INFO something happened\n"
            "2026-01-01 ERROR something broke\n"
            "2026-01-01 CRITICAL something very broke\n"
            "2026-01-01 INFO something else happened\n",
            encoding="utf-8",
        )
        self.assertEqual(soak_snapshot.count_new_error_lines(), 2)

    def test_only_counts_lines_added_since_last_run(self):
        soak_snapshot.AXIM_LOG.write_text("2026-01-01 INFO line one\n", encoding="utf-8")
        soak_snapshot.count_new_error_lines()  # establishes the baseline
        soak_snapshot.AXIM_LOG.write_text(
            "2026-01-01 INFO line one\n2026-01-01 ERROR line two\n", encoding="utf-8")
        self.assertEqual(soak_snapshot.count_new_error_lines(), 1)

    def test_survives_log_rotation_instead_of_permanently_reporting_zero(self):
        # simulate many runs' worth of accumulated line count, matching
        # what a real long-running soak test would build up
        soak_snapshot.AXIM_LOG.write_text(
            "\n".join(f"2026-01-01 INFO line {i}" for i in range(5000)) + "\n", encoding="utf-8")
        soak_snapshot.count_new_error_lines()  # last_count is now 5000

        # RotatingFileHandler rolls axim.log -> axim.log.1 and starts a
        # fresh, much shorter axim.log
        soak_snapshot.AXIM_LOG.write_text(
            "2026-01-01 INFO fresh log after rotation\n2026-01-01 ERROR real error post-rotation\n",
            encoding="utf-8",
        )
        self.assertEqual(soak_snapshot.count_new_error_lines(), 1)


if __name__ == "__main__":
    unittest.main()
