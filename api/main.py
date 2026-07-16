"""
AXIM control API - FastAPI backend for the web UI (docs/AXIM_UI_PLAN.md).

Phase 1: Telegram channel manager + start/stop/pause/emergency-stop
control + basic status. Runs as its OWN process, separate from
core/telegram_listener.py - controls it through shared DB state
(ui_channels, ui_control_state) and the Windows Scheduled Task, never by
importing/running the trading engine in this process.

Binds to 127.0.0.1 only by default - local, single-operator tool, same
posture as the existing core/dashboard_server.py. Remote access (a
Remote Client over Tailscale) is opt-in via config/settings.py's
API_BIND_HOST/API_BIND_PORT/ALLOWED_ORIGINS - see
docs/AXIM_REMOTE_ACCESS.md. Nothing here is ever exposed to the public
internet as a default.

Run: python -m uvicorn api.main:app --host $env:API_BIND_HOST --port $env:API_BIND_PORT
(scripts/install_api_scheduled_task.ps1 reads the same settings)
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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import telegram_channels
import process_control
import risk_manager
import session_manager
import trade_statistics
import timeline_report
import log_reader
from signal_parser import parse_signal
from settings import (
    WATCH_CHANNELS, ACCOUNT, MAX_TRADE_AMOUNT, MAX_TRADES_PER_HOUR,
    MAX_CONSECUTIVE_LOSSES, COOLDOWN_AFTER_LOSS_SECONDS,
    DUPLICATE_SIGNAL_WINDOW_SECONDS, MINIMUM_PAYOUT, MAX_DAILY_LOSS,
    TRADE_AMOUNT, ALLOWED_ORIGINS, ENABLE_API_DOCS,
)
from logger import get_logger

import auth_routes as auth_module
import admin as admin_module
import telegram_admin as telegram_admin_module
import sessions as sessions_module
import risk_engine_routes as risk_engine_module
import trades as trades_module
import backups as backups_module
import rules as rules_module
import billing_routes as billing_module
import backtest_routes as backtest_module
import funds_routes as funds_module
import broker_accounts_routes as broker_accounts_module
import notifications as notifications_module
import event_stream_routes as event_stream_module
import capital_strategies_routes as capital_strategies_module
import money_studio_routes as money_studio_module
import capital_recommendation_routes as capital_recommendation_module
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
database.seed_money_studio_templates()

# /docs, /redoc, /openapi.json are unauthenticated by FastAPI's own
# design - off by default (ENABLE_API_DOCS, config/settings.py) so
# opening API_BIND_HOST up to a Tailscale network doesn't also hand out
# the full route/schema map, admin endpoints included, to anyone who can
# merely reach the server without logging in.
app = FastAPI(
    title="AXIM Trader API",
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

# CORS (docs/AXIM_REMOTE_ACCESS.md) - empty ALLOWED_ORIGINS (the default)
# means no cross-origin browser requests are permitted at all, matching
# today's local-only behavior exactly. A Remote Client's browser only
# needs this if it loads the web UI from a different origin than the API
# itself (e.g. a future separately-hosted dashboard); the desktop Remote
# Client and same-origin web UI never hit this path. "*" is rejected
# outright rather than silently ignored - it's incompatible with
# allow_credentials=True (browsers refuse the combination outright, so a
# wildcard here would just be a confusing dead setting) and this app's
# cookie-based sessions require credentials.
_cors_origins = [o for o in ALLOWED_ORIGINS if o != "*"]
if "*" in ALLOWED_ORIGINS:
    logger.warning(
        "ALLOWED_ORIGINS contains '*' - wildcard origins are incompatible "
        "with credentialed (cookie) requests and have been ignored. List "
        "explicit origins instead, e.g. https://your-machine.tailnet-name.ts.net"
    )
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Standard defense-in-depth headers on every response - none of
    this was set anywhere before. X-Frame-Options is the one that
    matters most here: AXIM's login page controls access to a real,
    money-moving account, and without it a malicious page could frame
    AXIM invisibly and trick a logged-in user into clicking what looks
    like that page's own UI but is actually a real AXIM action
    (clickjacking) - Tailscale-only network reach doesn't prevent this,
    since the attacking page just needs to be loaded in the same
    browser as an authenticated AXIM session, not on the same network.
    HSTS is only added when the request actually arrived over HTTPS -
    same reasoning as _request_is_https() elsewhere: asserting it
    unconditionally over a plain-HTTP local/Tailscale deployment would
    be actively wrong, not just unnecessary."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(auth_module.router)
app.include_router(admin_module.router)
app.include_router(telegram_admin_module.router)
app.include_router(sessions_module.router)
app.include_router(risk_engine_module.router)
app.include_router(trades_module.router)
app.include_router(backups_module.router)
app.include_router(rules_module.router)
app.include_router(billing_module.router)
app.include_router(backtest_module.router)
app.include_router(funds_module.router)
app.include_router(broker_accounts_module.router)
app.include_router(notifications_module.router)
app.include_router(event_stream_module.router)
app.include_router(capital_strategies_module.router)
app.include_router(money_studio_module.router)
app.include_router(capital_recommendation_module.router)

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
    default_expiry: Optional[str] = None


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


@app.get("/reset-password")
def reset_password_page():
    return _serve("reset_password.html")


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


@app.get("/capital-strategies")
def capital_strategies_page():
    return _serve("capital_strategies.html")


@app.get("/trades")
def trades_page():
    return _serve("trades.html")


@app.get("/performance")
def performance_page():
    return _serve("performance.html")


@app.get("/broker")
def broker_page():
    return _serve("broker.html")


@app.get("/automation")
def automation_page():
    return _serve("automation.html")


@app.get("/rules")
def rules_page_redirect(request: Request):
    """Automation Studio was renamed from "Rule Builder" - keep the old
    URL working (preserves any existing bookmarks/links) rather than
    silently 404ing. Forwards the query string (e.g. ?fund=X) unchanged."""
    suffix = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/automation{suffix}")


@app.get("/billing")
def billing_page():
    return _serve("billing.html")


@app.get("/strategy-lab")
def strategy_lab_page():
    return _serve("strategy_lab.html")


@app.get("/funds")
def funds_page():
    return _serve("funds.html")


@app.get("/guide")
def guide_page():
    return _serve("guide.html")


@app.get("/logs")
def logs_page():
    return _serve("logs.html")


@app.get("/settings")
def settings_page():
    return _serve("settings.html")


@app.get("/wizard")
def wizard_page():
    return _serve("wizard.html")


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
    #
    # Must also end every active session, not just flip the control-state
    # flags - a session left status="active" after this is a stale,
    # misleading record and, more importantly, skips the state that
    # actually closing a session performs (Profit Vault triggers,
    # session-scoped rule cleanup). This is the button web/dashboard.html's
    # Emergency Stop calls - it must produce the exact same end state as
    # the session-scoped POST /api/sessions/{id}/emergency-stop below.
    database.set_control_state(paused=True, emergency_stop=True)
    session_manager.end_all_active_sessions("stopped_emergency", f"emergency stop by {user['email']}")
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


@app.get("/api/settings/developer-mode")
def get_developer_mode(user=Depends(get_current_user)):
    """System-wide flag (not per-user) that reveals developer/technical
    detail elsewhere in the app - e.g. Trade Center's raw trade_id/
    session_id - to anyone logged in while it's on, matching "Never
    expose developer tools ... unless they intentionally enter Developer
    Mode" rather than gating it per-role."""
    return {"enabled": bool(database.get_setting("developer_mode", default=False))}


