import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))


class GetLoggerRootNameCollisionTests(unittest.TestCase):
    """_attach_root() and get_logger() share one _configured set to avoid
    double-attaching handlers on repeat calls. _attach_root() used to mark
    itself done using the literal string "axim" - the exact name a logger
    would have if ever created via get_logger("axim") with no dotted
    sub-name (every real call site in this codebase uses one, e.g.
    "axim.lifecycle", but nothing enforced that). Since _attach_root()
    always runs first inside get_logger(), that call would have found
    "axim" already marked configured and silently skipped attaching its
    own file/console handlers - a real bug, just never triggered in
    practice. Fixed by giving _attach_root() its own marker that can never
    collide with a real logger name."""

    _TEST_LOGGER_NAMES = ("axim", "axim.test_logger_collision")

    def _close_and_remove_test_loggers(self):
        # RotatingFileHandler keeps its log file open - Windows can't
        # delete/clean up the temp dir underneath it until every handler
        # this test attached is explicitly closed and detached first.
        for name in self._TEST_LOGGER_NAMES:
            log = logging.Logger.manager.loggerDict.get(name)
            if isinstance(log, logging.Logger):
                for handler in list(log.handlers):
                    handler.close()
                    log.removeHandler(handler)
                logging.Logger.manager.loggerDict.pop(name, None)

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        import logger as logger_module
        self._logger_module = logger_module
        self._original_log_dir = logger_module.LOG_DIR
        logger_module.LOG_DIR = Path(self._tmp_dir.name) / "logs"
        # Fresh module-level state per test - _configured and the standard
        # library's own logger registry both persist across tests otherwise.
        logger_module._configured = set()
        self._close_and_remove_test_loggers()

    def tearDown(self):
        self._logger_module.LOG_DIR = self._original_log_dir
        self._close_and_remove_test_loggers()
        self._tmp_dir.cleanup()

    def test_get_logger_with_the_bare_root_name_still_gets_its_own_handlers(self):
        # The exact scenario the bug would have broken - a logger literally
        # named "axim", with no dotted sub-name.
        log = self._logger_module.get_logger("axim")
        # _attach_root()'s own file handler on the "axim" root logger,
        # PLUS get_logger()'s own file+console handlers on this same
        # logger object (since logging.getLogger("axim") IS the root here)
        # - at least 2 handlers, not just the root's 1, confirms
        # get_logger()'s own setup wasn't skipped.
        self.assertGreaterEqual(len(log.handlers), 2)

    def test_normal_dotted_name_still_works(self):
        log = self._logger_module.get_logger("axim.test_logger_collision")
        self.assertGreaterEqual(len(log.handlers), 2)  # file + console
        self.assertTrue(log.propagate)


