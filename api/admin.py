"""Owner/Admin user-management endpoints - everything here requires
api/auth.py's require_admin dependency. All mutating actions are logged
via database.record_admin_action so they show up on the Logs page.

Telegram/Pocket Option status and trade counts are reported per the
ONE shared trading connection AXIM currently has (core/telegram_listener.py
owns a single Telegram session and a single Pocket Option browser) - there
is no per-user broker isolation yet (that's a future SaaS-multi-tenant
step, see docs/AXIM_SESSION_ARCHITECTURE.md). Rather than fabricate
per-user numbers that don't exist, this honestly labels them as shared.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(API_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
import process_control
from auth_routes import require_admin, public_user

VALID_ROLES = {"owner", "admin", "user", "free_user", "trial_user", "disabled_user"}
VALID_TIERS = {"owner", "internal", "free_beta", "trial", "basic", "pro", "elite", "suspended"}
VALID_STATES = {"active", "free_access", "trial", "pending_approval", "expired", "suspended", "disabled"}

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "user"
    access_tier: str = "trial"
    access_state: str = "pending_approval"


class EditUserRequest(BaseModel):
    role: Optional[str] = None
    access_tier: Optional[str] = None
    access_state: Optional[str] = None
    trial_expires_at: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class SetTrialRequest(BaseModel):
    trial_expires_at: str


class SetTierRequest(BaseModel):
    access_tier: str


def _shared_connection_status():
    heartbeat = database.get_listener_heartbeat()
    process_status = process_control.get_status()
    return {
        "process_running": process_status["running"],
        "heartbeat": heartbeat,
        "note": "one shared Telegram/Pocket Option connection today - not yet isolated per user",
    }


def _log(admin_user, target_user_id, action, detail=None):
    database.record_admin_action(admin_user["id"], target_user_id, action, detail)


@router.get("/users")
def list_users(admin_user=Depends(require_admin)):
    return [public_user(u) for u in database.list_users()]


@router.post("/users")
def create_user(body: CreateUserRequest, admin_user=Depends(require_admin)):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role, must be one of {sorted(VALID_ROLES)}")
    if body.access_tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"invalid access_tier, must be one of {sorted(VALID_TIERS)}")
    if body.access_state not in VALID_STATES:
        raise HTTPException(status_code=400, detail=f"invalid access_state, must be one of {sorted(VALID_STATES)}")
    if database.get_user_by_email(body.email) is not None:
        raise HTTPException(status_code=409, detail="a user with this email already exists")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    user_id = database.create_user(body.email, body.password, body.role, body.access_tier, body.access_state)
    _log(admin_user, user_id, "create_user", f"role={body.role} tier={body.access_tier} state={body.access_state}")
    return public_user(database.get_user_by_id(user_id))


@router.get("/users/{user_id}")
def get_user_detail(user_id: int, admin_user=Depends(require_admin)):
    user = database.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "user": public_user(user),
        "sessions": database.list_user_sessions(user_id),
        "shared_connection": _shared_connection_status(),
        "trade_count": None,
        "pl_summary": None,
    }


@router.patch("/users/{user_id}")
def edit_user(user_id: int, body: EditUserRequest, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    updates = {}
    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"invalid role, must be one of {sorted(VALID_ROLES)}")
        updates["role"] = body.role
    if body.access_tier is not None:
        if body.access_tier not in VALID_TIERS:
            raise HTTPException(status_code=400, detail=f"invalid access_tier, must be one of {sorted(VALID_TIERS)}")
        updates["access_tier"] = body.access_tier
    if body.access_state is not None:
        if body.access_state not in VALID_STATES:
            raise HTTPException(status_code=400, detail=f"invalid access_state, must be one of {sorted(VALID_STATES)}")
        updates["access_state"] = body.access_state
    if body.trial_expires_at is not None:
        updates["trial_expires_at"] = body.trial_expires_at

    database.update_user(user_id, **updates)
    _log(admin_user, user_id, "edit_user", str(updates))
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordRequest, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    database.set_user_password(user_id, body.new_password)
    database.revoke_all_sessions(user_id)
    _log(admin_user, user_id, "reset_password")
    return {"status": "password reset, all sessions revoked"}


@router.post("/users/{user_id}/activate")
def activate_user(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, access_state="active")
    _log(admin_user, user_id, "activate")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, access_state="disabled")
    database.revoke_all_sessions(user_id)
    _log(admin_user, user_id, "disable")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/grant-free-access")
def grant_free_access(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, access_tier="free_beta", access_state="free_access")
    _log(admin_user, user_id, "grant_free_access")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/set-trial")
def set_trial(user_id: int, body: SetTrialRequest, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, access_tier="trial", access_state="trial", trial_expires_at=body.trial_expires_at)
    _log(admin_user, user_id, "set_trial", f"expires_at={body.trial_expires_at}")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/set-tier")
def set_tier(user_id: int, body: SetTierRequest, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    if body.access_tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"invalid access_tier, must be one of {sorted(VALID_TIERS)}")
    database.update_user(user_id, access_tier=body.access_tier)
    _log(admin_user, user_id, "set_tier", f"tier={body.access_tier}")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/force-demo")
def force_demo(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, demo_only_forced=True)
    _log(admin_user, user_id, "force_demo")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/allow-live")
def allow_live(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.update_user(user_id, demo_only_forced=False, live_trading_allowed=True)
    _log(admin_user, user_id, "allow_live")
    return public_user(database.get_user_by_id(user_id))


@router.post("/users/{user_id}/revoke-access")
def revoke_access(user_id: int, admin_user=Depends(require_admin)):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    database.revoke_all_sessions(user_id)
    _log(admin_user, user_id, "revoke_access")
    return {"status": "all sessions revoked"}


@router.get("/actions")
def list_actions(limit: int = 200, admin_user=Depends(require_admin)):
    return database.list_admin_actions(limit)
