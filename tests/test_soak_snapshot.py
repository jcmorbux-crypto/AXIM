import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import soak_snapshot


class CountNewErrorLinesTests(unittest.TestCase):
    """count_new_error_lines() tracks how many lines of core/logger.py's
    axim.log it has already scanned via a stored line count
    (logs/.soak_state), so repeated runs only count genuinely NEW error
    lines. axim.log is a RotatingFileHandler - once it hits MAX_BYTES it
    gets renamed aside and a fresh, empty axim.log starts, meaning the
    stored count from before rotation is larger than the new file's
    actual line count. lines[last_count:] on a list shorter than
    last_count silently returns [] (confirmed via direct Python slicing,
    not assumed) rather than raising - a real bug where every run after a
    rotation reported 0 new errors regardless of what was actually in the
    file, until it happened to regrow past the old count."""

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

    @staticmethod
    def _line(level, message):
        # Matches core/logger.py's real format ("%(asctime)s %(levelname)s
        # [%(name)s] %(message)s") closely enough for count_new_error_lines'
        # own " ERROR "/" CRITICAL " substring check to behave exactly as
        # it does against a real axim.log - a level as the very first word
        # (no leading timestamp) would NOT match that check, since it looks
        # for the level surrounded by spaces, not just present anywhere.
        return f"2026-01-01 00:00:00 {level} [axim.test] {message}"

    def _write_log(self, lines):
        soak_snapshot.AXIM_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_no_log_file_returns_zero(self):
        self.assertEqual(soak_snapshot.count_new_error_lines(), 0)

    def test_first_run_counts_every_error_line_in_the_file(self):
        self._write_log([
            self._line("INFO", "hello"), self._line("ERROR", "something broke"),
            self._line("INFO", "world"), self._line("CRITICAL", "fire"),
        ])
        self.assertEqual(soak_snapshot.count_new_error_lines(), 2)

    def test_second_run_only_counts_lines_appended_since_the_first(self):
        self._write_log([self._line("INFO", "hello"), self._line("ERROR", "something broke")])
        soak_snapshot.count_new_error_lines()  # baseline: 2 lines scanned
        self._write_log([
            self._line("INFO", "hello"), self._line("ERROR", "something broke"),
            self._line("INFO", "world"), self._line("ERROR", "another one"),
        ])
        self.assertEqual(soak_snapshot.count_new_error_lines(), 1)

    def test_rotation_shrinking_the_file_does_not_silently_report_zero(self):
        # Simulates RotatingFileHandler rotating axim.log: a large prior
        # file (many lines already scanned), then a fresh, much shorter
        # file after rotation.
        self._write_log([self._line("INFO", f"line {i}") for i in range(500)] + [self._line("ERROR", "pre-rotation")])
        soak_snapshot.count_new_error_lines()  # baseline: 501 lines scanned

        self._write_log([self._line("INFO", "fresh start"), self._line("ERROR", "right after rotation")])
        # Without the fix, lines[501:] on this 2-line file silently
        # returns [] and this would incorrectly report 0.
        self.assertEqual(soak_snapshot.count_new_error_lines(), 1)

    def test_state_file_correctly_reflects_the_post_rotation_line_count(self):
        self._write_log([self._line("INFO", f"line {i}") for i in range(500)])
        soak_snapshot.count_new_error_lines()

        self._write_log([self._line("INFO", "fresh start"), self._line("ERROR", "right after rotation")])
        soak_snapshot.count_new_error_lines()

        self.assertEqual(int(soak_snapshot.STATE_FILE.read_text()), 2)


if __name__ == "__main__":
    unittest.main()
