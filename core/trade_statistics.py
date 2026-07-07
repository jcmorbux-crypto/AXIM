import sys
from datetime import datetime, timedelta
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database


def _summarize(rows):
    closed = [r for r in rows if r["result"] in ("win", "loss", "draw")]
    wins = [r for r in closed if r["result"] == "win"]
    losses = [r for r in closed if r["result"] == "loss"]
    draws = [r for r in closed if r["result"] == "draw"]

    total_stake = sum(r["trade_amount"] or 0 for r in closed)
    total_profit_loss = sum(r["profit_loss"] or 0 for r in closed if r["profit_loss"] is not None)
    payouts = [r["payout"] for r in closed if r["payout"] is not None]

    return {
        "total_closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "draws": len(draws),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "profit_loss": total_profit_loss,
        "average_payout": (sum(payouts) / len(payouts)) if payouts else None,
        "roi": (total_profit_loss / total_stake) if total_stake else None,
    }


def daily_stats(now=None):
    now = now or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = database.get_trades_between(start.isoformat(), now.isoformat(), closed_only=True)
    return _summarize(rows)


def weekly_stats(now=None):
    now = now or datetime.now()
    start = now - timedelta(days=7)
    rows = database.get_trades_between(start.isoformat(), now.isoformat(), closed_only=True)
    return _summarize(rows)


def _consecutive(kind):
    results = database.get_recent_results(1000)
    count = 0
    for r in results:
        if r == kind:
            count += 1
        else:
            break
    return count


def consecutive_wins():
    return _consecutive("win")


def consecutive_losses():
    return _consecutive("loss")


def signals_ignored_count(since_iso=None):
    return database.count_with_result_prefix("ignored:", since_iso)


def signals_rejected_count(since_iso=None):
    return database.count_with_result_prefix("rejected:", since_iso)


def full_report():
    return {
        "daily": daily_stats(),
        "weekly": weekly_stats(),
        "consecutive_wins": consecutive_wins(),
        "consecutive_losses": consecutive_losses(),
        "signals_ignored": signals_ignored_count(),
        "signals_rejected": signals_rejected_count(),
    }


# Sentinel start date for "lifetime" windows - simpler than a separate
# unbounded query path, and no real AXIM installation predates this.
_EPOCH = "2000-01-01T00:00:00"


def monthly_stats(now=None):
    now = now or datetime.now()
    start = now - timedelta(days=30)
    return _summarize(database.get_trades_between(start.isoformat(), now.isoformat(), closed_only=True))


def yearly_stats(now=None):
    now = now or datetime.now()
    start = now - timedelta(days=365)
    return _summarize(database.get_trades_between(start.isoformat(), now.isoformat(), closed_only=True))


def lifetime_stats(now=None):
    now = now or datetime.now()
    return _summarize(database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True))


def _grouped_performance(rows, key):
    """Groups closed trades by an arbitrary key (channel/asset/session_id)
    and summarizes each group with the same win_rate/profit_loss shape
    _summarize already uses elsewhere, so every "performance by X" view
    in this module looks the same."""
    groups = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        groups.setdefault(k, []).append(r)
    return {k: _summarize(v) for k, v in groups.items()}


def profit_by_channel(now=None):
    now = now or datetime.now()
    rows = database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True)
    return _grouped_performance(rows, "channel")


def profit_by_asset(now=None):
    now = now or datetime.now()
    rows = database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True)
    return _grouped_performance(rows, "asset")


def best_worst(grouped, min_trades=1):
    """Ranks a profit_by_X() dict by profit_loss, filtering out groups
    with fewer than min_trades closed trades (a single lucky/unlucky
    trade shouldn't crown a "best"/"worst" source)."""
    eligible = {k: v for k, v in grouped.items() if v["total_closed"] >= min_trades}
    if not eligible:
        return {"best": None, "worst": None}
    ranked = sorted(eligible.items(), key=lambda kv: kv[1]["profit_loss"], reverse=True)
    best_name, best_stats = ranked[0]
    worst_name, worst_stats = ranked[-1]
    return {
        "best": {"name": best_name, **best_stats},
        "worst": {"name": worst_name, **worst_stats},
    }


