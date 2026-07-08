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
import session_manager
from settings import ACCOUNT, TRADE_CONFIRMATION_TIMEOUT_SECONDS
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
    risk_profile_id: Optional[int] = None
    fund_id: int


class AttachRiskProfile(BaseModel):
    risk_profile_id: Optional[int] = None


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


# ---------------------------------------------------------------------
# Live-mode trade confirmation gate (docs/AXIM_APP_PLAN.md) - registered
# BEFORE the /{session_id} catch-all below on purpose: FastAPI/Starlette
# matches routes in registration order, so "/pending-confirmations"
# would otherwise be swallowed by /{session_id} (which then 422s trying
# to parse "pending-confirmations" as an int) - a real bug caught during
# live verification, not a hypothetical one. The listener process
# (core/session_manager.wait_for_trade_confirmation) blocks and polls
# core/database.py's pending_trade_confirmations table; this is just the
# HTTP surface a browser polls/writes to. Deliberately available to ANY
# logged-in user, same safety exception as Emergency Stop below -
# whoever is watching when a Live trade needs a decision should be able
# to make it, not just an Owner/Admin.
# ---------------------------------------------------------------------

@router.get("/pending-confirmations")
def list_pending_confirmations(user=Depends(get_current_user)):
    """timeout_seconds is included on every row (rather than the client
    hardcoding it) so the countdown shown in the UI can never drift from
    the actual TRADE_CONFIRMATION_TIMEOUT_SECONDS the listener process is
    really enforcing, even if that's been customized via .env."""
    rows = database.list_pending_trade_confirmations()
    for row in rows:
        row["timeout_seconds"] = TRADE_CONFIRMATION_TIMEOUT_SECONDS
    return rows


@router.post("/pending-confirmations/{trade_id}/confirm")
def confirm_trade(trade_id: int, user=Depends(get_current_user)):
    updated = database.decide_trade_confirmation(trade_id, "confirmed", decided_by=user["email"])
    if not updated:
        raise HTTPException(status_code=409, detail="already decided or no longer pending")
    return database.get_pending_trade_confirmation(trade_id)


@router.post("/pending-confirmations/{trade_id}/reject")
def reject_trade(trade_id: int, user=Depends(get_current_user)):
    updated = database.decide_trade_confirmation(trade_id, "rejected", decided_by=user["email"])
    if not updated:
        raise HTTPException(status_code=409, detail="already decided or no longer pending")
    return database.get_pending_trade_confirmation(trade_id)


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
    to DEMO, or vice versa.

    fund_id is required - every session must be attributed to a Fund
    (docs/AXIM_APP_PLAN.md) so P&L/vault history stays organized per
    portfolio rather than one undifferentiated pile. If the caller
    doesn't explicitly pick a risk profile, the fund's own
    default_risk_profile_id is used - the fund's "assigned money
    management profile" setting actually does something, not just a
    label."""
    if not body.channel_ids:
        raise HTTPException(status_code=400, detail="a session must include at least one channel")
    fund = database.get_fund(body.fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="fund not found")
    risk_profile_id = body.risk_profile_id if body.risk_profile_id is not None else fund["default_risk_profile_id"]
    try:
        session_id = database.start_trading_session(
            name=body.name, channel_ids=body.channel_ids, account_mode=ACCOUNT,
            profit_target=body.profit_target, loss_limit=body.loss_limit, max_trades=body.max_trades,
            require_confirmation=body.require_confirmation, profile_id=body.profile_id,
            risk_profile_id=risk_profile_id, fund_id=body.fund_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _with_progress(database.get_trading_session(session_id))


@router.patch("/{session_id}/risk-profile")
def attach_risk_profile(session_id: int, body: AttachRiskProfile, user=Depends(require_admin)):
    session = database.get_trading_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if body.risk_profile_id is not None and database.get_risk_profile(body.risk_profile_id) is None:
        raise HTTPException(status_code=404, detail="risk profile not found")
    database.set_session_risk_profile(session_id, body.risk_profile_id)
    return _with_progress(database.get_trading_session(session_id))


@router.post("/{session_id}/stop")
def stop_session(session_id: int, user=Depends(require_admin)):
    session = database.get_trading_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail=f"session is already {session['status']}")
    session_manager.end_session(session_id, "stopped_manual", f"stopped by {user['email']}")
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
        session_manager.end_session(session_id, "stopped_emergency", f"emergency stop by {user['email']}")
    return _with_progress(database.get_trading_session(session_id))
