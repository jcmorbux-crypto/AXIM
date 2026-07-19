"""Bot Control Center API - HTTP surface over core/telegram_bot_trigger.py's
interactive request/reply loop. That loop runs entirely inside the
listener process (a separate OS process from this one) and previously
had zero externally-visible state - this module reads the real,
persisted activity log (core/database.py's bot_command_activity table)
plus each channel's currently-covering active session, rather than
reaching into the listener process's in-memory _active_loops dict
(which this process cannot see at all).
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from fastapi import APIRouter, Depends, HTTPException

import database
from auth_routes import get_current_user

router = APIRouter(prefix="/api/bots", tags=["bots"])


def _active_session_for_channel(channel_id):
    for session in database.list_active_trading_sessions():
        if channel_id in (session.get("channel_ids") or []):
            return session
    return None


def _session_summary(session):
    fund = database.get_fund(session["fund_id"]) if session.get("fund_id") else None
    broker_account = database.get_broker_account(session["broker_account_id"]) if session.get("broker_account_id") else None
    risk_profile = database.get_risk_profile(session["risk_profile_id"]) if session.get("risk_profile_id") else None
    activity = database.list_bot_command_activity(session["id"], limit=1)
    return {
        "id": session["id"],
        "status": session["status"],
        "account_mode": session["account_mode"],
        "fund_id": session.get("fund_id"),
        "fund_name": fund["name"] if fund else None,
        "broker_account_id": session.get("broker_account_id"),
        "broker_account_name": broker_account["name"] if broker_account else None,
        "risk_profile_id": session.get("risk_profile_id"),
        "risk_profile_name": risk_profile["name"] if risk_profile else None,
        "profit_target": session["profit_target"],
        "loss_limit": session["loss_limit"],
        "max_trades": session["max_trades"],
        "trades_count": session["trades_count"],
        "realized_pnl": session["realized_pnl"],
        "requests_sent": database.count_bot_command_activity(session["id"]),
        "last_activity": activity[0] if activity else None,
    }


@router.get("")
def list_bots(user=Depends(get_current_user)):
    """Every Bot Command Channel (whether or not a session currently
    covers it) plus its live status, if any - so a configured-but-idle
    bot source is still visible, not just active ones."""
    channels = [c for c in database.list_channels() if c.get("source_type") == "bot_command"]
    result = []
    for channel in channels:
        session = _active_session_for_channel(channel["id"])
        result.append({
            "channel_id": channel["id"],
            "title": channel["title"],
            "trigger_command": channel.get("trigger_command"),
            "command_wait_for_result": bool(channel.get("command_wait_for_result")),
            "max_requests_per_session": channel.get("max_requests_per_session"),
            "active_session": _session_summary(session) if session else None,
        })
    return result


@router.get("/{channel_id}/activity")
def get_bot_activity(channel_id: int, session_id: int = None, limit: int = 50, user=Depends(get_current_user)):
    """Full activity log for one channel's session - session_id lets the
    UI look at a just-ended session's history too, not only the
    currently active one."""
    channel = database.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    if session_id is None:
        session = _active_session_for_channel(channel_id)
        if session is None:
            return []
        session_id = session["id"]
    return database.list_bot_command_activity(session_id, limit=limit)