def best_time_of_day(now=None):
    """Buckets closed trades by hour-of-day (0-23, local time) and ranks
    by profit_loss - same min-sample-size caution as best_worst."""
    now = now or datetime.now()
    rows = database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True)
    for r in rows:
        r["_hour"] = datetime.fromisoformat(r["closed_at"]).hour if r.get("closed_at") else None
    grouped = _grouped_performance(rows, "_hour")
    return {str(hour): stats for hour, stats in sorted(grouped.items())}


def max_drawdown(now=None):
    """Largest peak-to-trough drop in cumulative realized P&L across all
    closed trades in chronological order - the standard drawdown
    definition, not an approximation."""
    now = now or datetime.now()
    rows = database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rows:
        cumulative += r["profit_loss"] or 0
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return round(max_dd, 2)


def longest_streaks(now=None):
    """Longest win streak and longest loss streak ever, not just the
    CURRENT streak (see consecutive_wins/consecutive_losses for that)."""
    now = now or datetime.now()
    rows = database.get_trades_between(_EPOCH, now.isoformat(), closed_only=True)
    longest_win = longest_loss = current_win = current_loss = 0
    for r in rows:
        if r["result"] == "win":
            current_win += 1
            current_loss = 0
        elif r["result"] == "loss":
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
    return {"longest_win_streak": longest_win, "longest_loss_streak": longest_loss}


def session_performance(limit=20):
    """Per-session summary (trades/win-rate/P&L) for the most recent
    sessions - joins trading_sessions with its own signals by session_id,
    not the global profit_by_channel/asset grouping above."""
    sessions = database.list_trading_sessions(limit=limit)
    results = []
    for s in sessions:
        conn = database.get_connection()
        rows = conn.execute(
            "SELECT * FROM signals WHERE session_id = ? AND result IN ('win','loss','draw')", (s["id"],)
        ).fetchall()
        conn.close()
        stats = _summarize([dict(r) for r in rows])
        results.append({"session_id": s["id"], "name": s["name"], "status": s["status"],
                         "realized_pnl": s["realized_pnl"], **stats})
    return results


def martingale_and_compounding_performance():
    """Honestly-scoped summary, not a fabricated "recovery rate" - this
    codebase doesn't record the martingale step or effective compounding
    risk % at the time of each individual historical trade (see
    core/risk_engine.py's module docstring), only the session's CURRENT
    state. So this reports what's real: which sessions used a martingale/
    compounding-enabled profile, their current step/effective risk, and
    their overall P&L - not a step-by-step backtest."""
    sessions = database.list_trading_sessions(limit=100)
    martingale_sessions = []
    compounding_sessions = []
    for s in sessions:
        if s["risk_profile_id"] is None:
            continue
        profile = database.get_risk_profile(s["risk_profile_id"])
        if profile is None:
            continue
        if profile["martingale"]["enabled"]:
            martingale_sessions.append({
                "session_id": s["id"], "name": s["name"], "profile_name": profile["name"],
                "current_step": s["current_martingale_step"], "realized_pnl": s["realized_pnl"],
            })
        if profile["compounding"]["mode"] != "disabled":
            compounding_sessions.append({
                "session_id": s["id"], "name": s["name"], "profile_name": profile["name"],
                "base_risk_percent": profile["compounding"]["base_risk_percent"],
                "realized_pnl": s["realized_pnl"],
            })
    return {"martingale_sessions": martingale_sessions, "compounding_sessions": compounding_sessions}


def performance_report():
    return {
        "daily": daily_stats(),
        "weekly": weekly_stats(),
        "monthly": monthly_stats(),
        "yearly": yearly_stats(),
        "lifetime": lifetime_stats(),
        "by_channel": best_worst(profit_by_channel(), min_trades=3),
        "by_asset": best_worst(profit_by_asset(), min_trades=3),
        "best_time_of_day": best_worst(best_time_of_day(), min_trades=3),
        "max_drawdown": max_drawdown(),
        "streaks": longest_streaks(),
        "sessions": session_performance(),
        "risk_engine": martingale_and_compounding_performance(),
    }
