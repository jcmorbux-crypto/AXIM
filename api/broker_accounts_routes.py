"""Broker Accounts API (docs/AXIM_APP_PLAN.md) - HTTP surface over
core/database.py's broker_accounts/fund_broker_accounts CRUD. The actual
login flow is a standalone subprocess (scripts/connect_broker_account.py)
spawned fire-and-forget from POST /connect, not run inside this process -
a manual Pocket Option login can take an arbitrary amount of real time,
which doesn't fit inside one HTTP request/response cycle. This process
never touches the browser directly, matching the rest of AXIM's
architecture (the live trading engine is a separate process this API
only ever talks to through the database).
"""
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
import fund_manager
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/broker-accounts", tags=["broker-accounts"])

CONNECT_SCRIPT = PROJECT_ROOT / "scripts" / "connect_broker_account.py"


class BrokerAccountCreate(BaseModel):
    name: str
    mode: str = "demo"


class BrokerAccountUpdate(BaseModel):
    name: Optional[str] = None
    mode: Optional[str] = None
    live_enabled: Optional[bool] = None
    status: Optional[str] = None


def _get_or_404(account_id):
    account = database.get_broker_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="broker account not found")
    return account


def _with_funds(account):
    funds = database.list_broker_account_funds(account["id"])
    for f in funds:
        f["has_active_session"] = database.get_active_trading_session_for_fund(f["id"]) is not None
    return {
        **account,
        "funds": funds,
        "reserve": fund_manager.get_broker_account_reserve(account["id"]),
    }


@router.get("")
def list_broker_accounts(user=Depends(get_current_user)):
    return [_with_funds(a) for a in database.list_broker_accounts()]


@router.post("")
def create_broker_account(body: BrokerAccountCreate, user=Depends(require_admin)):
    try:
        account_id = database.create_broker_account(body.name, mode=body.mode, user_id=user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_funds(database.get_broker_account(account_id))


@router.get("/{account_id}")
def get_broker_account(account_id: int, user=Depends(get_current_user)):
    return _with_funds(_get_or_404(account_id))


@router.patch("/{account_id}")
def update_broker_account(account_id: int, body: BrokerAccountUpdate, user=Depends(require_admin)):
    _get_or_404(account_id)
    fields = body.model_dump(exclude_unset=True)
    if "live_enabled" in fields:
        fields["live_enabled"] = int(fields["live_enabled"])
    try:
        database.update_broker_account(account_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_funds(database.get_broker_account(account_id))


@router.post("/{account_id}/connect")
def connect_broker_account(account_id: int, user=Depends(require_admin)):
    """Fire-and-forget: spawns scripts/connect_broker_account.py, which
    opens a real browser window for the operator to log in through and
    writes the outcome back to the DB itself once it detects success (or
    a timeout). The UI polls GET /{account_id} for connection_status to
    change, same poll-driven pattern as pending trade confirmations."""
    _get_or_404(account_id)
    if not database.claim_broker_account_connecting(account_id):
        raise HTTPException(status_code=409, detail="a connection attempt is already in progress")
    subprocess.Popen(
        [sys.executable, str(CONNECT_SCRIPT), str(account_id)],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )
    return _with_funds(database.get_broker_account(account_id))


@router.post("/{account_id}/test-connection")
def test_broker_account_connection(account_id: int, user=Depends(require_admin)):
    """Distinct from both /connect (the full login flow) and the
    Broker page's "Run a Test Trade" (places a real Demo trade) - this
    verifies an ALREADY-connected account's session is genuinely still
    responsive, by reading its real balance, without ever submitting an
    order. Same fire-and-forget + poll pattern as /connect: this process
    never touches the browser directly - core/telegram_listener.py's own
    poll loop (which owns every account's live browser context) picks up
    the request and writes the result back."""
    account = _get_or_404(account_id)
    if account["connection_status"] != "connected":
        raise HTTPException(
            status_code=409,
            detail=f"account is {account['connection_status']!r}, not connected - use Connect first",
        )
    try:
        database.request_connection_test(account_id, requested_by=user["email"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "pending"}


@router.get("/{account_id}/test-connection")
def get_broker_account_connection_test(account_id: int, user=Depends(get_current_user)):
    _get_or_404(account_id)
    result = database.get_connection_test(account_id)
    return result or {"status": "none"}


@router.post("/{account_id}/disconnect")
def disconnect_broker_account(account_id: int, user=Depends(require_admin)):
    """Marks the account disconnected. Does not delete its persistent
    profile/cookies (see clear-session-style destructive actions
    elsewhere for that) - just stops AXIM from treating it as usable
    until reconnected, and blocks any fund pointing at it from trading
    (see fund_manager.can_trade / the session-start safety gate)."""
    _get_or_404(account_id)
    database.update_broker_account(account_id, connection_status="disconnected")
    return _with_funds(database.get_broker_account(account_id))


@router.post("/{account_id}/archive")
def archive_broker_account(account_id: int, user=Depends(require_admin)):
    _get_or_404(account_id)
    in_use = database.list_broker_account_funds(account_id)
    if in_use:
        names = ", ".join(f["name"] for f in in_use)
        raise HTTPException(
            status_code=409,
            detail=f"still assigned to: {names} - unassign it from every fund first",
        )
    database.update_broker_account(account_id, status="archived")
    return _with_funds(database.get_broker_account(account_id))
