"""Authentication routes and RBAC dependencies for the AXIM control API.

Two parallel auth transports share one underlying session model
(core/database.py's auth_sessions table / core/auth.py's token hashing):
cookie-based sessions (httpOnly, SameSite=Lax, Secure whenever the actual
request came in over HTTPS - see _request_is_https() and
docs/AXIM_REMOTE_ACCESS.md) for the local web UI, and an
`Authorization: Bearer <token>` header for Remote Clients (a laptop over
Tailscale, and eventually native mobile/tablet apps) that don't want
cookie-jar/CORS semantics. Both resolve through the exact same
get_current_user() - a request carrying either is authenticated
identically; the header is checked first, cookie is the fallback, so an
unrelated/expired cookie never shadows a valid token.
"""
import sys
import threading
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

import database
import email_sender
from settings import APP_BASE_URL

SESSION_COOKIE = "axim_session"
REMEMBER_ME_HOURS = 720  # 30 days
DEFAULT_SESSION_HOURS = 12
_VALID_CLIENT_TYPES = {"web", "desktop", "mobile", "tablet", "api"}

# access_states that must never be allowed to authenticate at all, even
# with the correct password - "pending_approval" is deliberately NOT in
# this set, since a brand-new signup can log in and see a waiting screen
# rather than a bare rejection.
_BLOCKED_ACCESS_STATES = {"disabled", "suspended", "expired"}

router = APIRouter(prefix="/api/auth", tags=["auth"])

# bootstrap_owner()'s count_users()==0 check and the create_user() that
# follows it are two separate DB connections/transactions, not one
# atomic operation - two concurrent first-run requests (e.g. two devices
# on the Tailscale network racing to set up AXIM at the same moment)
# could both pass the check before either inserts, minting two owners
# instead of one. AXIM's API always runs as a single uvicorn process (no
# --workers), so a plain in-process lock around the check-then-create
# sequence closes this completely.
_bootstrap_lock = threading.Lock()


def _request_is_https(request: Request) -> bool:
    """Whether to set the Secure cookie flag - tied to the actual request
    scheme, not the server's bind host, so a Tailscale/reverse-proxy setup
    that terminates TLS in front of a plain-HTTP local process (e.g.
    `tailscale serve`) still gets Secure cookies, and a misconfigured bind
    host can't silently ship a cookie over plaintext. X-Forwarded-Proto is
    trusted here because it's only ever meaningful behind a proxy the
    operator themselves put in front of AXIM (Tailscale serve, or a future
    hosted reverse proxy) - there is no untrusted intermediary in this
    deployment model that could spoof it to our advantage."""
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return forwarded_proto.split(",")[0].strip().lower() == "https"


class BootstrapOwnerRequest(BaseModel):
    email: str
    password: str
    client_name: Optional[str] = None
    client_type: str = "web"


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False
    client_name: Optional[str] = None
    client_type: str = "web"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


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


def _extract_bearer_token(authorization):
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[len("bearer "):].strip() or None


def get_current_user(authorization: Optional[str] = Header(default=None),
                      axim_session: Optional[str] = Cookie(default=None)):
    """Header checked before cookie - a Remote Client sending a valid
    Bearer token must never be shadowed by a stale/unrelated cookie the
    same request happens to also carry."""
    raw_token = _extract_bearer_token(authorization) or axim_session
    if not raw_token:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = database.get_session_user(raw_token)
    if user is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    user = database.check_and_expire_trial(user)
    if user["access_state"] in _BLOCKED_ACCESS_STATES:
        raise HTTPException(status_code=403, detail=f"account is {user['access_state']}")
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
def bootstrap_owner(body: BootstrapOwnerRequest, response: Response, request: Request):
    """Creates the very first account as Owner with full access - only
    callable while the users table is empty, so this can't be used to
    mint a second owner later. This is the ONE place a real account gets
    created without an existing Owner/Admin approving it - by definition,
    since no admin exists yet."""
    if not body.email.strip() or "@" not in body.email:
        raise HTTPException(status_code=400, detail="a valid email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    if body.client_type not in _VALID_CLIENT_TYPES:
        raise HTTPException(status_code=400, detail=f"invalid client_type: {body.client_type!r}")

    with _bootstrap_lock:
        if database.count_users() > 0:
            raise HTTPException(status_code=409, detail="an account already exists - use /login")
        user_id = database.create_user(
            body.email, body.password,
            role="owner", access_tier="owner", access_state="active",
        )
    database.update_user(user_id, live_trading_allowed=True)
    database.record_login(user_id)
    raw_token = database.create_session(user_id, expires_hours=REMEMBER_ME_HOURS,
                                         client_name=body.client_name, client_type=body.client_type)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, samesite="lax",
                         secure=_request_is_https(request), max_age=REMEMBER_ME_HOURS * 3600)
    # token is always returned alongside the cookie - a browser client
    # simply ignores it (it already has the cookie), a token-mode Remote
    # Client stores it and sends it as Authorization: Bearer going
    # forward. One response shape for both transports, no client_type
    # branching in the response itself.
    return {**public_user(database.get_user_by_id(user_id)), "token": raw_token}


