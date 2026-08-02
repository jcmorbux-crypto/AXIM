"""Trade Center API (docs/AXIM_APP_PLAN.md Phase 5) - list + detail views
over the existing signals table. No new trading logic - purely read-only
reporting over data core/trade_coordinator.py and execution/pocket_executor.py
already record.
"""
import csv
import io
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

import database
from auth_routes import get_current_user
from settings import MARTIN_TRADER_CHANNEL_ID

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = 50, search: Optional[str] = None, result: Optional[str] = None,
                 since: Optional[str] = None, until: Optional[str] = None, session_id: Optional[int] = None,
                 fund_id: Optional[int] = None, user=Depends(get_current_user)):
    """No filters given -> database.filter_signals returns byte-identical
    rows to the old database.get_recent_signals(limit) call this endpoint
    used before 2026-08-01 (same columns, same WHERE-less query, same
    ORDER BY id DESC) - existing callers (web/trades.html's plain refresh,
    the Dashboard activity table via get_recent_signals directly) are
    unaffected either way."""
    return database.filter_signals(search=search, result=result, since=since, until=until,
                                    session_id=session_id, fund_id=fund_id, limit=limit)


# Registered before /{trade_id} for the same reason martin-trader-summary
# is above - "export" would otherwise be swallowed as a trade_id path
# param and 422 on the int conversion.
@router.get("/export")
def export_trades(format: str = "csv", limit: int = 10000, search: Optional[str] = None,
                   result: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                   session_id: Optional[int] = None, fund_id: Optional[int] = None,
                   user=Depends(get_current_user)):
    """Same filters as list_trades above, so "export what I'm currently
    looking at" (the filtered view) is possible, not just "export
    everything" - just a much higher default limit (a real export should
    cover the operator's actual history, not the 50-row page view) and a
    file response instead of JSON for csv. Same csv/io.StringIO/Response
    pattern already proven by api/backtest_routes.py's export_run - kept
    consistent rather than inventing a second export convention."""
    trades = database.filter_signals(search=search, result=result, since=since, until=until,
                                      session_id=session_id, fund_id=fund_id, limit=limit)

    if format == "json":
        import json as json_module
        return Response(content=json_module.dumps(trades, indent=2), media_type="application/json",
                         headers={"Content-Disposition": "attachment; filename=axim_trades.json"})

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "received_at", "opened_at", "closed_at", "channel", "asset", "direction",
                          "timeframe", "trade_amount", "execution_status", "result", "payout",
                          "profit_loss", "session_id"])
        for t in trades:
            writer.writerow([t["id"], t["received_at"], t["opened_at"], t["closed_at"], t["channel"],
                              t["asset"], t["direction"], t["timeframe"], t["trade_amount"],
                              t["execution_status"], t["result"], t["payout"], t["profit_loss"],
                              t["session_id"]])
        return Response(content=buffer.getvalue(), media_type="text/csv",
                         headers={"Content-Disposition": "attachment; filename=axim_trades.csv"})

    raise HTTPException(status_code=400, detail="format must be csv or json")


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
