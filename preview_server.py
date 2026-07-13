"""AXIM Trader V2 — isolated UI Vision preview server.

Runs on port 8091, completely separate from the production API (port
8090, C:\\AXIM, branch master). This process:

- Only ever imports READ functions from core/database.py, core/
  fund_manager.py, and core/trade_statistics.py - never
  core/trade_coordinator.py, core/broker_account_manager.py,
  core/telegram_listener.py, or anything under execution/. Those modules
  are never imported here, so there is no code path in this process that
  can place a trade, connect a broker account, or touch a live browser
  session - not "disabled", structurally absent.
- Reads from data/axim.db resolved relative to THIS process's own
  working directory (this worktree, C:/AXIM-ui-vision) - a one-time
  snapshot copy of production's database, not a live connection. Writes
  from this process (there are none exposed, but even if code drifted to
  add one) could never reach production's actual data/axim.db, because
  it is a physically different file.
- Every response is read-only aggregation. No POST/PUT/PATCH/DELETE
  routes exist in this file at all.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import database
import fund_manager
import trade_statistics

app = FastAPI(title="AXIM Trader V2 Preview (read-only)")

WEB_V2_DIR = PROJECT_ROOT / "web_v2"


def _short_result(result):
    """Production's raw `result` column can hold a full DOM accessibility-
    tree dump for a failed trade (execution/pocket_dom.py's own diagnostic
    detail) - real, useful data in Trade History's detail view, but not
    something a design preview's activity feed should ever render
    verbatim. Collapses to a short, honest label instead."""
    if not result:
        return None
    if result in ("win", "loss", "draw"):
        return result
    prefix = result.split(":", 1)[0]
    return prefix if len(prefix) < 40 else "error"


@app.get("/api/preview/home")
def preview_home():
    today = trade_statistics.daily_stats()
    active_session = database.get_active_trading_session()
    channels = database.list_channels()
    watched = [c for c in channels if c.get("enabled")]
    return {
        "today_pnl": today.get("profit_loss", 0),
        "today_trades": today.get("total_closed", 0),
        "today_win_rate": today.get("win_rate"),
        "watching_count": len(watched),
        "active_session": {
            "name": active_session["name"],
            "status": active_session["status"],
            "realized_pnl": active_session["realized_pnl"],
        } if active_session else None,
        "recent_activity": [
            {"asset": s.get("asset"), "direction": s.get("direction"), "result": _short_result(s.get("result"))}
            for s in database.get_recent_signals(limit=5)
        ],
    }


@app.get("/api/preview/portfolio")
def preview_portfolio():
    funds = fund_manager.list_funds_with_balances()
    accounts = database.list_broker_accounts()
    return {
        "funds": [
            {
                "id": f["id"], "name": f["name"],
                "trading_balance": f.get("balances", {}).get("trading_balance"),
                "total_account_value": f.get("balances", {}).get("total_account_value"),
                "status": f["status"],
            }
            for f in funds
        ],
        "broker_accounts": [
            {
                "id": a["id"], "name": a["name"], "mode": a["mode"],
                "connection_status": a["connection_status"], "last_balance": a["last_balance"],
            }
            for a in accounts
        ],
    }


@app.get("/api/preview/signals")
def preview_signals():
    channels = database.list_channels()
    out = []
    for c in channels:
        if not c.get("enabled"):
            continue
        perf = database.get_channel_performance(c["title"] or c.get("username") or "")
        out.append({
            "id": c["id"], "title": c["title"] or c.get("username"),
            "win_rate": perf.get("win_rate") if perf else None,
            "profit_loss": perf.get("profit_loss") if perf else None,
            "total_signals": perf.get("total_closed") if perf else 0,
        })
    return {"sources": out}


@app.get("/api/preview/sessions")
def preview_sessions():
    sessions = database.list_trading_sessions(limit=15)
    return {
        "sessions": [
            {
                "id": s["id"], "name": s["name"], "status": s["status"],
                "trades_count": s["trades_count"], "realized_pnl": s["realized_pnl"],
                "started_at": s["started_at"], "ended_at": s["ended_at"],
            }
            for s in sessions
        ]
    }


@app.get("/api/preview/dashboard")
def preview_dashboard():
    """One consolidated payload for the 3-column terminal Dashboard -
    LEFT (sources), CENTER (activity/sessions), RIGHT (funds/risk/
    performance/positions) - one round trip instead of 4, since this
    screen is specifically the one meant to be scanned fast."""
    today = trade_statistics.daily_stats()
    active_session = database.get_active_trading_session()
    channels = [c for c in database.list_channels() if c.get("enabled")]
    funds = fund_manager.list_funds_with_balances()
    open_trades = database.get_open_trades()

    sources = []
    for c in channels:
        perf = database.get_channel_performance(c["title"] or c.get("username") or "")
        sources.append({
            "title": c["title"] or c.get("username"),
            "win_rate": perf.get("win_rate") if perf else None,
            "total_signals": perf.get("total_closed") if perf else 0,
        })

    return {
        "today_pnl": today.get("profit_loss", 0),
        "today_trades": today.get("total_closed", 0),
        "today_win_rate": today.get("win_rate"),
        "active_session": {
            "name": active_session["name"], "status": active_session["status"],
            "realized_pnl": active_session["realized_pnl"], "trades_count": active_session["trades_count"],
        } if active_session else None,
        "sources": sources,
        "activity": [
            {
                "asset": s.get("asset"), "direction": (s.get("direction") or "").lower(),
                "result": _short_result(s.get("result")), "received_at": s.get("received_at"),
            }
            for s in database.get_recent_signals(limit=12)
        ],
        "open_positions": [
            {"id": t["id"], "asset": t["asset"], "direction": (t.get("direction") or "").lower(), "trade_amount": t["trade_amount"]}
            for t in open_trades
        ],
        "funds_summary": [
            {"name": f["name"], "trading_balance": f.get("balances", {}).get("trading_balance")}
            for f in funds
        ],
        "risk_ok": True,  # preview simplification - real page (Phase 1+) reads the actual risk-limit gate
    }


# Static V2 pages - mounted last so /api/preview/* above takes priority.
app.mount("/", StaticFiles(directory=str(WEB_V2_DIR), html=True), name="web_v2")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8091)
