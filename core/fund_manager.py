"""Fund / Portfolio manager (docs/AXIM_APP_PLAN.md) - balance and
performance aggregation for a fund, computed from its real sessions and
trades rather than a stored, separately-maintained running total (the
same compute-don't-cache approach core/backtest_engine.py already uses
for simulated balances) - a fund's numbers can never drift out of sync
with its actual trade history.

core/database.py owns schema/CRUD; this module is where the actual
"what does this fund's balance/performance look like" logic lives,
mirroring how core/trade_statistics.py sits alongside core/database.py
for the same reason. Reuses trade_statistics._summarize() rather than
re-implementing win-rate/P&L aggregation a second time.
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database
import trade_statistics

# Aggregate queries need every session/run a fund has ever had, not just
# the most recent page - list_fund_sessions/list_fund_backtest_runs
# default to a UI-sized limit, so aggregation callers pass this instead.
_ALL = 1_000_000


def get_fund_balances(fund_id):
    """{starting_balance, trading_balance, protected_balance,
    total_account_value}. trading_balance excludes vaulted funds (they
    are protected, not available for future position sizing) -
    total_account_value is trading + protected combined, so vaulting
    never destroys value, only reallocates it - same accounting as
    core/backtest_engine.py's simulation."""
    fund = database.get_fund(fund_id)
    if fund is None:
        return None
    sessions = database.list_fund_sessions(fund_id, limit=_ALL)
    total_realized_pnl = sum(s["realized_pnl"] or 0 for s in sessions)
    total_vaulted = sum(s["vaulted_amount"] or 0 for s in sessions)
    trading_balance = fund["starting_balance"] + total_realized_pnl - total_vaulted
    return {
        "starting_balance": fund["starting_balance"],
        "trading_balance": round(trading_balance, 2),
        "protected_balance": round(total_vaulted, 2),
        "total_account_value": round(fund["starting_balance"] + total_realized_pnl, 2),
    }


def _fund_trades(fund_id):
    session_ids = [s["id"] for s in database.list_fund_sessions(fund_id, limit=_ALL)]
    if not session_ids:
        return []
    conn = database.get_connection()
    placeholders = ", ".join("?" for _ in session_ids)
    rows = conn.execute(f"""
        SELECT * FROM signals
        WHERE session_id IN ({placeholders}) AND result IN ('win', 'loss', 'draw')
        ORDER BY closed_at ASC
    """, session_ids).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_fund_performance(fund_id):
    """Win rate / P&L / ROI across every trade the fund's sessions have
    ever recorded - the same summary shape trade_statistics.py already
    uses everywhere else, scoped to this fund."""
    trades = _fund_trades(fund_id)
    return trade_statistics._summarize(trades)


def get_fund_report(fund_id):
    """Everything the Funds page needs for one fund in a single call:
    the fund row, computed balances, performance, and recent session/
    backtest history (capped at a UI-reasonable page size, unlike the
    _ALL-scoped aggregation above)."""
    fund = database.get_fund(fund_id)
    if fund is None:
        return None
    return {
        "fund": fund,
        "balances": get_fund_balances(fund_id),
        "performance": get_fund_performance(fund_id),
        "sources": database.list_fund_source_channel_ids(fund_id),
        "recent_sessions": database.list_fund_sessions(fund_id, limit=20),
        "recent_backtests": database.list_fund_backtest_runs(fund_id, limit=20),
    }


def list_funds_with_balances(status=None):
    """Powers the Funds page list view and Mission Control's fund
    selector - every fund plus its current computed balance, in one
    pass, without the caller needing a request per fund."""
    funds = database.list_funds(status=status)
    result = []
    for fund in funds:
        balances = get_fund_balances(fund["id"])
        result.append({**fund, "balances": balances})
    return result
