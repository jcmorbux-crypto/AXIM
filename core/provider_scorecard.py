"""AXIM Provider Scorecard - real, measurable per-provider (signal
channel) statistics, the sole input Blackwater (tiered allocation) and
Sniper (execution gate) are allowed to use (2026-08-01 product decision:
"every decision must be driven by measurable trading statistics already
collected by AXIM, never fabricated confidence values or arbitrary
scoring systems" - see core/capital_strategies.py's blackwater_deployment/
sniper_qualifies and core/capital_strategies_catalog.py's Leviathan/
Oracle "definition_required" entries for the contrast: those two would
need an invented signal-quality judgment this module deliberately never
makes).

Every field is derived directly from core/database.py's real trade
history for that channel_id - no external data, no AI scoring."""
import statistics as _statistics
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database

ROLLING_WINDOWS = (25, 50, 100)


def _window_metrics(trades):
    """Pure. win_rate/profit_factor/expected_value over exactly the
    trades handed in - the caller decides which slice (lifetime, a
    rolling window, one session)."""
    n = len(trades)
    if n == 0:
        return {"sample_size": 0, "win_rate": None, "profit_factor": None, "expected_value": None}
    wins = sum(1 for t in trades if t["result"] == "win")
    gross_profit = sum(t["profit_loss"] for t in trades if t["profit_loss"] and t["profit_loss"] > 0)
    gross_loss = -sum(t["profit_loss"] for t in trades if t["profit_loss"] and t["profit_loss"] < 0)
    total_pl = sum((t["profit_loss"] or 0) for t in trades)
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    else:
        # No losing trades in this slice - not "infinitely good", just
        # not yet meaningfully measurable. None (not float('inf')) so
        # every downstream threshold comparison ("profit_factor >= X")
        # fails closed rather than a stray inf silently passing every
        # possible configured threshold.
        profit_factor = None
    return {
        "sample_size": n,
        "win_rate": round(100.0 * wins / n, 2),
        "profit_factor": profit_factor,
        "expected_value": round(total_pl / n, 4),
    }


def _max_drawdown_percent(trades):
    """Pure. Peak-to-trough on the cumulative P&L curve - same approach
    core/trade_statistics.py's global max_drawdown already uses, applied
    to one channel's own trades instead of every trade. Providers don't
    have their own bankroll (only Funds do), so this is relative to the
    provider's own cumulative P&L, not a real dollar bankroll."""
    if not trades:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += (t["profit_loss"] or 0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return round(max_dd, 2)


def _current_streak(trades):
    """Pure. Positive = current win-streak length, negative = current
    loss-streak length, 0 = no trades yet or the most recent trade was a
    draw (draws don't extend or break a streak either way)."""
    streak = 0
    for t in reversed(trades):
        if t["result"] == "win":
            if streak < 0:
                break
            streak += 1
        elif t["result"] == "loss":
            if streak > 0:
                break
            streak -= 1
        else:
            break
    return streak


def _avg_signal_age_seconds(trades):
    """Pure. Mean received_at -> opened_at latency, in seconds, over
    trades that have both timestamps - the real per-provider execution
    latency, distinct from core/trade_coordinator.py's live per-signal
    MAX_SIGNAL_AGE staleness check (that gates ONE signal against a
    ceiling; this reports the historical average for a provider)."""
    ages = []
    for t in trades:
        if t.get("received_at") and t.get("opened_at"):
            try:
                received = datetime.fromisoformat(t["received_at"])
                opened = datetime.fromisoformat(t["opened_at"])
            except ValueError:
                continue
            ages.append((opened - received).total_seconds())
    if not ages:
        return None
    return round(_statistics.mean(ages), 2)


def scorecard_from_trades(channel_id, trades, session_id=None, signal_rejection_rate=None):
    """The pure core of get_scorecard below, split out so core/
    backtest_engine.py can compute a genuine WALK-FORWARD scorecard from
    its own in-memory, already-replayed trades (only signals earlier in
    the same simulated timeline) instead of this live function's real DB
    query - querying the live "signals" table from inside a backtest
    replay would leak the provider's CURRENT/FUTURE real performance into
    a historical simulation (lookahead bias), silently making Blackwater/
    Sniper look better in a backtest than they could have actually
    performed at the time. Each trade dict needs the same shape
    database.get_channel_closed_trades returns: "result", "profit_loss",
    "payout" required; "received_at"/"opened_at"/"session_id" optional
    (missing timestamps just mean avg_signal_age_seconds comes back
    None - honest, not fabricated, exactly like a live trade missing
    those fields already behaves)."""
    lifetime = _window_metrics(trades)

    rolling = {str(n): _window_metrics(trades[-n:]) for n in ROLLING_WINDOWS}

    session_stats = None
    if session_id is not None:
        session_trades = [t for t in trades if t.get("session_id") == session_id]
        session_stats = _window_metrics(session_trades)

    payouts = [t["payout"] for t in trades if t.get("payout")]

    return {
        "channel_id": channel_id,
        "sample_size": lifetime["sample_size"],
        "win_rate": lifetime["win_rate"],
        "avg_payout_percent": round(_statistics.mean(payouts), 2) if payouts else None,
        "profit_factor": lifetime["profit_factor"],
        "expected_value": lifetime["expected_value"],
        "max_drawdown_percent": _max_drawdown_percent(trades),
        "current_streak": _current_streak(trades),
        "avg_signal_age_seconds": _avg_signal_age_seconds(trades),
        "signal_rejection_rate": signal_rejection_rate,
        "rolling": rolling,
        "session": session_stats,
    }


def get_scorecard(channel_id, session_id=None):
    """Real, measurable stats for one provider/channel, live from the
    real DB - see module docstring. session_id is optional; when given,
    adds a "session" slice scoped to that specific trading session (in
    addition to the provider's own full lifetime numbers, which are
    never session-scoped). core/backtest_engine.py must NOT call this -
    see scorecard_from_trades above for why."""
    trades = database.get_channel_closed_trades(channel_id)
    counts = database.get_channel_signal_counts(channel_id)
    rejection_rate = round(100.0 * counts["rejected"] / counts["total"], 2) if counts["total"] else None
    return scorecard_from_trades(channel_id, trades, session_id=session_id, signal_rejection_rate=rejection_rate)
