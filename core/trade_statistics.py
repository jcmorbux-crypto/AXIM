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
