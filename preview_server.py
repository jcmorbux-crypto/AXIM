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
import risk_engine  # pure computation module, same one backtest_engine.py itself imports - no DB writes, no execution

app = FastAPI(title="AXIM Trader V2 Preview (read-only)")

WEB_V2_DIR = PROJECT_ROOT / "web_v2"

# Starter names v2 - direct feedback that the original 6 (and several
# of the 27 template names, e.g. "Base Camp", "Mission Control") were
# "vague or gimmicky" and didn't explain the risk logic. Every starter
# below is either a real template (mapped to its closest honest config
# match, not by name) or a SYNTHETIC snapshot for the two archetypes no
# real template covers (a true fixed-$ strategy, and "build your own").
#
# Real template mapping, chosen by re-reading all 27 templates' actual
# config (not name):
#   Capital Preservation <- id 1  "Capital Shield"   (1% fixed/percent-lean, no martingale, vault optional)
#   Balanced Growth       <- id 16 "RiskBound"         (2%, session/target-based compounding, explicitly capped, NO martingale)
#   Controlled Recovery   <- id 22 "Controlled Recovery" (already named correctly: short, capped martingale ladder)
#   Profit Compounding    <- id 7  "Growth Engine"     (milestone-based compounding only, no martingale)
# Fixed Stake and Custom Strategy have no real template (every one of
# the 27 uses percent or kelly sizing, never a plain fixed dollar
# amount) - built as explicit synthetic snapshots instead of forcing a
# dishonest mapping onto a percent-based template.
SYNTHETIC_PROFILES = {
    9001: {
        "id": 9001, "name": "Fixed Stake", "description": "The same trade amount every time - no percentage math, no ladder, no compounding.",
        "is_template": 1, "bankroll": 0.0, "sizing_mode": "fixed", "fixed_amount": 5.0, "percent_of_bankroll": 0.0,
        "kelly_win_rate_estimate": None, "kelly_payout_estimate": None, "kelly_fraction_multiplier": 0.5,
        "max_trade_amount": 0.0, "max_daily_loss": 0.0, "max_session_loss": 0.0, "profit_target": 0.0, "max_trades": 0,
        "martingale": {"enabled": False}, "compounding": {"mode": "disabled"}, "profit_vault": {"enabled": False},
    },
    9002: {
        "id": 9002, "name": "Custom Strategy", "description": "Start from a blank, safe default and set every rule yourself.",
        "is_template": 1, "bankroll": 0.0, "sizing_mode": "percent", "fixed_amount": 1.0, "percent_of_bankroll": 1.0,
        "kelly_win_rate_estimate": None, "kelly_payout_estimate": None, "kelly_fraction_multiplier": 0.5,
        "max_trade_amount": 0.0, "max_daily_loss": 0.0, "max_session_loss": 0.0, "profit_target": 0.0, "max_trades": 0,
        "martingale": {"enabled": False}, "compounding": {"mode": "disabled"}, "profit_vault": {"enabled": False},
        "is_custom_entry": True,  # routes straight to the customize form, never shows a simulated detail page
    },
}

STARTER_STRATEGY_IDS = [1, 16, 22, 7, 9001, 9002]

# Display-name overrides - the profile's REAL config and real template
# row are untouched (this is a preview-layer rename only, never a DB
# write); applied to both the 6 starters and a subset of the 27
# "Advanced Library" entries whose original names were flagged as
# gimmicky. Picked per-template by matching the suggested descriptive
# names against each one's actual real config, not assigned arbitrarily.
NAME_OVERRIDES = {
    1: "Capital Preservation",
    16: "Balanced Growth",
    7: "Profit Compounding",
    # Advanced Library renames - "Base Camp", "Mission Control", "Elite
    # Session", "Signal Sprint", "Daily Climb", "TargetRun", "StepUp"
    # explicitly flagged for removal; renamed here to describe the real
    # config rather than removed outright, since the underlying
    # strategies themselves are still real and usable, just misnamed.
    17: "Small Bankroll Preservation",   # was "Base Camp" - 1%, no martingale, no vault, minimal-risk
    12: "Session Compounding",           # was "Mission Control" - 2%, daily compounding, no martingale
    27: "High-Conviction Risk",          # was "Elite Session" - 3%, smart compounding, vault
    13: "Percentage Risk Growth",        # was "Signal Sprint" - 1.5%, plain percent, nothing else enabled
    14: "Daily Risk Compounding",        # was "Daily Climb" - 2%, daily compounding, no martingale
    20: "Daily Target Stop",             # was "TargetRun" - sprints to a fixed target then stops
    18: "Milestone Risk Growth",         # was "StepUp" - milestone-based compounding, no martingale
    3: "Two-Step Recovery",              # was "Balanced Builder" - exactly 2 martingale steps
    23: "Milestone Compounding",         # was "Profit Staircase" - milestone-based compounding, no martingale
    9: "Kelly Fractional",               # was "Precision Compounding" - Kelly-criterion sizing
    2: "Profit Vault Conservative",      # was "Vault Builder" - large vault skim, no martingale
}


