"""core/single_instance.py - RC1 reboot-survival audit (2026-07-29).

Verifies the OS-level lock actually stops a second real process from
acquiring the same named lock while a first one holds it, and that the
lock is released (letting a subsequent process through) once the holder
exits - including a hard kill, not just a clean exit. Exercised via real
subprocesses rather than calling acquire_or_exit() twice in-process,
because msvcrt.locking's semantics are about cross-process exclusion -
a same-process double-lock wouldn't prove anything about the actual
duplicate-instance scenario this guards against.
"""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"

_HOLDER_SCRIPT = """
import sys
sys.path.insert(0, r"{core_dir}")
import single_instance
single_instance.acquire_or_exit("test_lock", r"{project_root}")
import os
print("ACQUIRED", os.getpid(), flush=True)
import time
time.sleep({hold_seconds})
"""


class SingleInstanceGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.project_root = self._tmp_dir.name

    def tearDown(self):
        self._tmp_dir.cleanup()

    def _spawn_holder(self, hold_seconds):
        script = _HOLDER_SCRIPT.format(
            core_dir=str(CORE_DIR), project_root=self.project_root, hold_seconds=hold_seconds,
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        # Wait for confirmed acquisition rather than a fixed sleep, so this
        # isn't flaky under slow CI/interpreter-startup conditions.
        deadline = time.time() + 10
        while time.time() < deadline:
            line = proc.stdout.readline()
            if "ACQUIRED" in line:
                # proc.pid is the pid Popen launched, but on this venv's
                # python.exe that can be a launcher stub distinct from the
                # actual interpreter's own os.getpid() - use the pid the
                # script reports about itself wherever the true pid matters
                # (e.g. matching lock-file content).
                proc.reported_pid = int(line.split()[1])
                return proc
        proc.kill()
        self.fail("holder process never reported ACQUIRED")

    def test_second_process_is_refused_the_lock(self):
        holder = self._spawn_holder(hold_seconds=5)
        try:
            second = subprocess.run(
                [sys.executable, "-c", _HOLDER_SCRIPT.format(
                    core_dir=str(CORE_DIR), project_root=self.project_root, hold_seconds=0,
                )],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(second.returncode, 1)
            self.assertIn("already holds", second.stderr)
        finally:
            holder.kill()
            holder.wait(timeout=10)

    def test_lock_file_holds_only_current_holders_pid(self):
        """Regression for an "a+" mode bug: seek(0)+write() in append mode
        is forced back to end-of-file by the OS, so a prior holder's PID
        stayed in the file and the next holder's PID was appended after it
        instead of replacing it. The file must contain exactly the most
        recent holder's PID, never a concatenation of past holders'.

        Reads happen only after each holder has fully exited (and released
        its byte-range lock) - Windows record locks are mandatory, so
        reading the locked byte from a second handle while still held
        raises PermissionError, which isn't what this test is checking.
        """
        first = self._spawn_holder(hold_seconds=0)
        first.wait(timeout=10)

        second = self._spawn_holder(hold_seconds=0)
        second.wait(timeout=10)

        lock_path = Path(self.project_root) / "data" / "test_lock.lock"
        content = lock_path.read_text().strip()
        self.assertEqual(content, str(second.reported_pid))

    def test_lock_is_released_after_holder_is_killed(self):
        holder = self._spawn_holder(hold_seconds=60)
        holder.kill()  # simulates a crash/forced-terminate, not a clean exit
        holder.wait(timeout=10)

        second = subprocess.run(
            [sys.executable, "-c", _HOLDER_SCRIPT.format(
                core_dir=str(CORE_DIR), project_root=self.project_root, hold_seconds=0,
            )],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(second.returncode, 0)
        self.assertIn("ACQUIRED", second.stdout)


if __name__ == "__main__":
    unittest.main()
