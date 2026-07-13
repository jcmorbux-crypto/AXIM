"""AXIM Trader V2 — isolated UI Vision preview server.

Runs on port 8091, completely separate from the production API (port
8090, C:\\AXIM, branch master). This process:

- Only ever imports READ functions from core/database.py, core/
  fund_manager.py, core/trade_statistics.py, and core/backtest_engine.py
  (a pure simulation module - no DB writes, no browser, no Telegram; its
  own docstring says "Pure - no DB I/O") - never core/trade_coordinator.py,
  core/broker_account_manager.py, core/telegram_listener.py, or anything
  under execution/. Those modules are never imported here, so there is no
  code path in this process that can place a trade, connect a broker
  account, or touch a live browser session - not "disabled", structurally
  absent.
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
from datetime import datetime, timedelta
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
import backtest_engine

app = FastAPI(title="AXIM Trader V2 Preview (read-only)")

WEB_V2_DIR = PROJECT_ROOT / "web_v2"

# The 6 curated starter strategies (Strategy Studio) - a genuine spread
# across the real archetype space, not an arbitrary/random 6: two
# no-martingale capital-preservation profiles (Capital Shield, Base
# Camp), the profile production's own template set already calls "the
# default starting point" (Balanced Builder - the one with a light,
# capped martingale), two no-martingale growth profiles at different
# aggressiveness (AutoPilot Growth, Elite Session), and one genuine
# controlled-martingale example (Recovery Guard) so a beginner sees what
# "capped recovery" actually looks like rather than only extremes.
# Picked by reading all 27 templates' real config, not by name alone -
# see the session that built this for the actual numbers compared.
STARTER_STRATEGY_IDS = [1, 17, 3, 26, 8, 27]

DEFAULT_SCENARIO_BANKROLL = 1000
DEFAULT_SCENARIO_TRADES = 60
DEFAULT_SCENARIO_DAYS = 30
DEFAULT_SCENARIO_PAYOUT = 88  # matches AXIM's real observed historical average payout range


def _synthetic_signal_pool(win_rate, seed, num_trades=DEFAULT_SCENARIO_TRADES,
                            days=DEFAULT_SCENARIO_DAYS, payout_percent=DEFAULT_SCENARIO_PAYOUT):
    """A labeled, honest hypothetical - NOT a prediction. AXIM has no
    verified real edge yet (see docs/AXIM_LIVE_READINESS_CHECKLIST.md),
    so this generates a clearly-scenario-tagged sequence at an ASSUMED
    win rate the caller must state, spread realistically over `days`
    calendar days so session-scoped stepping (Martingale/Momentum resets
    daily) behaves the way it would live - never presented as real
    trading history anywhere in the UI (P9: never fabricate confidence)."""
    import random
    rng = random.Random(seed)
    start = datetime.now() - timedelta(days=days)
    assets = ["EUR/USD OTC", "GBP/USD OTC", "Gold OTC", "BTC/USD OTC"]
    pool = []
    for i in range(num_trades):
        day_offset = (i / num_trades) * days
        ts = start + timedelta(days=day_offset, hours=rng.uniform(0, 8))
        result = "win" if rng.random() < win_rate else "loss"
        pool.append({
            "timestamp": ts.isoformat(), "result": result, "payout_percent": payout_percent,
            "source_type": "illustrative_scenario", "signal_id": i,
            "asset": assets[i % len(assets)], "direction": "BUY" if rng.random() < 0.5 else "SELL",
        })
    return pool


def _simulate(profile_snapshot, win_rate, seed=42):
    """Runs the SAME simulate_strategy/compute_metrics production's own
    Strategy Lab uses - see core/backtest_engine.py. Returns the bankroll
    curve (for the animated simulator), real computed metrics, and the
    same _risk_score/_best_for_label heuristics Strategy Lab already
    shows, so a strategy is never described two different ways in two
    different parts of the product."""
    pool = _synthetic_signal_pool(win_rate, seed)
    result = backtest_engine.simulate_strategy(pool, profile_snapshot, DEFAULT_SCENARIO_BANKROLL, session_window="all")
    metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], DEFAULT_SCENARIO_BANKROLL)
    curve = [{"t": i, "balance": round(DEFAULT_SCENARIO_BANKROLL + sum(
        tr["profit_loss"] for tr in result["trades"][:i + 1]
    ), 2)} for i in range(len(result["trades"]))]
    return {
        "metrics": metrics,
        "curve": [{"t": 0, "balance": DEFAULT_SCENARIO_BANKROLL}] + curve,
        "risk_score": backtest_engine._risk_score(metrics["max_drawdown_percent"], metrics["max_martingale_step_used"]),
        "best_for_label": backtest_engine._best_for_label(metrics["roi_percent"], metrics["max_drawdown_percent"]),
    }


def _describe(profile):
    """Templated, honest description built ENTIRELY from the profile's
    real stored config - never hand-written marketing copy per strategy.
    Every sentence traces to an actual field value (P9)."""
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]
    sizing = profile["sizing_mode"]

    if sizing == "kelly":
        what = "Sizes each trade using the Kelly criterion - a formula that computes the mathematically optimal stake from your estimated win rate and payout."
    elif sizing == "percent":
        what = f"Sizes each trade at {profile.get('percent_of_bankroll', 0)}% of your current trading balance."
    else:
        what = f"Sizes each trade at a fixed ${profile.get('fixed_amount', 0)}."

    how_parts = []
    if m["enabled"]:
        how_parts.append(f"after a loss, the next trade increases {m['multiplier']}x, up to {m['max_steps']} step(s), before resetting")
    if c["mode"] != "disabled":
        how_parts.append(f"risk grows via {c['mode'].replace('_', ' ')} compounding as your balance grows")
    if v["enabled"]:
        how_parts.append(f"{v['vault_percent']}% of profit is set aside into a protected vault ({v['trigger_event'].replace('_', ' ')})")
    how = ("Beyond that, " + "; ".join(how_parts) + ".") if how_parts else "It does not adjust stake size based on wins, losses, or balance growth - the stake stays constant."

    # Compositional, not a single mutually-exclusive bucket - a profile
    # with martingale AND compounding AND vault all enabled (e.g.
    # "Balanced Builder") needs ALL three reflected, not just whichever
    # branch happened to match first. Confirmed this was a real bug: the
    # first version of this function silently mis-described exactly that
    # profile as "one thing to think about at a time," the opposite of
    # its real config - caught by reading actual API output, not assumed
    # correct from the code alone.
    best_bits, worst_bits = [], []
    if m["enabled"]:
        if m["max_steps"] >= 3:
            best_bits.append("comfortable with a real chance of a multi-step losing streak, in exchange for faster recovery")
            worst_bits.append("a small bankroll that can't absorb several losses in a row")
        else:
            best_bits.append("comfortable with a short, capped step-up in stake after a loss")
            worst_bits.append("traders who want their stake to NEVER increase after a loss, even briefly")
    if c["mode"] != "disabled":
        best_bits.append("comfortable with risk size growing automatically as the balance grows")
        worst_bits.append("traders who want their dollar risk per trade to stay exactly the same every time")
    if v["enabled"]:
        if v["vault_percent"] >= 20:
            best_bits.append("traders who want to genuinely lock in profit as it happens, not just watch a number grow")
            worst_bits.append("traders trying to compound aggressively - a large vault skim works against fast balance growth")
        else:
            best_bits.append("traders who want a modest, automatic profit cushion without slowing growth much")

    if best_bits:
        best_for = ("Best for " + "; ".join(best_bits) + ".").capitalize()
        worst_for = ("Worst for " + "; ".join(worst_bits) + ".").capitalize() if worst_bits else "No major mismatch identified from this profile's config alone - it's a genuinely flexible fit."
    else:
        best_for = "A calm, predictable starting point - new signal sources, or traders who want one thing to think about at a time: the stake amount, nothing else."
        worst_for = "Traders specifically looking for fast compounding or loss-recovery mechanics - this profile deliberately has neither."

    return {"what_it_does": what, "how_it_works": how, "best_for": best_for, "worst_for": worst_for}


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


def _strategy_summary(profile, win_rate=0.5):
    sim = _simulate(profile, win_rate)
    desc = _describe(profile)
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]
    return {
        "id": profile["id"], "name": profile["name"], "description": profile["description"],
        "uses_martingale": bool(m["enabled"]), "uses_compounding": c["mode"] != "disabled",
        "uses_vault": bool(v["enabled"]),
        "risk_level": sim["risk_score"], "growth_label": sim["best_for_label"],
        "roi_percent": sim["metrics"]["roi_percent"], "max_drawdown_percent": sim["metrics"]["max_drawdown_percent"],
        **desc,
    }


@app.get("/api/preview/strategies")
def preview_strategies_starters():
    """The 6 curated starters - Strategy Studio's default page (never
    the full 27), per the explicit 'reduce cognitive overload' brief."""
    profiles = [database.get_risk_profile(i) for i in STARTER_STRATEGY_IDS]
    return {"strategies": [_strategy_summary(p) for p in profiles if p]}


@app.get("/api/preview/strategies/library")
def preview_strategies_library():
    """All 27 templates - the Advanced Library, deliberately one click
    further away than the 6 starters."""
    profiles = [p for p in database.list_risk_profiles(include_templates=True) if p["is_template"]]
    return {"strategies": [_strategy_summary(p) for p in profiles]}


@app.get("/api/preview/strategies/{strategy_id}")
def preview_strategy_detail(strategy_id: int, win_rate: float = 0.5):
    """Full educational detail for one strategy, including the animated
    bankroll simulator's real data. win_rate is a query param specifically
    so the UI can offer a scenario toggle (e.g. 45% / 50% / 55%) without
    a second endpoint - every value is honestly labeled as a hypothetical
    scenario, never presented as a prediction (P9)."""
    profile = database.get_risk_profile(strategy_id)
    if profile is None:
        return {"error": "strategy not found"}
    win_rate = max(0.1, min(0.9, win_rate))  # keep scenarios sane, still user-adjustable
    sim = _simulate(profile, win_rate)
    desc = _describe(profile)
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]
    return {
        "id": profile["id"], "name": profile["name"], "description": profile["description"],
        "sizing_mode": profile["sizing_mode"], "percent_of_bankroll": profile.get("percent_of_bankroll"),
        "uses_martingale": bool(m["enabled"]), "martingale": {"multiplier": m.get("multiplier"), "max_steps": m.get("max_steps")} if m["enabled"] else None,
        "uses_compounding": c["mode"] != "disabled", "compounding_mode": c["mode"] if c["mode"] != "disabled" else None,
        "uses_vault": bool(v["enabled"]), "vault_percent": v.get("vault_percent") if v["enabled"] else None,
        "risk_level": sim["risk_score"], "growth_label": sim["best_for_label"],
        "scenario_win_rate": win_rate,
        "metrics": sim["metrics"], "curve": sim["curve"],
        **desc,
    }


# Static V2 pages - mounted last so /api/preview/* above takes priority.
app.mount("/", StaticFiles(directory=str(WEB_V2_DIR), html=True), name="web_v2")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8091)