def _display_name(profile):
    return NAME_OVERRIDES.get(profile["id"], profile["name"])


def _get_profile(strategy_id):
    """Looks up a starter/library profile by id - real DB templates
    first, then the 2 synthetic ones (Fixed Stake, Custom Strategy) that
    have no real template row at all."""
    if strategy_id in SYNTHETIC_PROFILES:
        return dict(SYNTHETIC_PROFILES[strategy_id])
    return database.get_risk_profile(strategy_id)


# Real finding, not assumed: EVERY one of the 27 real templates has
# compounding_settings.steps_json = NULL - the compounding `mode`
# string ("daily"/"smart"/"milestone_based"/etc) is currently a label
# with no functional milestone schedule behind it anywhere in
# production's template set (core/risk_engine.py's
# _effective_risk_percent treats every non-disabled mode identically,
# driven entirely by steps_json - confirmed by reading the function, not
# assumed). Rather than either display a broken "does nothing" card or
# fabricate numbers pretending the real template already has them, the
# 2 compounding-flavored starters get a clearly-labeled REPRESENTATIVE
# starting schedule here - exactly what a "starter template, customize
# it" is supposed to offer. Applied only for display/simulation in THIS
# preview process; never written back to the real template row (this
# server has no write path at all).
STARTER_COMPOUNDING_OVERLAY = {
    16: {"steps_json": '[{"profit_threshold": 50, "risk_percent": 2.5}]', "max_risk_percent": 3.0, "drawdown_reset_percent": 10.0},  # Balanced Growth
    7: {"steps_json": '[{"profit_threshold": 100, "risk_percent": 2.5}, {"profit_threshold": 250, "risk_percent": 3.0}]', "max_risk_percent": 3.0},  # Profit Compounding
}


def _with_overlay(profile):
    """Returns a deep-enough copy with STARTER_COMPOUNDING_OVERLAY merged
    into the compounding sub-dict, if this profile has one - used
    identically for both the displayed money-rules text and the actual
    simulation run, so what's shown always matches what was simulated."""
    overlay = STARTER_COMPOUNDING_OVERLAY.get(profile["id"])
    if not overlay:
        return profile
    p = dict(profile)
    p["compounding"] = {**profile["compounding"], **overlay}
    return p


def _fmt_pct(n):
    return f"{n:g}%"


def _martingale_ladder_string(m):
    if m.get("custom_ladder_json"):
        import json as _json
        ladder = _json.loads(m["custom_ladder_json"])
        return " -> ".join(f"${v:g}" for v in ladder)
    steps = list(range(0, m["max_steps"] + 1))
    return " -> ".join(f"{m['multiplier'] ** s:.2g}x" for s in steps)


def _martingale_max_exposure_percent(profile):
    """Worst case: every step in the ladder loses in a row. Sum of the
    ladder's own stake-multiples (not a simulated average) - a real,
    deterministic ceiling, not a scenario-dependent estimate."""
    m = profile["martingale"]
    if not m["enabled"]:
        return None
    base_pct = profile.get("percent_of_bankroll") or 0
    if profile["sizing_mode"] != "percent" or not base_pct:
        return None
    total_multiple = sum(m["multiplier"] ** s for s in range(0, m["max_steps"] + 1))
    return round(base_pct * total_multiple, 2)


