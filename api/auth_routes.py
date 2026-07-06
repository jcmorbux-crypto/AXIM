"""Authentication routes and RBAC dependencies for the AXIM control API.

Cookie-based sessions (httpOnly, SameSite=Lax) - this is a local-first
app served over http://127.0.0.1 today, so no Secure flag; revisit if/when
this is ever served over anything but localhost (docs/AXIM_APP_PLAN.md).
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

import database

SESSION_COOKIE = "axim_session"
REMEMBER_ME_HOURS = 720  # 30 days
DEFAULT_SESSION_HOURS = 12

# access_states that must never be allowed to authenticate at all, even
# with the correct password - "pending_approval" is deliberately NOT in
# this set, since a brand-new signup can log in and see a waiting screen
# rather than a bare rejection.
_BLOCKED_ACCESS_STATES = {"disabled", "suspended", "expired"}

router = APIRouter(prefix="/api/auth", tags=["auth"])


class BootstrapOwnerRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False


def public_user(user):
    """Whitelisted fields safe to return over HTTP - never password_hash."""
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "access_tier": user["access_tier"],
        "access_state": user["access_state"],
        "trial_expires_at": user["trial_expires_at"],
        "demo_only_forced": bool(user["demo_only_forced"]),
        "live_trading_allowed": bool(user["live_trading_allowed"]),
        "last_login_at": user["last_login_at"],
    }


def get_current_user(axim_session: Optional[str] = Cookie(default=None)):
    if not axim_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = database.get_session_user(axim_session)
    if user is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return user


def require_admin(user=Depends(get_current_user)):
    if user["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="admin access required")
    return user


def require_owner(user=Depends(get_current_user)):
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="owner access required")
    return user


@router.get("/bootstrap-status")
def bootstrap_status():
    return {"needs_bootstrap": database.count_users() == 0}


@router.post("/bootstrap-owner")
def bootstrap_owner(body: BootstrapOwnerRequest, response: Response):
    """Creates the very first account as Owner with full access - only
    callable while the users table is empty, so this can't be used to
    mint a second owner later. This is the ONE place a real account gets
    created without an existing Owner/Admin approving it - by definition,
    since no admin exists yet."""
    if database.count_users() > 0:
        raise HTTPException(status_code=409, detail="an account already exists - use /login")
    if not body.email.strip() or "@" not in body.email:
        raise HTTPException(status_code=400, detail="a valid email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    user_id = database.create_user(
        body.email, body.password,
        role="owner", access_tier="owner", access_state="active",
    )
    database.update_user(user_id, live_trading_allowed=True)
    database.record_login(user_id)
    raw_token = database.create_session(user_id, expires_hours=REMEMBER_ME_HOURS)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, samesite="lax",
                         max_age=REMEMBER_ME_HOURS * 3600)
    return public_user(database.get_user_by_id(user_id))


@router.post("/login")
def login(body: LoginRequest, response: Response):
    user = database.verify_user_credentials(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="incorrect email or password")
    if user["access_state"] in _BLOCKED_ACCESS_STATES:
        raise HTTPException(status_code=403, detail=f"account is {user['access_state']}")

    database.record_login(user["id"])
    hours = REMEMBER_ME_HOURS if body.remember_me else DEFAULT_SESSION_HOURS
    raw_token = database.create_session(user["id"], expires_hours=hours)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, samesite="lax",
                         max_age=hours * 3600)
    return public_user(database.get_user_by_id(user["id"]))


@router.post("/logout")
def logout(response: Response, axim_session: Optional[str] = Cookie(default=None)):
    if axim_session:
        database.delete_session(axim_session)
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged_out"}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return public_user(user)
