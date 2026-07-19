"""Live Signal Pipeline API (2026-07-19 v2 mandate) - HTTP surface over
core/database.py's signal_pipeline_events CRUD. Read-only: this
subsystem is pure observability, written to by core/telegram_listener.py
and core/trade_coordinator.py/execution/pocket_executor.py alongside
their real decisions - nothing here ever writes to a signal or triggers
execution.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import database
from signal_lifecycle import SignalLifecycleState
from auth_routes import get_current_user

router = APIRouter(prefix="/api/signal-pipeline", tags=["signal-pipeline"])


@router.get("/journeys")
def list_journeys(state: Optional[str] = None, limit: int = 50, user=Depends(get_current_user)):
    """One row per distinct (channel_id, message_id) journey, most
    recent first - the main Live Signal Pipeline list. state (optional)
    filters to journeys whose most recent event is that state, e.g.
    state=SKIPPED to see everything that silently vanished before this
    subsystem existed."""
    if state is not None and state not in set(SignalLifecycleState):
        raise HTTPException(status_code=400, detail=f"invalid state: {state!r}")
    return database.list_recent_pipeline_journeys(limit=limit, state=state)


@router.get("/journeys/{channel_id}/{message_id}")
def get_journey(channel_id: int, message_id: int, user=Depends(get_current_user)):
    """Every event recorded for one specific message, in order - the
    full journey, whether or not it ever became a real trade."""
    events = database.list_pipeline_events_for_message(channel_id, message_id)
    if not events:
        raise HTTPException(status_code=404, detail="no pipeline events found for this channel/message")
    return events


@router.get("/signals/{signal_id}")
def get_signal_journey(signal_id: int, user=Depends(get_current_user)):
    """Every event linked to a real `signals` row - the natural
    entry point from an existing Trade History / Signal Inspector view
    that already has a signal_id, without needing to know its original
    channel_id/message_id."""
    events = database.list_pipeline_events_for_signal(signal_id)
    if not events:
        raise HTTPException(status_code=404, detail="no pipeline events linked to this signal")
    return events