class ResilientRotatingFileHandlerTests(unittest.TestCase):
    """2026-07-31 verified production incident: the live listener's own
    logs/lifecycle.log handler went silently dead for 4+ hours after a
    single failed Windows log rotation (another process - one of this
    codebase's own many one-off diagnostic scripts, which transitively
    import a get_logger()-owning module - had the file open at the exact
    moment of rollover; os.rename() then raised OSError). See core/
    logger.py's _ResilientRotatingFileHandler docstring for the full
    mechanism this covers: delay=True (handler below) shrinks how often a
    merely-imports-but-never-logs process opens the file at all, and this
    class's own doRollover() override stops a failed rotation from ever
    leaving self.stream permanently unusable."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        # addCleanup (LIFO) rather than tearDown - every handler this test
        # opens must be closed BEFORE the temp dir is removed, or Windows
        # refuses to delete a file still held open (registering the temp
        # dir's own cleanup first means it runs LAST).
        self.addCleanup(self._tmp_dir.cleanup)
        import logger as logger_module
        self._logger_module = logger_module
        self._log_path = Path(self._tmp_dir.name) / "resilient_test.log"

    def _make_handler(self, max_bytes=1_000_000, delay=True):
        handler = self._logger_module._ResilientRotatingFileHandler(
            self._log_path, maxBytes=max_bytes, backupCount=3, encoding="utf-8", delay=delay,
        )
        self.addCleanup(handler.close)
        return handler

    def _record(self, msg="test message"):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=msg, args=None, exc_info=None,
        )

    def test_delay_true_does_not_open_the_file_until_first_emit(self):
        handler = self._make_handler(delay=True)
        self.assertFalse(self._log_path.exists())
        handler.emit(self._record())
        self.assertTrue(self._log_path.exists())

    def test_normal_rollover_without_contention_still_works(self):
        # Small maxBytes so a handful of real writes genuinely trigger a
        # real rollover - proves the override didn't break the ordinary,
        # no-contention path.
        handler = self._make_handler(max_bytes=200, delay=False)
        for _ in range(20):
            handler.emit(self._record("x" * 50))
        backups = list(Path(self._tmp_dir.name).glob("resilient_test.log.*"))
        self.assertTrue(backups, "expected at least one real backup file from an actual rollover")

    def test_failed_rollover_reopens_the_stream_instead_of_staying_none(self):
        handler = self._make_handler(max_bytes=200, delay=False)
        handler.emit(self._record("prime the file"))  # opens self.stream for real

        with patch(
            "logging.handlers.RotatingFileHandler.doRollover",
            side_effect=OSError("[WinError 32] The process cannot access the file"),
        ):
            handler.doRollover()  # simulates the exact 2026-07-31 incident

        self.assertIsNotNone(handler.stream)
        self.assertFalse(handler.stream.closed)

    def test_failed_rollover_does_not_lose_the_triggering_record(self):
        handler = self._make_handler(max_bytes=50, delay=False)
        handler.emit(self._record("prime"))

        real_do_rollover = logging.handlers.RotatingFileHandler.doRollover
        call_count = {"n": 0}

        def flaky_do_rollover(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("[WinError 32] The process cannot access the file")
            return real_do_rollover(self)

        with patch("logging.handlers.RotatingFileHandler.doRollover", flaky_do_rollover):
            handler.emit(self._record("this record must not be lost"))

        handler.stream.flush()
        content = self._log_path.read_text(encoding="utf-8")
        self.assertIn("this record must not be lost", content)

    def test_get_logger_uses_delay_so_import_only_does_not_create_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._logger_module.LOG_DIR = Path(tmp) / "logs"
            self._logger_module._configured = set()
            names = ("axim", "axim.test_delay_no_create")
            try:
                log = self._logger_module.get_logger("axim.test_delay_no_create", console=False)
                log_file = self._logger_module.LOG_DIR / "test_delay_no_create.log"
                self.assertFalse(log_file.exists(), "constructing the logger alone must not open the file")
                log.info("now it should exist")
                self.assertTrue(log_file.exists())
            finally:
                # get_logger() also attached the root "axim" logger's own
                # separate handler (_attach_root(), also pointed inside this
                # same temp LOG_DIR) - must close that one too, not just the
                # leaf logger's, or Windows can't remove the temp dir below.
                for name in names:
                    log_obj = logging.Logger.manager.loggerDict.get(name)
                    if isinstance(log_obj, logging.Logger):
                        for handler in list(log_obj.handlers):
                            handler.close()
                            log_obj.removeHandler(handler)
                        logging.Logger.manager.loggerDict.pop(name, None)


class ProcessRoleFileSplitTests(unittest.TestCase):
    """2026-08-01 verified production incident: api/main.py and core/
    telegram_listener.py are two independent, permanent processes that
    both call get_logger("axim.lifecycle", filename="lifecycle.log") -
    each constructs its own RotatingFileHandler on the SAME physical
    file, and on Windows a rollover in either process can never complete
    while the other still has that file open. That condition is
    permanent (both are long-lived services), not transient - see
    core/logger.py's set_process_role() docstring for the full incident
    (logs/lifecycle.log froze at 19:15:30 on 2026-07-31 and was still
    frozen, byte-for-byte identical, 14+ hours later). set_process_role()
    fixes this at the source: each process's handlers get a role-specific
    filename, so two processes can never contend for the same rename."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        import logger as logger_module
        self._logger_module = logger_module
        self._original_log_dir = logger_module.LOG_DIR
        self._original_role = logger_module._process_role
        logger_module.LOG_DIR = Path(self._tmp_dir.name) / "logs"
        logger_module._configured = set()
        logger_module._process_role = None
        self.addCleanup(self._restore)

    def _restore(self):
        self._logger_module.LOG_DIR = self._original_log_dir
        self._logger_module._process_role = self._original_role
        self._logger_module._configured = set()
        for name in ("axim", "axim.test_role_a", "axim.test_role_b"):
            log_obj = logging.Logger.manager.loggerDict.get(name)
            if isinstance(log_obj, logging.Logger):
                for handler in list(log_obj.handlers):
                    handler.close()
                    log_obj.removeHandler(handler)
                logging.Logger.manager.loggerDict.pop(name, None)

    def test_role_suffixed_filename_unchanged_with_no_role_set(self):
        self.assertEqual(self._logger_module._role_suffixed("lifecycle.log"), "lifecycle.log")

    def test_role_suffixed_filename_gets_the_role_inserted_before_the_extension(self):
        self._logger_module._process_role = "api"
        self.assertEqual(self._logger_module._role_suffixed("lifecycle.log"), "lifecycle-api.log")
        self._logger_module._process_role = "listener"
        self.assertEqual(self._logger_module._role_suffixed("lifecycle.log"), "lifecycle-listener.log")

    def test_set_process_role_raises_once_a_logger_is_already_configured(self):
        self._logger_module.get_logger("axim.test_role_a", console=False)
        with self.assertRaises(RuntimeError):
            self._logger_module.set_process_role("api")

    def test_get_logger_writes_to_the_role_suffixed_physical_file(self):
        self._logger_module.set_process_role("listener")
        log = self._logger_module.get_logger(
            "axim.test_role_a", filename="lifecycle.log", console=False,
        )
        log.info("hello from listener")
        expected = self._logger_module.LOG_DIR / "lifecycle-listener.log"
        self.assertTrue(expected.exists())
        self.assertFalse((self._logger_module.LOG_DIR / "lifecycle.log").exists())

    def test_two_processes_never_touch_the_same_physical_file(self):
        """The actual regression: real two-process contention, reproduced
        with real subprocesses (not mocked) writing to a real shared LOG_DIR
        with a tiny MAX_BYTES so both are forced to rotate repeatedly and
        concurrently - the exact conditions that froze production. Before
        set_process_role(), both would target the same "lifecycle.log" and
        Windows would eventually strand one of them mid-rollover. After it,
        each process's rollover only ever contends with itself."""
        shared_log_dir = Path(self._tmp_dir.name) / "shared_logs"
        shared_log_dir.mkdir()

        worker_script = textwrap.dedent(f"""
            import sys, time
            from pathlib import Path
            sys.path.insert(0, {str(PROJECT_ROOT / "core")!r})
            import logger as logger_module
            logger_module.LOG_DIR = Path({str(shared_log_dir)!r})
            logger_module._configured = set()
            logger_module.set_process_role(sys.argv[1])
            log = logger_module.get_logger("axim.test_shared_role", filename="shared.log", console=False)
            for i in range(400):
                log.info("line %d " + ("x" * 80), i)
            for h in list(log.handlers):
                h.flush()
        """)
        script_path = Path(self._tmp_dir.name) / "role_worker.py"
        script_path.write_text(worker_script, encoding="utf-8")

        env = dict(os.environ)
        env["LOG_MAX_BYTES"] = "2000"
        env["LOG_BACKUP_COUNT"] = "3"

        procs = [
            subprocess.Popen([sys.executable, str(script_path), role], env=env)
            for role in ("api", "listener")
        ]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)

        api_file = shared_log_dir / "shared-api.log"
        listener_file = shared_log_dir / "shared-listener.log"
        self.assertTrue(api_file.exists())
        self.assertTrue(listener_file.exists())

        # Rotation must have actually happened for both (proves neither got
        # permanently stuck the way the un-split file did in production) -
        # a .1 backup only exists once a rollover has fully completed.
        self.assertTrue(list(shared_log_dir.glob("shared-api.log.*")),
                         "api process's log never rotated - looks stuck")
        self.assertTrue(list(shared_log_dir.glob("shared-listener.log.*")),
                         "listener process's log never rotated - looks stuck")