@app.put("/api/settings/developer-mode")
def set_developer_mode(body: dict, user=Depends(require_admin)):
    database.set_setting("developer_mode", bool(body.get("enabled")))
    logger.info("api: developer mode set to %s by %s", body.get("enabled"), user["email"])
    return {"enabled": bool(database.get_setting("developer_mode", default=False))}


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

    Live account balance: self-reported by the listener the same way as
    the process-health columns (pocket_dom.read_balance against its own
    warm page, see telegram_listener.py's _heartbeat_loop) - None until
    the first successful read after a (re)start, or if every read since
    has failed; never fabricated."""
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
        "balance": heartbeat.get("balance") if heartbeat else None,
    }


@app.post("/api/broker/test-trade")
def request_test_trade(session_id: Optional[int] = None, user=Depends(require_admin)):
    """Queues a real test trade for core/telegram_listener.py's own poll
    loop to execute through the SAME live coordinator/worker_pool - this
    API process never calls the trading engine directly. Hard-blocked
    here AND independently in the listener's poll loop if ACCOUNT isn't
    DEMO (belt and suspenders on anything that can place a real trade).

    session_id is optional - omitted (as web/broker.html's existing Test
    Trade button always does today), it runs through the legacy default
    coordinator exactly as before. Passed, it runs through that specific
    session's Fund/broker account instead (core/broker_account_manager.py's
    route_signal) - lets a Fund's own connection be test-fired without
    waiting for real Telegram traffic."""
    if ACCOUNT.upper() != "DEMO":
        raise HTTPException(status_code=403, detail=f"refused: ACCOUNT is {ACCOUNT!r}, not DEMO")
    if session_id is not None and database.get_trading_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        database.request_test_trade(user["email"], session_id=session_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    logger.info("api: test trade requested by %s (session_id=%s)", user["email"], session_id)
    return database.get_pending_test_trade()


@app.get("/api/broker/test-trade")
def get_test_trade_status(user=Depends(get_current_user)):
    return database.get_pending_test_trade()


@app.post("/api/broker/clear-session")
def clear_broker_session(user=Depends(require_admin)):
    """Deletes the persistent Chrome profile (sessions/pocket_browser) so
    the next listener start requires a completely fresh Pocket Option
    login - destructive, only allowed while the listener process is
    stopped (a running Chrome process has these files open; deleting out
    from under it would corrupt the profile, not just log it out)."""
    if process_control.get_status()["running"]:
        raise HTTPException(status_code=409, detail="stop the listener process first")
    profile_dir = PROJECT_ROOT / "sessions" / "pocket_browser"
    if profile_dir.exists():
        import shutil
        shutil.rmtree(profile_dir, ignore_errors=True)
    logger.warning("api: Pocket Option browser session cleared via UI by %s", user["email"])
    return {"status": "cleared"}


@app.get("/api/portfolio/growth-curve")
def portfolio_growth_curve(days: int = 90, user=Depends(get_current_user)):
    """Real, chronologically-ordered cumulative P/L points across every
    Fund's closed trades - powers Home's portfolio growth chart. Read-
    only, reuses database.get_portfolio_growth_curve verbatim (ported
    from the UI Vision branch) - returns however many real points exist
    in the window, never padded/interpolated to look like more history
    than is real."""
    points = database.get_portfolio_growth_curve(days=days)
    return {"days": days, "points": points}


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


@app.get("/api/performance")
def performance(user=Depends(get_current_user)):
    """Full Performance page report (docs/AXIM_APP_PLAN.md Phase 5):
    daily/weekly/monthly/yearly/lifetime, best/worst channel/asset/time-
    of-day, max drawdown, longest streaks, per-session performance, and
    an honestly-scoped Martingale/Compounding summary (see
    core/trade_statistics.martingale_and_compounding_performance's own
    docstring for exactly what is and isn't tracked historically)."""
    return trade_statistics.performance_report()


@app.get("/api/logs")
def logs(since: str = None, until: str = None, level: str = None, module: str = None,
         search: str = None, limit: int = 200, user=Depends(require_admin)):
    """Real log entries (core/logger.py's own rotating files) + admin_actions,
    merged and filterable - Owner/Admin only, since raw logs can contain
    operational detail (session IDs, asset names, error internals) not
    meant for a general User role. Session/channel filtering is covered
    by `search` (free text) rather than dedicated structured filters -
    log lines aren't parsed down to a structured session_id/channel_id
    today, see core/log_reader.py's own docstring."""
    return log_reader.read_logs(since=since, until=until, level=level, module=module, search=search, limit=limit)


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
