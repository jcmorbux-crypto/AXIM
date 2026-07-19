"""Live Signal Pipeline (2026-07-19 v2 mandate) - the canonical per-
signal lifecycle vocabulary the mandate specifies verbatim. Distinct
from trade_lifecycle.TradeStatus (9 values, `signals` table's own
execution_status column, unchanged) - this is the broader journey a
signal can take BEFORE it ever becomes a trade at all, including
everything that currently has zero persisted trace (see core/database.
py's signal_pipeline_events table docstring). Nothing in this module
enforces or changes control flow anywhere - it is pure observability
vocabulary, written to by core/telegram_listener.py and core/
trade_coordinator.py alongside their real decisions, never instead of
them.
"""
from enum import Enum


class SignalLifecycleState(str, Enum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    NORMALIZED = "NORMALIZED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    SIZED = "SIZED"
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    BROKER_ACCEPTED = "BROKER_ACCEPTED"
    BROKER_REJECTED = "BROKER_REJECTED"
    OPEN = "OPEN"
    WON = "WON"
    LOST = "LOST"
    DRAW = "DRAW"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
