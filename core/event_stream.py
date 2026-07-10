"""Bridges core/event_bus.py's in-process pub/sub (lives inside the
Telegram listener process) to core/database.py's server_events outbox
table, which the API process's SSE endpoint (GET /api/events/stream) can
see. The two processes share no memory - only the SQLite file - so an
event published inside the listener is otherwise invisible to anything
outside that one process. This module is a thin, dumb bridge only: no
business logic, just "the event fired, write it down" - matching the
same discipline every other cross-process signal in this codebase
follows (core/session_manager.py, core/rule_engine.py, etc. all mutate
real state directly; this module never does).
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

# The known, real events core/trade_coordinator.py and
# execution/pocket_executor.py already publish - see their own
# event_bus.publish() call sites. notification.created is written
# directly by database.create_notification() itself (not via the bus),
# since notifications can originate from either process, not just the
# listener's signal pipeline.
_BRIDGED_EVENTS = (
    "trade.signal_received",
    "signal.ignored",
    "trade.prepared",
    "trade.error",
    "trade.closed",
)


def register(event_bus):
    """Call once at listener startup, alongside the existing
    session_manager.register(get_event_bus()) call (core/
    telegram_listener.py)."""
    for event_name in _BRIDGED_EVENTS:
        event_bus.subscribe(event_name, _make_writer(event_name))


def _make_writer(event_name):
    def _write(payload):
        try:
            database.record_server_event(event_name, payload)
        except Exception:
            logger.exception("event_stream: failed to record server_event for %s", event_name)
    return _write
