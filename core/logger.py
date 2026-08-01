"""
AXIM centralized logging.

Every module gets its logger through get_logger(name, filename) instead of
hand-rolling its own handlers. That gives all of them, for free:

- A rotating, UTF-8 file under logs/<filename> (files no longer grow
  forever - previously nothing rotated).
- A console handler that never crashes on characters the terminal's
  encoding can't represent (Windows cp1252 + emoji/non-ASCII text from
  Telegram messages or the DOM has crashed plain StreamHandler before -
  this was previously only fixed for execution/pocket_dom.py).
- Level controlled by LOG_LEVEL in .env (previously set but never read
  by anything).
- Propagation up to one root "axim" logger, which writes every record -
  regardless of which module logger emitted it - into logs/axim.log.
  Combined with each module's own file, this gives both a focused,
  per-topic view and one true unified stream. The root has no console
  handler of its own, so propagation doesn't double-print to the console.

Safe to call repeatedly - handlers are only attached once per logger name.
"""
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from timeline import get_current_timeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Under pytest, every test file that merely imports a module owning a
# get_logger(...) call (browser_warmup.py, broker_account_manager.py, etc.)
# was writing straight into the SAME logs/lifecycle.log and logs/axim.log
# the live listener process uses - confirmed live: tests/test_browser_warmup.py,
# tests/test_recovery.py, and tests/test_trade_coordinator.py inject canned
# failures (e.g. the literal exception message "boom") through this exact
# logging path, and with no per-test LOG_DIR override (test_logger.py is the
# only file that patches it manually), those synthetic "browser crash" /
# "reconnecting" / "flagged needs_reauth" lines landed in production's log
# files indistinguishable from real ones. That is what made the browser
# crash/reconnect pattern look far more frequent than it actually is - the
# generation counter and reconnect-log counts pulled from those files during
# this investigation were contaminated by every pytest run that happened to
# overlap the observation window. "pytest" in sys.modules (not
# PYTEST_CURRENT_TEST, which pytest only sets once a specific test starts
# running - too late, since get_logger() attaches its handler at import
# time, during collection) is set for the whole process from the moment
# pytest itself starts, before any test file is even imported.
_UNDER_PYTEST = "pytest" in sys.modules
LOG_DIR = PROJECT_ROOT / "logs" / "test" if _UNDER_PYTEST else PROJECT_ROOT / "logs"

_TIMED_LOG_METHODS = ("debug", "info", "warning", "error", "critical", "exception")

MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))
BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))

_FORMATTER = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
_configured = set()


class _ResilientRotatingFileHandler(RotatingFileHandler):
    """2026-07-31 logs/lifecycle.log handler went silently dead at
    19:15:30. CORRECTION (2026-08-01, see set_process_role() below for the
    full account): this class's own initial docstring theorized it had
    "self-healed after 4+ hours" once one-off diagnostic script activity
    dropped off - re-checked live the next morning, it had NOT: still
    frozen, byte-for-byte identical, 14+ hours later. The real, verified
    cause was two permanent co-resident processes (api/main.py and core/
    telegram_listener.py) contending for the same physical file, not a
    transient script collision - fixed at the source by set_process_role()
    below, which this class does not replace. What follows below is still
    accurate as a description of THIS class's own mechanism and remains
    real, useful defense-in-depth for the narrower case it was originally
    written for (a short-lived script's transient collision with another
    short-lived script) - it just isn't what actually fixed the incident.

    Root cause, traced through the stdlib source (logging/handlers.py):
    RotatingFileHandler(delay unset -> False) opens its file the moment
    it's CONSTRUCTED, not the moment it first logs anything - so any
    short-lived process that merely IMPORTS a module owning a get_logger()
    call (core/risk_manager.py, core/database.py, etc. - and this
    codebase has many one-off diagnostic scripts that do exactly that)
    briefly holds its own open handle on the exact same file. On Windows,
    BaseRotatingHandler.doRollover()'s os.rename() step raises OSError if
    ANY process has the file open at that instant - unlike POSIX, Windows
    does not allow renaming an open file out from under another handle.
    stdlib's own doRollover() closes self.stream BEFORE the rename, and
    shouldRollover() reopens self.stream if it's still None on the very
    next call, so this IS supposed to self-heal on the next log call in
    isolation - the 4-hour duration here means the collision was
    effectively continuous (frequent one-off script activity against the
    same file), not a single unlucky moment.

    Two independent mitigations, both real fixes to the actual mechanism
    above (not a workaround for one specific script or theory of exactly
    which process caused this exact incident):
      1. get_logger() below now passes delay=True to every handler this
         class wraps - a process that imports a get_logger()-owning
         module but never actually logs anything (the common case for
         this codebase's read-only diagnostic scripts) no longer opens
         the file at all, sharply cutting how often that transient-handle
         collision can happen in the first place.
      2. This override: doRollover() failing no longer eats the record
         that triggered it, and it never leaves self.stream permanently
         unusable - it explicitly reopens the (still-existing, since the
         rename didn't happen) original file and keeps appending to it,
         retrying the actual rotation on the next maxBytes crossing
         instead. A collision now costs one skipped rotation, never
         silent, indefinite data loss for this file."""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream is None or self.stream.closed:
                self.stream = self._open()


_process_role = None


