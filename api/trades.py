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
sys.path.insert(0, str(CORE_DIR))

from fastapi import APIRouter, Depends, HTTPException

import database
from auth_routes import get_current_user

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = 50, user=Depends(get_current_user)):
    return database.get_recent_signals(limit)


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