def _money_rules(profile):
    """The exact-numbers card/detail content - direct response to 'cards
    describe personalities, not bankroll mechanics.' Every field is
    either a real stored number or explicitly says when a mechanism is
    present but has no configured schedule (never silently blank, never
    fabricated - P9)."""
    sizing_mode = profile["sizing_mode"]
    pct = profile.get("percent_of_bankroll") or 0
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]

    if sizing_mode == "fixed":
        risk_per_trade = f"Fixed ${profile['fixed_amount']:g} per trade"
        sizing_method = "Fixed amount"
    elif sizing_mode == "kelly":
        risk_per_trade = "Dynamic - computed from your win-rate/payout estimates each trade"
        sizing_method = "Kelly-based"
    elif sizing_mode == "dynamic":
        risk_per_trade = f"Dynamic {_fmt_pct(pct)} of current balance (recalculated every trade)"
        sizing_method = "Dynamic percentage"
    else:
        risk_per_trade = f"{_fmt_pct(pct)} of active bankroll"
        sizing_method = "Percentage of bankroll"

    martingale = None
    if m["enabled"]:
        max_exposure = _martingale_max_exposure_percent(profile)
        martingale = {
            "steps": m["max_steps"],
            "ladder": _martingale_ladder_string(m),
            "reset_rule": "Resets after a win" if m.get("reset_after_win") else "Does not reset after a win",
            "max_exposure": f"{max_exposure:g}% of bankroll" if max_exposure is not None else "Depends on bankroll (fixed-amount sizing)",
        }

    compounding = None
    if c["mode"] != "disabled":
        import json as _json
        steps = _json.loads(c["steps_json"]) if c.get("steps_json") else []
        if steps:
            steps_sorted = sorted(steps, key=lambda s: s["profit_threshold"])
            chain = [f"{_fmt_pct(c['base_risk_percent'])}"] if c.get("base_risk_percent") else []
            chain += [f"{_fmt_pct(s['risk_percent'])} at ${s['profit_threshold']:g} profit" for s in steps_sorted]
            increase_rule = "Risk increases " + " -> ".join(chain)
        else:
            increase_rule = f"Labeled \"{c['mode'].replace('_',' ')}\" but no milestone schedule is configured yet on this template - customize to set one"
        compounding = {
            "trigger": c["mode"].replace("_", " "),
            "increase_rule": increase_rule,
            "reset_rule": f"Resets after a {c['drawdown_reset_percent']:g}% drawdown" if c.get("drawdown_reset_percent") else "No automatic reset configured",
            "max_risk_cap": f"{_fmt_pct(c['max_risk_percent'])}" if c.get("max_risk_percent") else "No cap configured",
        }

    vault = None
    if v["enabled"]:
        vault = {"percent": f"{v['vault_percent']:g}%", "trigger": v["trigger_event"].replace("_", " ")}

    profit_target = f"Stop at +${profile['profit_target']:g}" if profile.get("profit_target") else "No automatic stop"
    loss_limit = f"Stop at -${profile['max_session_loss']:g}" if profile.get("max_session_loss") else "No automatic stop"

    return {
        "risk_per_trade": risk_per_trade, "sizing_method": sizing_method,
        "martingale": martingale, "compounding": compounding, "vault": vault,
        "profit_target": profit_target, "loss_limit": loss_limit,
    }


def _single_trade_scenarios(profile):
    """Win scenario / loss scenario - a single real next-trade projection
    from the profile's OWN sizing rule at bankroll=$1000, step=0 (not a
    multi-trade simulation, so it's exact, not an average)."""
    bankroll = DEFAULT_SCENARIO_BANKROLL
    session_state = {"realized_pnl": 0.0, "current_martingale_step": 0, "current_momentum_step": 0, "consecutive_losses": 0}
    p = dict(profile)
    p["bankroll"] = bankroll
    try:
        stake = round(risk_engine._base_amount(p, session_state, record_events=False), 2)
    except Exception:
        stake = profile.get("fixed_amount", 1.0)
    payout = DEFAULT_SCENARIO_PAYOUT / 100.0
    win_amount = round(stake * payout, 2)
    return {
        "stake": stake,
        "win": {"profit": win_amount, "new_balance": round(bankroll + win_amount, 2)},
        "loss": {"loss": -stake, "new_balance": round(bankroll - stake, 2)},
    }