@router.post("/login")
def login(body: LoginRequest, response: Response, request: Request):
    if body.client_type not in _VALID_CLIENT_TYPES:
        raise HTTPException(status_code=400, detail=f"invalid client_type: {body.client_type!r}")

    # Brute-force lockout (core/database.py: MAX_FAILED_LOGIN_ATTEMPTS,
    # LOCKOUT_MINUTES) - checked before verify_user_credentials so a
    # locked account gets a distinct, actionable message instead of the
    # generic "incorrect email or password" a real credential mismatch
    # gets. AXIM has no public internet exposure by default, but the
    # login endpoint is still reachable by any device on the Tailscale
    # network - this is real protection, not theater.
    locked_until = database.is_account_locked(body.email)
    if locked_until:
        raise HTTPException(
            status_code=429,
            detail=f"too many failed login attempts - locked until {locked_until}",
        )

    user = database.verify_user_credentials(body.email, body.password)
    if user is None:
        database.record_failed_login(body.email)
        raise HTTPException(status_code=401, detail="incorrect email or password")
    user = database.check_and_expire_trial(user)
    if user["access_state"] in _BLOCKED_ACCESS_STATES:
        raise HTTPException(status_code=403, detail=f"account is {user['access_state']}")

    database.reset_failed_login(user["id"])
    database.record_login(user["id"])
    hours = REMEMBER_ME_HOURS if body.remember_me else DEFAULT_SESSION_HOURS
    raw_token = database.create_session(user["id"], expires_hours=hours,
                                         client_name=body.client_name, client_type=body.client_type)
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, samesite="lax",
                         secure=_request_is_https(request), max_age=hours * 3600)
    return {**public_user(database.get_user_by_id(user["id"])), "token": raw_token}


@router.post("/logout")
def logout(response: Response, authorization: Optional[str] = Header(default=None),
           axim_session: Optional[str] = Cookie(default=None)):
    raw_token = _extract_bearer_token(authorization) or axim_session
    if raw_token:
        database.delete_session(raw_token)
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged_out"}


@router.get("/me")
def me(user=Depends(get_current_user)):
    return public_user(user)


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user=Depends(get_current_user),
                     authorization: Optional[str] = Header(default=None),
                     axim_session: Optional[str] = Cookie(default=None)):
    """Self-service password change - distinct from
    api/admin.py's reset_password (an Owner/Admin resetting SOMEONE
    ELSE'S password without knowing the old one). This always requires
    the current password first, even for an Owner changing their own.

    Same credential-compromise-recovery reasoning as the forgot-password
    reset flow (which already revokes every session): a stolen session
    must not survive its own account's password change. Scoped to the
    session actively making the change (revoke_other_sessions) rather
    than revoke_all_sessions, so the user isn't logged out of the device
    they're using right now.

    Same brute-force lockout as login() - this endpoint checks a password
    too, and only requires an already-valid session to reach, not the
    password itself. Without this, a hijacked/stolen session (or a device
    left logged in) could brute-force the real account password with
    unlimited attempts, completely bypassing the lockout that exists
    specifically to prevent that on /login."""
    locked_until = database.is_account_locked(user["email"])
    if locked_until:
        raise HTTPException(
            status_code=429,
            detail=f"too many failed attempts - locked until {locked_until}",
        )
    if database.verify_user_credentials(user["email"], body.current_password) is None:
        database.record_failed_login(user["email"])
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="new password must be at least 8 characters")
    database.set_user_password(user["id"], body.new_password)
    raw_token = _extract_bearer_token(authorization) or axim_session
    if raw_token:
        database.revoke_other_sessions(user["id"], raw_token)
    return {"status": "password changed"}


@router.get("/sessions")
def list_my_sessions(user=Depends(get_current_user)):
    """The 'Connected Devices' panel's data source - every session
    belonging to the caller's own account (this browser, a laptop's
    Remote Client, etc.), never another user's."""
    return database.list_user_sessions(user["id"])


@router.delete("/sessions/{session_id}")
def revoke_my_session(session_id: int, user=Depends(get_current_user)):
    """Log out one specific device of the caller's own - scoped to
    user["id"] so a user can never revoke someone else's session by
    guessing an id (see database.revoke_session)."""
    database.revoke_session(session_id, user_id=user["id"])
    return {"status": "revoked"}


_GENERIC_FORGOT_PASSWORD_MESSAGE = "If an account exists for that email, a reset link has been sent."


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest):
    """Always returns the same generic message regardless of whether the
    email has an account - a distinct error/success here would let
    someone enumerate registered emails. The real per-case difference
    (no account / SMTP not configured / real send failure) is only ever
    visible server-side, in the log line email_sender.py already writes."""
    user = database.get_user_by_email(body.email)
    if user is not None and user["access_state"] not in _BLOCKED_ACCESS_STATES \
            and not database.password_reset_recently_requested(user["id"]):
        raw_token, _ = database.create_password_reset_token(user["id"])
        reset_url = f"{APP_BASE_URL}/reset-password?token={raw_token}"
        email_sender.send_password_reset_email(user["email"], reset_url)
    return {"status": "ok", "message": _GENERIC_FORGOT_PASSWORD_MESSAGE}


@router.get("/reset-password/validate")
def validate_reset_token(token: str):
    """Read-only check so the reset-password page can tell the user
    upfront that a link is already used/expired, rather than only
    discovering it after they've filled out and submitted the form."""
    user = database.get_valid_password_reset_token(token)
    return {"valid": user is not None}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest):
    user = database.get_valid_password_reset_token(body.token)
    if user is None:
        raise HTTPException(status_code=400, detail="this reset link is invalid or has expired - request a new one")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    database.set_user_password(user["id"], body.new_password)
    database.consume_password_reset_token(body.token)
    # A reset is a credential-compromise-recovery action - every existing
    # session (possibly including whatever session an attacker who
    # triggered this had) must be invalidated, not just the token used.
    database.revoke_all_sessions(user["id"])
    return {"status": "password reset"}
