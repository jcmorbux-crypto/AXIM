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
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))
sys.path.insert(0, str(API_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import database
import telegram_channels
import process_control
from settings import WATCH_CHANNELS
from logger import get_logger

logger = get_logger("axim.ui", filename="ui.log")

database.initialize_database()

app = FastAPI(title="AXIM Control API")

WEB_DIR = PROJECT_ROOT / "web"


class ChannelToggle(BaseModel):
    enabled: bool


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
