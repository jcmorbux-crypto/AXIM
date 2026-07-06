"""Trading Sessions API (docs/AXIM_SESSION_ARCHITECTURE.md) - profiles
(saved start-config templates) and the actual session lifecycle (start/
stop/emergency-stop/status). Session-scoped stop-condition enforcement
itself lives in core/session_manager.py + core/trade_coordinator.py; this
router is just the HTTP surface over core/database.py's session tables.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from settings import ACCOUNT
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class ProfileCreate(BaseModel):
    name: str
    channel_ids: List[int]
    profit_target: float = 0
    loss_limit: float = 0
    max_trades: int = 0
    require_confirmation: bool = False


class SessionStart(BaseModel):
    name: Optional[str] = None
    channel_ids: List[int]
    profit_target: float = 0
    loss_limit: float = 0
    max_trades: int = 0
    require_confirmation: bool = False
    profile_id: Optional[int] = None


def _with_progress(session):
    """Adds the derived fields the Trading Sessions UI needs (section 7:
    "Active session progress ... Remaining target") without storing them -
    always computed fresh from profit_target/loss_limit/realized_pnl."""
    if session is None:
        return None
    remaining_to_target = (
        max(session["profit_target"] - session["realized_pnl"], 0) if session["profit_target"] > 0 else None
    )
    remaining_to_loss_limit = (
        max(session["loss_limit"] + session["realized_pnl"], 0) if session["loss_limit"] > 0 else None
    )
    return {
        **session,
        "remaining_to_target": remaining_to_target,
        "remaining_to_loss_limit": remaining_to_loss_limit,
    }


@router.get("/profiles")
def list_profiles(user=Depends(get_current_user)):
    return database.list_session_profiles()


@router.post("/profiles")
def create_profile(body: ProfileCreate, user=Depends(require_admin)):
    if not body.channel_ids:
        raise HTTPException(status_code=400, detail="a profile must include at least one channel")
    profile_id = database.create_session_profile(
        body.name, body.channel_ids, body.profit_target, body.loss_limit, body.max_trades, body.require_confirmation,
    )
    return {"id": profile_id}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, user=Depends(require_admin)):
    database.delete_session_profile(profile_id)
    return {"status": "deleted"}


@router.get("/active")
def active_session(user=Depends(get_current_user)):
    return _with_progress(database.get_active_trading_session())


@router.get("")
def list_sessions(limit: int = 50, user=Depends(get_current_user)):
    return [_with_progress(s) for s in database.list_trading_sessions(limit)]


@router.get("/{session_id}")
def get_session(session_id: int, user=Depends(get_current_user)):
    session = database.get_trading_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _with_progress(session)


@router.post("/start")
def start_session(body: SessionStart, user=Depends(require_admin)):
    """account_mode is never taken from the request body - it always
    reflects the real, currently-connected ACCOUNT (config/settings.py),
    same deliberate choice as GET /api/pocket-option/status: a session
    can't claim to be running LIVE while the actual browser is connected
    to DEMO, or vice versa."""
    if not body.channel_ids:
        raise HTTPException(status_code=400, detail="a session must include at least one channel")
    try:
        session_id = database.start_trading_session(
            name=body.name, channel_ids=body.channel_ids, account_mode=ACCOUNT,
            profit_target=body.profit_target, loss_limit=body.loss_limit, max_trades=body.max_trades,
            require_confirmation=body.require_confirmation, profile_id=body.profile_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _with_progress(database.get_trading_session(session_id))


@router.post("/{session_id}/stop")
def stop_session(session_id: int, user=Depends(require_admin)):
    session = database.get_trading_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail=f"session is already {session['status']}")
    database.stop_trading_session(session_id, "stopped_manual", f"stopped by {user['email']}")
    return _with_progress(database.get_trading_session(session_id))


@router.post("/{session_id}/emergency-stop")
def emergency_stop_session(session_id: int, user=Depends(get_current_user)):
    """Deliberately available to ANY logged-in user, not just Owner/Admin -
    same safety exception as the existing global
    POST /api/control/emergency-stop: a non-admin who spots a problem must
    still be able to halt trading immediately. Flips the SAME global
    ui_control_state emergency_stop (stops requesting/executing signals
    everywhere, not just this session) and marks this specific session
    stopped, matching the product spec's Emergency Stop requirements
    exactly."""
    session = database.get_trading_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    database.set_control_state(paused=True, emergency_stop=True)
    if session["status"] == "active":
        database.stop_trading_session(session_id, "stopped_emergency", f"emergency stop by {user['email']}")
    return _with_progress(database.get_trading_session(session_id))
