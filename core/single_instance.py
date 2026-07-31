"""OS-level single-instance guard (2026-07-29 RC1 reboot-survival audit).

Why this exists: AXIM's two long-running processes (core/telegram_listener.py,
api/main.py under uvicorn) are each started by a Windows Scheduled Task with
MultipleInstances=IgnoreNew, which stops Task Scheduler itself from double-
launching the SAME task - but nothing previously stopped a second copy
started outside Task Scheduler (a manually-run terminal command, a second
interactive logon session/RDP triggering the same AtLogon trigger while a
stale instance from before a crash was still alive, an operator re-running
the install/start script). Two listeners fighting over the same Pocket
Option browser profile, or two uvicorn servers on the same port, is exactly
the kind of duplicate-instance risk that produced the orphan-process
incidents this audit investigated.

Uses msvcrt.locking (Windows stdlib, no new dependency) on a small lock
file rather than a PID file: a PID file can go stale (the recorded PID
exits, gets reused by an unrelated process, and the file is still there
implying a false positive) and needs its own cleanup logic. An OS-level
lock has none of that - it is held only as long as the process's file
handle is open, and Windows closes that handle (releasing the lock)
automatically on ANY process exit, including a hard kill or crash. No
stale-lock case to handle.
"""
import atexit
import os
import sys
from pathlib import Path

import msvcrt

_held_locks = {}


def acquire_or_exit(name: str, project_root) -> None:
    """Acquires the named lock or exits the process immediately (code 1) if
    another instance already holds it. Call this as the first thing a
    long-running AXIM entrypoint does, before any other startup work."""
    lock_dir = Path(project_root) / "data"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"

    if not lock_path.exists():
        lock_path.write_bytes(b"0")

    fh = open(lock_path, "r+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        print(
            f"AXIM: another '{name}' process already holds {lock_path} - "
            f"exiting so this instance doesn't run alongside it.",
            file=sys.stderr,
        )
        sys.exit(1)

    # "r+" (not "a+"): "a"/append mode forces every write to the true end
    # of the file regardless of seek() position, so a prior "a+" version of
    # this function silently appended each new holder's PID onto whatever
    # was already there instead of overwriting it - the file grew forever
    # and stopped meaning "the current holder's PID" (found via a reboot
    # audit: lock file mtimes were stale even though fresh processes had
    # just re-acquired the lock).
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _held_locks[name] = fh  # module-level ref: keeps the fd (and lock) open for process lifetime

    def _release():
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            fh.close()
        except Exception:
            pass

    atexit.register(_release)
