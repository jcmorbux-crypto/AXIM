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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

logger = get_logger("axim.ui", filename="ui.log")

database.initialize_database()

app = FastAPI(title="AXIM Control API")

WEB_DIR = PROJECT_ROOT / "web"


class ChannelToggle(BaseModel):
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


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="web/index.html not found")
    return FileResponse(index_path)


@app.get("/api/channels")
def list_channels():
    return database.list_channels()


@app.post("/api/channels/sync")
async def sync_channels():
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
def set_channel_enabled(channel_id: int, body: ChannelToggle):
    database.set_channel_enabled(channel_id, body.enabled)
    return {"id": channel_id, "enabled": body.enabled}


@app.get("/api/control")
def get_control_state():
    return database.get_control_state()


@app.post("/api/control/pause")
def pause():
    database.set_control_state(paused=True)
    logger.info("api: trading paused via UI")
    return database.get_control_state()


@app.post("/api/control/resume")
def resume():
    database.set_control_state(paused=False)
    logger.info("api: trading resumed via UI")
    return database.get_control_state()


@app.post("/api/control/emergency-stop")
def emergency_stop():
    database.set_control_state(paused=True, emergency_stop=True)
    logger.warning("api: EMERGENCY STOP triggered via UI")
    return database.get_control_state()


@app.post("/api/control/clear-emergency-stop")
def clear_emergency_stop():
    database.set_control_state(emergency_stop=False)
    logger.info("api: emergency stop cleared via UI")
    return database.get_control_state()


@app.get("/api/status")
def status():
    process_status = process_control.get_status()
    control_state = database.get_control_state()
    return {
        "process": process_status,
        "control": control_state,
        "watch_channels_env": WATCH_CHANNELS,
        "enabled_channel_count": len(database.get_enabled_channels()),
    }


@app.post("/api/process/start")
def start_process():
    return process_control.start_listener()


@app.post("/api/process/stop")
def stop_process():
    return process_control.stop_listener()


@app.get("/api/settings")
def get_settings():
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
def put_settings(body: MoneyManagementSettings):
    updated = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            database.set_setting(key, value)
            updated[key] = value
    logger.info("api: settings updated via UI: %s", updated)
    return {"updated": updated}


@app.get("/api/pocket-option/status")
def pocket_option_status():
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
def dashboard():
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
def parse_test(body: ParseTestRequest):
    """Runs the REAL parsers/signal_parser.parse_signal() against a
    pasted sample message - not a mock or a re-implementation, the exact
    function the live listener calls on every incoming message."""
    result = parse_signal(body.message)
    return {"input": body.message, "parsed": result}


@app.get("/api/screenshots/{trade_id}")
def list_trade_screenshots(trade_id: int):
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
def get_trade_screenshot(trade_id: int, filename: str):
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
