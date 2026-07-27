"""Trade Center API (docs/AXIM_APP_PLAN.md Phase 5) - list + detail views
over the existing signals table. No new trading logic - purely read-only
reporting over data core/trade_coordinator.py and execution/pocket_executor.py
already record.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from fastapi import APIRouter, Depends, HTTPException

import database
from auth_routes import get_current_user
from settings import MARTIN_TRADER_CHANNEL_ID

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = 50, user=Depends(get_current_user)):
    return database.get_recent_signals(limit)


@router.get("/martin-trader-summary")
def martin_trader_summary(user=Depends(get_current_user)):
    """The live session summary core/trade_series_engine.py's own docstring
    promises (signals received, series completed, wins/losses, win% by
    signal, total entries executed, net demo P/L) - registered before
    /{trade_id} so this literal path is never mistaken for a trade_id."""
    return database.get_trade_series_summary(channel_id=MARTIN_TRADER_CHANNEL_ID)


@router.get("/{trade_id}")
def get_trade(trade_id: int, user=Depends(get_current_user)):
    detail = database.get_signal_detail(trade_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="trade not found")
    detail["screenshot_urls"] = [
        {"label": Path(p).stem, "url": f"/api/screenshots/{trade_id}/{Path(p).name}"}
        for p in detail["screenshots"]
    ]
    return detail
