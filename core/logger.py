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
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))
BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))

_FORMATTER = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
_configured = set()


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


def _attach_root():
    if "axim" in _configured:
        return
    _configured.add("axim")
    root = logging.getLogger("axim")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "axim.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
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
        log_file = filename or f"{name.rsplit('.', 1)[-1]}.log"

        file_handler = RotatingFileHandler(
            LOG_DIR / log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setFormatter(_FORMATTER)
        logger.addHandler(file_handler)

        if console:
            logger.addHandler(_SafeStreamHandler())

        logger.setLevel(_resolve_level())
        logger.propagate = True  # bubbles up to "axim" root -> logs/axim.log

    return logger
