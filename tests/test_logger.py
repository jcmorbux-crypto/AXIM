import logging
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
