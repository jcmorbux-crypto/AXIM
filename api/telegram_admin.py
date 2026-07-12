"""Telegram account linking - lets the operator enter API ID/Hash/phone
and complete the Telethon login (send code -> verify code) through the
UI instead of running core/telegram_channels.py interactively from a
terminal.

Uses the SAME dedicated UI session (axim_ui_session) as
core/telegram_channels.py - never the live listener's axim_session, so
this can safely run while the listener is up (see
core/telegram_channels.py's own docstring for why two processes can't
share one Telethon session file).

A send-code -> verify-code login is a two-step flow against one live
Telethon client connection, so this module holds that in-progress client
in memory (_pending_logins) between the two calls - single-operator tool,
not designed for concurrent linking attempts.
"""
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import database
from auth_routes import require_admin

load_dotenv()

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

UI_SESSION_NAME = os.getenv("UI_SESSION_NAME", "axim_ui_session")

# nonce -> {"client": TelegramClient, "phone": str, "phone_code_hash": str}
_pending_logins = {}


class CredentialsRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class SendCodeRequest(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone: Optional[str] = None


class VerifyCodeRequest(BaseModel):
    pending_id: str
    code: str
    password: Optional[str] = None


def _resolve_credentials(api_id=None, api_hash=None, phone=None):
    """Explicit args win (a fresh send-code call can supply new
    credentials) - else stored encrypted credentials - else the .env-
    derived config/settings.py constants, so existing installs keep
    working with zero UI configuration required."""
    if api_id and api_hash and phone:
        return api_id, api_hash, phone
    stored = database.get_decrypted_telegram_credentials()
    if stored:
        return stored
    env_api_id = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID")
    env_api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
    env_phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")
    if not (env_api_id and env_api_hash and env_phone):
        raise HTTPException(status_code=400, detail="no Telegram credentials configured yet")
    return int(env_api_id), env_api_hash, env_phone


@router.get("/credentials-status")
def credentials_status(user=Depends(require_admin)):
    return database.get_telegram_credentials_status()


@router.post("/credentials")
def save_credentials(body: CredentialsRequest, user=Depends(require_admin)):
    database.set_telegram_credentials(body.api_id, body.api_hash, body.phone)
    return database.get_telegram_credentials_status()


@router.get("/connection-status")
async def connection_status(user=Depends(require_admin)):
    """Real check against the UI session file, not a cached flag - asks
    Telethon whether axim_ui_session is actually authorized right now."""
    session_path = PROJECT_ROOT / f"{UI_SESSION_NAME}.session"
    if not session_path.exists():
        return {"session_exists": False, "authorized": False}
    try:
        api_id, api_hash, _ = _resolve_credentials()
    except HTTPException:
        return {"session_exists": True, "authorized": False, "note": "credentials not configured"}
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
        me = await client.get_me() if authorized else None
        return {
            "session_exists": True,
            "authorized": authorized,
            "account_name": f"{me.first_name} {me.last_name or ''}".strip() if me else None,
            "username": me.username if me else None,
        }
    finally:
        await client.disconnect()


async def _clear_pending_logins():
    """An operator who calls send-code and never follows through with
    verify-code (wrong number, gave up, closed the tab) left that pending
    login's TelegramClient connected forever - verify_code's cleanup only
    runs on the path that actually completes. Since this is already a
    "single-operator tool, not designed for concurrent linking attempts"
    (module docstring), the next send-code call is a safe, natural place
    to close out whatever's left over rather than letting attempts
    accumulate unboundedly."""
    for pending in list(_pending_logins.values()):
        try:
            await pending["client"].disconnect()
        except Exception:
            pass
    _pending_logins.clear()


@router.post("/connect/send-code")
async def send_code(body: SendCodeRequest, user=Depends(require_admin)):
    await _clear_pending_logins()
    api_id, api_hash, phone = _resolve_credentials(body.api_id, body.api_hash, body.phone)
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=400, detail=str(e))
    pending_id = uuid.uuid4().hex
    _pending_logins[pending_id] = {"client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash}
    return {"pending_id": pending_id, "message": f"Code sent to {phone}"}


@router.post("/connect/verify-code")
async def verify_code(body: VerifyCodeRequest, user=Depends(require_admin)):
    pending = _pending_logins.get(body.pending_id)
    if pending is None:
        raise HTTPException(status_code=400, detail="no pending login for this pending_id - call send-code again")
    client = pending["client"]
    try:
        try:
            await client.sign_in(pending["phone"], body.code, phone_code_hash=pending["phone_code_hash"])
        except SessionPasswordNeededError:
            if not body.password:
                raise HTTPException(status_code=400, detail="2FA password required")
            await client.sign_in(password=body.password)
        me = await client.get_me()
        return {"authorized": True, "account_name": f"{me.first_name} {me.last_name or ''}".strip(), "username": me.username}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await client.disconnect()
        _pending_logins.pop(body.pending_id, None)


@router.post("/disconnect")
async def disconnect(user=Depends(require_admin)):
    """Destructive - logs out the UI session and deletes its session
    file. Does NOT touch the live listener's own axim_session (a
    different session file), so this only affects the channel-manager/
    sync capability, not active trading - but the UI must still warn
    clearly before calling this."""
    session_path = PROJECT_ROOT / f"{UI_SESSION_NAME}.session"
    if not session_path.exists():
        return {"status": "already disconnected"}
    try:
        api_id, api_hash, _ = _resolve_credentials()
        client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
        await client.connect()
        await client.log_out()
    except Exception:
        pass  # best-effort logout; the session file removal below is what actually matters
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = PROJECT_ROOT / f"{UI_SESSION_NAME}.session{suffix}"
        if p.exists():
            p.unlink()
    return {"status": "disconnected"}
