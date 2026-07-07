"""Session-scoped stop conditions (docs/AXIM_SESSION_ARCHITECTURE.md) -
mirrors core/risk_manager.py's shape (small pure functions raising a
typed exception) but for a single active trading_sessions row instead of
global/daily limits. Layered ALONGSIDE risk_manager's checks in
core/trade_coordinator.py, never a replacement for them.

Also owns the event_bus subscription that updates a session's realized
P&L the instant a trade's outcome is known - see register() and
_on_trade_closed below. This is the ONLY hook into the outcome-tracking
path; execution/pocket_executor.py itself is never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
import risk_engine
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class SessionLimitReached(Exception):
    """Same (rule, reason) shape as risk_manager.RiskViolation so
    trade_coordinator.py's existing _reject() helper can handle either
    without a separate code path."""

    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def get_active_session():
    return database.get_active_trading_session()


def channel_in_session(session, channel_row):
    """True if the given ui_channels row (already resolved by the caller,
    e.g. via database.find_channel) is one of this session's channels."""
    if session is None or channel_row is None:
        return False
    return channel_row["id"] in session["channel_ids"]


def end_session(session_id, status, reason=None):
    """The ONE path every session-ending call should go through (manual
    stop, emergency stop, and every check_session_limits breach below) -
    centralizes the Profit Vault's "every winning session" trigger here
    rather than duplicating it at each call site."""
    database.stop_trading_session(session_id, status, reason)
    risk_engine.on_session_ended(session_id)


def check_session_limits(session_id):
    """No-op if session_id is None (no active session covers this
    signal). Otherwise checks the three stop conditions in order and, on
    the first breach, transitions the session to stopped AND raises -
    the caller (trade_coordinator.handle_signal) treats this exactly like
    a RiskViolation: reject the current signal, same as every other
    rejected-before-execution path."""
    if session_id is None:
        return
    session = database.get_trading_session(session_id)
    if session is None or session["status"] != "active":
        return

    if session["profit_target"] > 0 and session["realized_pnl"] >= session["profit_target"]:
        end_session(session_id, "stopped_target",
                    f"realized P/L ${session['realized_pnl']:.2f} reached target ${session['profit_target']:.2f}")
        raise SessionLimitReached("session_profit_target",
                                   f"session {session_id} reached its profit target - session stopped")

    if session["loss_limit"] > 0 and session["realized_pnl"] <= -session["loss_limit"]:
        end_session(session_id, "stopped_loss_limit",
                    f"realized P/L ${session['realized_pnl']:.2f} breached loss limit ${session['loss_limit']:.2f}")
        raise SessionLimitReached("session_loss_limit",
                                   f"session {session_id} breached its loss limit - session stopped")

    if session["max_trades"] > 0 and session["trades_count"] >= session["max_trades"]:
        end_session(session_id, "stopped_max_trades",
                    f"{session['trades_count']} trades reached max {session['max_trades']}")
        raise SessionLimitReached("session_max_trades",
                                   f"session {session_id} reached its max trades - session stopped")


def record_trade_started(session_id):
    if session_id is not None:
        database.record_session_trade(session_id)


async def _on_trade_closed(payload):
    trade_id = payload.get("trade_id")
    profit_loss = payload.get("profit_loss")
    result = payload.get("result")
    if trade_id is None or profit_loss is None:
        return
    session_id = database.get_signal_session_id(trade_id)
    if session_id is None:
        return
    database.update_session_pnl(session_id, profit_loss)
    risk_engine.on_trade_closed(session_id, won=(result == "win"), profit_loss=profit_loss)
    try:
        check_session_limits(session_id)
    except SessionLimitReached as e:
        logger.info("session_manager: trade_id=%s closed session %s: %s", trade_id, session_id, e.reason)


def register(event_bus):
    """Call once at listener startup (core/telegram_listener.py)."""
    event_bus.subscribe("trade.closed", _on_trade_closed)