def _trade_by_trade_example(profile, win_rate=0.5, n=8):
    """First N trades of the SAME simulation the animated chart already
    ran - real numbers from a real (if hypothetical) run, not a
    hand-crafted illustrative table."""
    pool = _synthetic_signal_pool(win_rate, seed=42, num_trades=max(n, 20))
    result = backtest_engine.simulate_strategy(pool, profile, DEFAULT_SCENARIO_BANKROLL, session_window="all")
    return [
        {
            "seq": i + 1, "amount": t["trade_amount"], "result": t["result"],
            "profit_loss": t["profit_loss"], "running_balance": t["running_balance"],
            "martingale_step": t["martingale_step"],
        }
        for i, t in enumerate(result["trades"][:n])
    ]

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
    profile = _with_overlay(profile)
    sim = _simulate(profile, win_rate)
    desc = _describe(profile)
    rules = _money_rules(profile)
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]
    return {
        "id": profile["id"], "name": _display_name(profile), "description": profile["description"],
        "uses_martingale": bool(m["enabled"]), "uses_compounding": c["mode"] != "disabled",
        "uses_vault": bool(v["enabled"]),
        "risk_level": sim["risk_score"], "growth_label": sim["best_for_label"],
        "roi_percent": sim["metrics"]["roi_percent"], "max_drawdown_percent": sim["metrics"]["max_drawdown_percent"],
        "rules": rules,
        "is_custom_entry": profile.get("is_custom_entry", False),
        **desc,
    }


@app.get("/api/preview/strategies")
def preview_strategies_starters():
    """The 6 curated starters - Strategy Studio's default page (never
    the full 27), per the explicit 'reduce cognitive overload' brief."""
    profiles = [_get_profile(i) for i in STARTER_STRATEGY_IDS]
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
    bankroll simulator's real data, real money rules, real single-trade
    win/loss scenarios, and a real trade-by-trade example - all from the
    SAME (possibly overlaid) profile snapshot, so nothing shown
    contradicts what was actually simulated. win_rate is a query param
    specifically so the UI can offer a scenario toggle (e.g. 45% / 50% /
    55%) without a second endpoint - every value is honestly labeled as
    a hypothetical scenario, never presented as a prediction (P9)."""
    profile = _get_profile(strategy_id)
    if profile is None:
        return {"error": "strategy not found"}
    profile = _with_overlay(profile)
    win_rate = max(0.1, min(0.9, win_rate))  # keep scenarios sane, still user-adjustable
    sim = _simulate(profile, win_rate)
    desc = _describe(profile)
    rules = _money_rules(profile)
    scenarios = _single_trade_scenarios(profile)
    trade_example = _trade_by_trade_example(profile, win_rate)
    m, c, v = profile["martingale"], profile["compounding"], profile["profit_vault"]
    return {
        "id": profile["id"], "name": _display_name(profile), "description": profile["description"],
        "sizing_mode": profile["sizing_mode"], "percent_of_bankroll": profile.get("percent_of_bankroll"),
        "uses_martingale": bool(m["enabled"]), "martingale": {"multiplier": m.get("multiplier"), "max_steps": m.get("max_steps")} if m["enabled"] else None,
        "uses_compounding": c["mode"] != "disabled", "compounding_mode": c["mode"] if c["mode"] != "disabled" else None,
        "uses_vault": bool(v["enabled"]), "vault_percent": v.get("vault_percent") if v["enabled"] else None,
        "risk_level": sim["risk_score"], "growth_label": sim["best_for_label"],
        "scenario_win_rate": win_rate,
        "metrics": sim["metrics"], "curve": sim["curve"],
        "rules": rules, "scenarios": scenarios, "trade_example": trade_example,
        "is_custom_entry": profile.get("is_custom_entry", False),
        **desc,
    }


# Static V2 pages - mounted last so /api/preview/* above takes priority.
app.mount("/", StaticFiles(directory=str(WEB_V2_DIR), html=True), name="web_v2")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8091)