class NoDuplicateHandlerTests(unittest.TestCase):
    """Repeat get_logger() calls for the same name (every module-level
    `logger = get_logger("axim.lifecycle", ...)` call site does this
    independently within one process) must never attach a second handler -
    that would double-write every record to the same file."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        import logger as logger_module
        self._logger_module = logger_module
        self._original_log_dir = logger_module.LOG_DIR
        logger_module.LOG_DIR = Path(self._tmp_dir.name) / "logs"
        logger_module._configured = set()
        self.addCleanup(self._restore)

    def _restore(self):
        self._logger_module.LOG_DIR = self._original_log_dir
        self._logger_module._configured = set()
        for name in ("axim", "axim.test_no_dup"):
            log_obj = logging.Logger.manager.loggerDict.get(name)
            if isinstance(log_obj, logging.Logger):
                for handler in list(log_obj.handlers):
                    handler.close()
                    log_obj.removeHandler(handler)
                logging.Logger.manager.loggerDict.pop(name, None)

    def test_exactly_one_file_handler_after_repeat_get_logger_calls(self):
        for _ in range(5):
            log = self._logger_module.get_logger("axim.test_no_dup", filename="no_dup.log", console=False)
        file_handlers = [h for h in log.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(file_handlers), 1)

    def test_no_duplicate_lines_written_for_a_single_log_call(self):
        log = self._logger_module.get_logger("axim.test_no_dup", filename="no_dup.log", console=False)
        for _ in range(5):
            self._logger_module.get_logger("axim.test_no_dup", filename="no_dup.log", console=False)
        log.info("exactly once")
        for h in log.handlers:
            h.flush()
        content = (self._logger_module.LOG_DIR / "no_dup.log").read_text(encoding="utf-8")
        self.assertEqual(content.count("exactly once"), 1)


class RolloverFailureDoesNotRecurseOrDisableLoggingTests(unittest.TestCase):
    """A rollover failure must degrade gracefully (drop at most the one
    triggering record) and must never recurse or permanently disable the
    handler - the very next log call, once whatever held the file clears,
    must succeed normally."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        import logger as logger_module
        self._log_path = Path(self._tmp_dir.name) / "recovery_test.log"
        self._handler = logger_module._ResilientRotatingFileHandler(
            self._log_path, maxBytes=200, backupCount=3, encoding="utf-8", delay=False,
        )
        self.addCleanup(self._handler.close)

    def _record(self, msg):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=msg, args=None, exc_info=None,
        )

    def test_reopen_failure_inside_doRollover_propagates_once_without_recursing(self):
        # Patching os.rename (not doRollover itself) so the real stdlib
        # logic actually runs up through self.stream.close(); self.stream =
        # None before hitting the failure - matching the real 2026-07-31
        # mechanism, where the rename step specifically is what raised.
        self._handler.emit(self._record("prime"))
        with patch("logging.handlers.os.rename", side_effect=OSError("rename failed")), \
             patch.object(type(self._handler), "_open", side_effect=OSError("reopen also failed")):
            with self.assertRaises(OSError):
                self._handler.doRollover()

    def test_logging_resumes_normally_once_contention_clears(self):
        self._handler.emit(self._record("prime"))
        with patch("logging.handlers.os.rename", side_effect=OSError("transient rename failure")):
            self._handler.doRollover()
        # Contention is gone now (no more patch) - the handler must recover
        # on its own, not stay wedged from the failure above.
        self._handler.emit(self._record("back to normal"))
        self._handler.stream.flush()
        content = self._log_path.read_text(encoding="utf-8")
        self.assertIn("back to normal", content)


if __name__ == "__main__":
    unittest.main()