def set_process_role(role):
    """Splits every rotating log file get_logger()/the "axim" root logger
    creates so this process (role, e.g. "api" or "listener" - the same
    vocabulary core/single_instance.py already uses for its own lock
    files) never shares a physical file with another long-lived AXIM
    process.

    2026-08-01 verified production incident, superseding the "self-healed
    after diagnostic script traffic dropped off" theory in
    _ResilientRotatingFileHandler's own docstring above: logs/lifecycle.log
    stopped growing at 2026-07-31 19:15:30 and was STILL frozen, byte-
    for-byte identical, the next morning - 14+ hours later, not the few
    hours that theory assumed, and with no external process holding an
    exclusive lock on the file by then either. Root cause, traced through
    git history and a live process/import audit: core/telegram_listener.py
    and api/main.py are two independent, long-lived OS processes, and both
    import modules that call get_logger("axim.lifecycle",
    filename="lifecycle.log") - risk_manager.py (imported by api/main.py)
    and browser_warmup.py/browser_worker_pool.py/trade_series_engine.py
    (imported by core/telegram_listener.py) among 21 total call sites.
    Python logging state is per-process, so each process independently
    constructs its OWN RotatingFileHandler pointed at the exact same
    physical file, each holding it open for its entire lifetime. On
    Windows, doRollover()'s os.rename() step raises OSError if ANY other
    process still has the file open at that instant - and since both
    processes are services, not one-off scripts, that condition never
    clears on its own. This only became possible once the API process
    became a second permanent co-resident alongside the listener (the
    recent RC1 reboot-survival / API-supervisor work) - logs/axim.log's
    own pre-existing backups (a .1 from before that change, none since)
    show the same rotation has been silently unable to complete since,
    just not yet forced to try again by crossing MAX_BYTES a second time.

    This is the actual, primary fix for that incident - not a workaround:
    each process now gets its own physical file per logger, so two
    processes can never contend for the same rename. Delay=True and the
    doRollover retry above remain real, useful defense-in-depth for the
    one case this doesn't cover (a short-lived script that never calls
    set_process_role() at all, sharing the un-suffixed filename with
    nothing but other equally-transient scripts).

    Must be called before ANY get_logger() call happens (see api/main.py
    and core/telegram_listener.py's own call sites for the exact
    placement, both immediately after sys.path setup / the single-instance
    guard and before any other project import) - raises RuntimeError if a
    logger has already been configured, since that handler's filename is
    already fixed and can't be retroactively split."""
    global _process_role
    if _configured:
        raise RuntimeError(
            "set_process_role() must be called before any get_logger() call - "
            f"{len(_configured)} logger(s) already configured with the un-suffixed "
            "filename, which can't be retroactively changed."
        )
    _process_role = role


def _role_suffixed(filename):
    """"lifecycle.log" -> "lifecycle-api.log" once set_process_role("api")
    has run; unchanged when no role has been set (scripts, tests, and any
    other short-lived, single-instance-at-a-time process)."""
    if not _process_role:
        return filename
    path = Path(filename)
    return f"{path.stem}-{_process_role}{path.suffix}"


class _SafeStreamHandler(logging.StreamHandler):
    """Like StreamHandler, but replaces characters the console's encoding
    can't represent instead of raising (and taking the process down)."""

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            encoding = getattr(stream, "encoding", None) or "utf-8"
            stream.write(msg.encode(encoding, errors="replace").decode(encoding, errors="replace"))
            stream.write(self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def _resolve_level():
    return getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)


def _time_log_calls(logger):
    """Wraps a logger's info/warning/error/etc. bound methods so every call
    anywhere in the codebase contributes measured wall time to the current
    trade's "logging" category (core/timeline.py), with zero changes needed
    at any of the many existing logger.info(...) call sites. A no-op
    measurement when no trade timeline is active (e.g. startup logging)."""
    for method_name in _TIMED_LOG_METHODS:
        original = getattr(logger, method_name)

        def make_wrapper(original=original):
            def wrapper(*args, **kwargs):
                t0 = time.monotonic()
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    timeline = get_current_timeline()
                    if timeline is not None:
                        timeline.add_time("logging", elapsed_ms)
            return wrapper

        setattr(logger, method_name, make_wrapper())


_ROOT_MARKER = "\x00axim_root\x00"  # can never collide with a real logger
                                     # name (get_logger's own "name not in
                                     # _configured" check shares this same
                                     # set) - previously the literal "axim",
                                     # which meant a hypothetical future
                                     # get_logger("axim") call (no dotted
                                     # sub-name, unlike every current real
                                     # call site) would have silently
                                     # skipped its own console/rotation
                                     # setup, since _attach_root() runs
                                     # first and would have already marked
                                     # that exact name as configured. Never
                                     # actually triggered (grepped for it),
                                     # fixed as a cheap precaution anyway.


def _attach_root():
    if _ROOT_MARKER in _configured:
        return
    _configured.add(_ROOT_MARKER)
    root = logging.getLogger("axim")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = _ResilientRotatingFileHandler(
        LOG_DIR / _role_suffixed("axim.log"), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(_FORMATTER)
    root.addHandler(handler)
    root.setLevel(_resolve_level())


def get_logger(name, filename=None, console=True):
    """
    name: logger name, e.g. "axim.lifecycle" (may be shared by several
        modules that want their records interleaved in one file).
    filename: log file under logs/, defaults to "<last segment of name>.log".
    console: set False for file-only logging (default True).
    """
    _attach_root()

    logger = logging.getLogger(name)
    if name not in _configured:
        _configured.add(name)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _role_suffixed(filename or f"{name.rsplit('.', 1)[-1]}.log")

        file_handler = _ResilientRotatingFileHandler(
            LOG_DIR / log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(_FORMATTER)
        logger.addHandler(file_handler)

        if console:
            logger.addHandler(_SafeStreamHandler())

        logger.setLevel(_resolve_level())
        logger.propagate = True  # bubbles up to "axim" root -> logs/axim.log
        _time_log_calls(logger)

    return logger
