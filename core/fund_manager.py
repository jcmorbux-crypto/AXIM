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
    the fund row, computed balances, performance, attached broker
    account, and recent session/backtest history (capped at a UI-
    reasonable page size, unlike the _ALL-scoped aggregation above)."""
    fund = database.get_fund(fund_id)
    if fund is None:
        return None
    return {
        "fund": fund,
        "balances": get_fund_balances(fund_id),
        "performance": get_fund_performance(fund_id),
        "sources": database.list_fund_source_channel_ids(fund_id),
        "broker_account": database.get_fund_primary_broker_account(fund_id),
        "recent_sessions": database.list_fund_sessions(fund_id, limit=20),
        "recent_backtests": database.list_fund_backtest_runs(fund_id, limit=20),
    }


def list_funds_with_balances(status=None):
    """Powers the Funds page list view and Mission Control's fund
    selector - every fund plus its current computed balance and attached
    broker account, in one pass, without the caller needing a request
    per fund."""
    funds = database.list_funds(status=status)
    result = []
    for fund in funds:
        balances = get_fund_balances(fund["id"])
        broker_account = database.get_fund_primary_broker_account(fund["id"])
        result.append({**fund, "balances": balances, "broker_account": broker_account})
    return result


class FundLimitReached(Exception):
    """Same (rule, reason) shape as session_manager.SessionLimitReached -
    core/session_manager.py's _on_trade_closed handles either
    identically."""

    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def check_fund_limits(fund_id):
    """A Fund's own profit_target/loss_limit/max_trades are a LIFETIME
    circuit breaker on that Fund's bankroll, distinct from any individual
    session's own (resettable, per-run) limits - a Fund is a persistent
    portfolio, not a disposable session (docs/AXIM_APP_PLAN.md). Measured
    against get_fund_performance's cumulative realized P/L and trade
    count across every session the fund has ever run, not "since
    midnight" or "this session only".

    On breach: pauses the Fund (blocks future session-starts via
    can_trade, and blocks its current session's future signals via
    core/telegram_listener.py's per-fund pause check) AND ends its
    currently active session, if any - the same two real, already-wired
    primitives Automation Studio's pause_fund/stop_active_session actions
    use, not a new enforcement path. No-op if the fund has no limits set
    (all zero) or doesn't exist."""
    fund = database.get_fund(fund_id)
    if fund is None or fund["status"] != "active":
        return
    performance = get_fund_performance(fund_id)
    realized_pnl = performance["profit_loss"]
    total_closed = performance["total_closed"]

    breach = None
    if fund["profit_target"] > 0 and realized_pnl >= fund["profit_target"]:
        breach = ("fund_profit_target",
                   f"fund {fund_id} reached its lifetime profit target ${fund['profit_target']:.2f} "
                   f"(realized ${realized_pnl:.2f}) - fund paused")
    elif fund["loss_limit"] > 0 and realized_pnl <= -fund["loss_limit"]:
        breach = ("fund_loss_limit",
                   f"fund {fund_id} breached its lifetime loss limit ${fund['loss_limit']:.2f} "
                   f"(realized ${realized_pnl:.2f}) - fund paused")
    elif fund["max_trades"] > 0 and total_closed >= fund["max_trades"]:
        breach = ("fund_max_trades",
                   f"fund {fund_id} reached its lifetime max trades {fund['max_trades']} "
                   f"({total_closed} closed) - fund paused")

    if breach is None:
        return

    rule, reason = breach
    import session_manager
    active_session = database.get_active_trading_session_for_fund(fund_id)
    if active_session is not None:
        stop_status = {"fund_profit_target": "stopped_fund_target",
                        "fund_loss_limit": "stopped_fund_loss_limit",
                        "fund_max_trades": "stopped_fund_max_trades"}[rule]
        session_manager.end_session(active_session["id"], stop_status, reason)
    database.update_fund(fund_id, status="paused")
    raise FundLimitReached(rule, reason)


def can_trade(fund_id):
    """The safety gate docs/AXIM_APP_PLAN.md requires before a session can
    even start: a fund needs a connected broker account, period - Live
    trading additionally needs BOTH the fund's own live_enabled AND the
    attached account's live_enabled (two independent switches, neither
    sufficient alone, matching the explicit "separately enabled at the
    Fund level and Broker Account level" requirement). Returns
    (allowed: bool, reason: str | None, can_go_live: bool) rather than
    raising, so callers (the session-start endpoint, the UI's pre-start
    summary) can show a clear reason instead of a bare 4xx."""
    fund = database.get_fund(fund_id)
    if fund is None:
        return False, "fund not found", False
    if fund["status"] == "paused":
        return False, "this Fund is paused - resume it before starting a session", False
    if fund["status"] == "archived":
        return False, "this Fund is archived", False

    account = database.get_fund_primary_broker_account(fund_id)
    if account is None:
        return False, "Broker account not connected. Connect this Fund to a Pocket Option account before starting a session.", False
    if account["status"] != "active":
        return False, f"the attached broker account ({account['name']}) is {account['status']}, not active", False
    if account["connection_status"] != "connected":
        return False, f"the attached broker account ({account['name']}) is not connected - reconnect it before starting a session", False

    can_go_live = bool(fund["live_enabled"]) and bool(account["live_enabled"]) and account["mode"] in ("live", "both")

    # Pessimistic pre-check for the fund's own LIFETIME loss_limit/
    # max_trades - check_fund_limits below still owns the actual pause+
    # stop once a trade closes and confirms a real breach; this is an
    # ADDITIONAL, non-mutating proactive gate. Unlike session-scoped
    # limits, check_fund_limits only ever ran reactively (after a trade
    # closes) - there was no proactive check here at all, so a burst of
    # signals arriving within one expiry window could all pass it before
    # any of them resolve (execution/pocket_executor.py's track_outcome
    # docstring: MAX_CONCURRENT_WORKERS bounds placements, not open
    # positions). Read-only: never pauses the fund or ends a session -
    # only the reactive check below does that.
    if fund["loss_limit"] > 0 or fund["max_trades"] > 0:
        performance = get_fund_performance(fund_id)
        if fund["loss_limit"] > 0:
            pending_stake = database.get_fund_pending_stake(fund_id)
            effective_pnl = performance["profit_loss"] - pending_stake
            if effective_pnl <= -fund["loss_limit"]:
                return False, (
                    f"fund's lifetime loss limit ${fund['loss_limit']:.2f} would be breached if "
                    f"${pending_stake:.2f} currently at risk in open trades all lose"
                ), False
        if fund["max_trades"] > 0:
            pending_count = database.count_fund_pending_trades(fund_id)
            if performance["total_closed"] + pending_count >= fund["max_trades"]:
                return False, (
                    f"fund's lifetime max trades {fund['max_trades']} would be reached by "
                    f"{pending_count} currently open trade(s)"
                ), False

    return True, None, can_go_live
