"""
AXIM control API - FastAPI backend for the web UI (docs/AXIM_UI_PLAN.md).

Phase 1: Telegram channel manager + start/stop/pause/emergency-stop
control + basic status. Runs as its OWN process, separate from
core/telegram_listener.py - controls it through shared DB state
(ui_channels, ui_control_state) and the Windows Scheduled Task, never by
importing/running the trading engine in this process.

Binds to 127.0.0.1 only - local, single-operator tool, same posture as
the existing core/dashboard_server.py.

Run: python -m uvicorn api.main:app --host 127.0.0.1 --port 8090
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
PARSERS_DIR = PROJECT_ROOT / "parsers"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(PARSERS_DIR))

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import telegram_channels
import process_control
import risk_manager
import trade_statistics
import timeline_report
from signal_parser import parse_signal
from settings import (
    WATCH_CHANNELS, ACCOUNT, MAX_TRADE_AMOUNT, MAX_TRADES_PER_HOUR,
    MAX_CONSECUTIVE_LOSSES, COOLDOWN_AFTER_LOSS_SECONDS,
    DUPLICATE_SIGNAL_WINDOW_SECONDS, MINIMUM_PAYOUT, MAX_DAILY_LOSS,
    TRADE_AMOUNT,
)
from logger import get_logger

import auth_routes as auth_module
import admin as admin_module
import telegram_admin as telegram_admin_module
import sessions as sessions_module
import risk_engine_routes as risk_engine_module
from auth_routes import get_current_user, require_admin

# 3x the listener's own HEARTBEAT_INTERVAL_SECONDS (30s) - margin for a
# slow tick before treating the heartbeat as genuinely stale.
HEARTBEAT_STALE_THRESHOLD_SECONDS = 45

# key -> static .env-derived fallback, used to compute the EFFECTIVE value
# (what's actually enforced right now) for GET /api/settings - mirrors
# risk_manager._setting's own (key, static_default) pairs exactly, so this
# can never silently drift out of sync with what's really enforced.
_SETTING_STATIC_DEFAULTS = {
    "starting_bankroll": 0,
    "trade_sizing_mode": "fixed",
    "fixed_trade_amount": TRADE_AMOUNT,
    "trade_sizing_percent": 1.0,
    "max_trade_amount": MAX_TRADE_AMOUNT,
    "max_daily_loss": MAX_DAILY_LOSS,
    "daily_profit_target": 0,
    "max_trades_per_hour": MAX_TRADES_PER_HOUR,
    "max_trades_per_day": 0,
    "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES,
    "cooldown_after_loss_seconds": COOLDOWN_AFTER_LOSS_SECONDS,
    "minimum_payout": MINIMUM_PAYOUT,
    "duplicate_signal_window_seconds": DUPLICATE_SIGNAL_WINDOW_SECONDS,
}

AXIM_VERSION = "0.9.0-dev"  # pre-release - see docs/AXIM_APP_PLAN.md build order

logger = get_logger("axim.ui", filename="ui.log")

database.initialize_database()
database.seed_risk_profile_templates()

app = FastAPI(title="AXIM Control API")
app.include_router(auth_module.router)
app.include_router(admin_module.router)
app.include_router(telegram_admin_module.router)
app.include_router(sessions_module.router)
app.include_router(risk_engine_module.router)

WEB_DIR = PROJECT_ROOT / "web"

# Static assets only (theme.css, shell.js, images) - HTML pages are served
# through their own explicit routes below (not from this mount), so every
# page load goes through a single, auditable list of routes rather than
# "whatever file happens to sit in web/".
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web-static")


def _serve(filename):
    path = WEB_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"web/{filename} not found")
    return FileResponse(path)


class ChannelToggle(BaseModel):
    enabled: bool


class ChannelConfigUpdate(BaseModel):
    source_type: Optional[str] = None
    priority: Optional[int] = None
    trigger_command: Optional[str] = None
    command_wait_for_result: Optional[bool] = None
    max_requests_per_session: Optional[int] = None


class SignalRuleCreate(BaseModel):
    find_pattern: str
    replace_with: str
    rule_name: Optional[str] = None


class SignalRuleToggle(BaseModel):
    enabled: bool


class ParseTestRequest(BaseModel):
    message: str


# Every field optional - PUT /api/settings only overwrites the keys the
# caller actually sends, matching database.set_setting's per-key model
# rather than requiring the whole settings object every time.
class MoneyManagementSettings(BaseModel):
    starting_bankroll: Optional[float] = None
    trade_sizing_mode: Optional[str] = None       # "fixed" | "percent"
    fixed_trade_amount: Optional[float] = None
    trade_sizing_percent: Optional[float] = None
    max_trade_amount: Optional[float] = None
    max_daily_loss: Optional[float] = None
    daily_profit_target: Optional[float] = None
    max_trades_per_hour: Optional[int] = None
    max_trades_per_day: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    cooldown_after_loss_seconds: Optional[int] = None
    minimum_payout: Optional[int] = None
    duplicate_signal_window_seconds: Optional[int] = None


@app.get("/api/version")
def version():
    return {"version": AXIM_VERSION}


@app.get("/")
def index():
    # Client-side redirect chain: login.html calls /api/auth/bootstrap-status
    # and /api/auth/me to decide whether to show the bootstrap-owner form,
    # the login form, or bounce straight to dashboard.html.
    return _serve("login.html")


@app.get("/login")
def login_page():
    return _serve("login.html")


@app.get("/dashboard")
def dashboard_page():
    return _serve("dashboard.html")


@app.get("/users")
def users_page():
    return _serve("users.html")


@app.get("/telegram")
def telegram_page():
    return _serve("telegram.html")


@app.get("/inspector")
def inspector_page():
    return _serve("inspector.html")


@app.get("/sessions")
def sessions_page():
    return _serve("sessions.html")


@app.get("/risk")
def risk_page():
    return _serve("risk.html")


@app.get("/legacy")
def legacy_page():
    """The original dark-theme single-page control UI - kept reachable
    (not deleted) while its panels are migrated into the new light-theme
    per-page structure (docs/AXIM_UI_PLAN.md)."""
    return _serve("index.html")


@app.get("/api/channels")
def list_channels(user=Depends(get_current_user)):
    return database.list_channels()


@app.post("/api/channels/sync")
async def sync_channels(user=Depends(require_admin)):
    """Triggers a real Telethon dialog fetch via the dedicated UI session
    (core/telegram_channels.py) - never touches enabled flags, only
    identity (chat_id/username/title). Requires axim_ui_session.session to
    already be authenticated (run `python core/telegram_channels.py` once,
    interactively, if this is the first time)."""
    try:
        count = await telegram_channels.sync_dialogs()
    except Exception as e:
        logger.error("api: channel sync failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return {"synced": count}


@app.patch("/api/channels/{channel_id}")
def set_channel_enabled(channel_id: int, body: ChannelToggle, user=Depends(require_admin)):
    database.set_channel_enabled(channel_id, body.enabled)
    return {"id": channel_id, "enabled": body.enabled}


def _get_channel_or_404(channel_id):
    channels = {c["id"]: c for c in database.list_channels()}
    if channel_id not in channels:
        raise HTTPException(status_code=404, detail="channel not found")
    return channels[channel_id]


@app.patch("/api/channels/{channel_id}/config")
def set_channel_config(channel_id: int, body: ChannelConfigUpdate, user=Depends(require_admin)):
    _get_channel_or_404(channel_id)
    updates = body.model_dump(exclude_unset=True)
    try:
        database.set_channel_config(channel_id, **updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _get_channel_or_404(channel_id)


@app.get("/api/channels/{channel_id}/messages")
def channel_messages(channel_id: int, limit: int = 25, user=Depends(get_current_user)):
    channel = _get_channel_or_404(channel_id)
    return database.list_recent_channel_messages(chat_id=channel["chat_id"], username=channel["username"], limit=limit)


@app.get("/api/channels/{channel_id}/performance")
def channel_performance(channel_id: int, user=Depends(get_current_user)):
    channel = _get_channel_or_404(channel_id)
    return database.get_channel_performance(channel["title"])


@app.get("/api/channels/{channel_id}/rules")
def list_channel_rules(channel_id: int, user=Depends(get_current_user)):
    _get_channel_or_404(channel_id)
    return database.list_signal_rules(channel_id)


@app.post("/api/channels/{channel_id}/rules")
def create_channel_rule(channel_id: int, body: SignalRuleCreate, user=Depends(require_admin)):
    _get_channel_or_404(channel_id)
    try:
        re.compile(body.find_pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"invalid regex: {e}")
    rule_id = database.create_signal_rule(channel_id, body.find_pattern, body.replace_with, body.rule_name)
    logger.info("api: signal rule created by %s for channel %s", user["email"], channel_id)
    return {"id": rule_id}


@app.patch("/api/signal-rules/{rule_id}")
def toggle_signal_rule(rule_id: int, body: SignalRuleToggle, user=Depends(require_admin)):
    database.set_signal_rule_enabled(rule_id, body.enabled)
    return {"id": rule_id, "enabled": body.enabled}


@app.delete("/api/signal-rules/{rule_id}")
def remove_signal_rule(rule_id: int, user=Depends(require_admin)):
    database.delete_signal_rule(rule_id)
    return {"status": "deleted"}


@app.get("/api/control")
def get_control_state(user=Depends(get_current_user)):
    return database.get_control_state()


@app.post("/api/control/pause")
def pause(user=Depends(require_admin)):
    database.set_control_state(paused=True)
    logger.info("api: trading paused via UI by %s", user["email"])
    return database.get_control_state()


@app.post("/api/control/resume")
def resume(user=Depends(require_admin)):
    database.set_control_state(paused=False)
    logger.info("api: trading resumed via UI by %s", user["email"])
    return database.get_control_state()


@app.post("/api/control/emergency-stop")
def emergency_stop(user=Depends(get_current_user)):
    # Deliberately just get_current_user, not require_admin - ANY logged-in
    # user must be able to halt trading immediately; requiring admin here
    # would be a safety regression (a non-admin who spots a problem
    # couldn't stop it). Every other mutating control stays admin-only.
    database.set_control_state(paused=True, emergency_stop=True)
    logger.warning("api: EMERGENCY STOP triggered via UI by %s", user["email"])
    return database.get_control_state()


@app.post("/api/control/clear-emergency-stop")
def clear_emergency_stop(user=Depends(require_admin)):
    database.set_control_state(emergency_stop=False)
    logger.info("api: emergency stop cleared via UI by %s", user["email"])
    return database.get_control_state()


@app.post("/api/control/test-mode/enable")
def enable_test_mode(user=Depends(require_admin)):
    """Test mode: signals still go through parsing/risk-evaluation/asset
    resolution exactly as normal, but trade_coordinator.py stops one step
    short of pocket_executor.prepare_trade (the actual browser click) -
    same shape as the existing static PREVIEW_ONLY/AUTO_EXECUTE .env gate,
    just runtime-flippable from the UI instead of requiring a restart."""
    database.set_control_state(test_mode=True)
    logger.info("api: test mode enabled via UI by %s", user["email"])
    return database.get_control_state()


@app.post("/api/control/test-mode/disable")
def disable_test_mode(user=Depends(require_admin)):
    database.set_control_state(test_mode=False)
    logger.info("api: test mode disabled via UI by %s", user["email"])
    return database.get_control_state()


@app.get("/api/status")
def status(user=Depends(get_current_user)):
    process_status = process_control.get_status()
    control_state = database.get_control_state()
    return {
        "process": process_status,
        "control": control_state,
        "watch_channels_env": WATCH_CHANNELS,
        "enabled_channel_count": len(database.get_enabled_channels()),
    }


@app.post("/api/process/start")
def start_process(user=Depends(require_admin)):
    return process_control.start_listener()


@app.post("/api/process/stop")
def stop_process(user=Depends(require_admin)):
    return process_control.stop_listener()


@app.get("/api/settings")
def get_settings(user=Depends(get_current_user)):
    """Returns the EFFECTIVE value of every money-management setting -
    whatever's actually enforced right now, whether that's a UI override
    or the static .env-derived default - plus the live-computed current
    bankroll and what the next trade would actually be sized at
    (risk_manager.compute_trade_amount, the exact real function
    trade_coordinator.py calls, not a re-implementation of its logic)."""
    overrides = database.get_all_settings()
    effective = {
        key: overrides.get(key, static_default)
        for key, static_default in _SETTING_STATIC_DEFAULTS.items()
    }
    lifetime_pnl = database.get_lifetime_realized_pnl()
    current_bankroll = effective["starting_bankroll"] + lifetime_pnl
    return {
        "effective": effective,
        "overridden_keys": list(overrides.keys()),
        "lifetime_realized_pnl": lifetime_pnl,
        "current_bankroll": current_bankroll,
        "next_trade_amount_preview": risk_manager.compute_trade_amount(TRADE_AMOUNT),
    }


@app.put("/api/settings")
def put_settings(body: MoneyManagementSettings, user=Depends(require_admin)):
    updated = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            database.set_setting(key, value)
            updated[key] = value
    logger.info("api: settings updated via UI by %s: %s", user["email"], updated)
    return {"updated": updated}


@app.get("/api/pocket-option/status")
def pocket_option_status(user=Depends(get_current_user)):
    """Session/worker health for the Pocket Option connection panel.
    Sourced from ui_listener_heartbeat, which the (separate-process)
    listener writes every 30s - not a live query against the browser
    itself, since this API process never touches the browser. A stale
    heartbeat (no update in a while) is the signal something's wrong even
    if the OS process is still technically running.

    Live account balance is NOT yet implemented - no DOM selector for it
    has been built/verified yet (flagged in docs/AXIM_UI_PLAN.md as a
    follow-up), so this deliberately does not fabricate a number."""
    heartbeat = database.get_listener_heartbeat()
    process_status = process_control.get_status()
    stale = True
    if heartbeat and heartbeat.get("updated_at"):
        age_seconds = (datetime.now() - datetime.fromisoformat(heartbeat["updated_at"])).total_seconds()
        stale = age_seconds > HEARTBEAT_STALE_THRESHOLD_SECONDS
    return {
        "process_running": process_status["running"],
        "account_mode": ACCOUNT,
        "heartbeat": heartbeat,
        "heartbeat_stale": stale,
        "balance": None,  # not yet implemented - see docstring above
    }


@app.get("/api/dashboard")
def dashboard(user=Depends(get_current_user)):
    """Same data core/dashboard_server.py's /api/data has always served
    (daily/weekly stats, recovery-rate data, P50/P95/P99 latency, recent
    signals) - reusing those exact functions, not a re-implementation.
    Supersedes the old stdlib dashboard rather than running both; that one
    still works standalone if preferred (python core/dashboard_server.py)."""
    _, timeline_aggregate = timeline_report.generate_report(limit=200)
    return {
        "statistics": trade_statistics.full_report(),
        "recovery": database.get_recovery_event_stats(),
        "timeline": timeline_aggregate,
        "recent_trades": database.get_recent_signals(25),
    }


@app.post("/api/parse-test")
def parse_test(body: ParseTestRequest, user=Depends(get_current_user)):
    """Runs the REAL parsers/signal_parser.parse_signal() against a
    pasted sample message - not a mock or a re-implementation, the exact
    function the live listener calls on every incoming message."""
    result = parse_signal(body.message)
    return {"input": body.message, "parsed": result}


@app.get("/api/screenshots/{trade_id}")
def list_trade_screenshots(trade_id: int, user=Depends(get_current_user)):
    conn = database.get_connection()
    row = conn.execute("SELECT screenshot_paths FROM signals WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    if row is None or not row["screenshot_paths"]:
        return {"trade_id": trade_id, "screenshots": []}
    paths = json.loads(row["screenshot_paths"])
    return {
        "trade_id": trade_id,
        "screenshots": [
            {"label": Path(p).stem, "url": f"/api/screenshots/{trade_id}/{Path(p).name}"}
            for p in paths
        ],
    }


@app.get("/api/screenshots/{trade_id}/{filename}")
def get_trade_screenshot(trade_id: int, filename: str, user=Depends(get_current_user)):
    # filename must be exactly one of the two labels prepare_trade ever
    # writes (execution/pocket_executor.py's _capture_screenshot_background
    # calls) - rejects anything else outright rather than trying to
    # sanitize an arbitrary path, closing off path traversal entirely.
    if filename not in ("prepared.png", "clicked.png"):
        raise HTTPException(status_code=400, detail="invalid screenshot filename")
    path = PROJECT_ROOT / "logs" / "trades" / str(trade_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(path)
