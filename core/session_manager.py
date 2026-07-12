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
import asyncio
import sys
import time
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR.parent / "config"))

import database
import risk_engine
import rule_engine
from logger import get_logger
from settings import TRADE_CONFIRMATION_TIMEOUT_SECONDS

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class SessionLimitReached(Exception):
    """Same (rule, reason) shape as risk_manager.RiskViolation so
    trade_coordinator.py's existing _reject() helper can handle either
    without a separate code path."""

    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


class TradeNotConfirmed(Exception):
    """Same (rule, reason) shape as SessionLimitReached/RiskViolation -
    raised by wait_for_trade_confirmation below on an explicit reject or
    a confirmation timeout, so trade_coordinator.py's existing _reject()
    helper handles this exactly like every other pre-execution gate."""

    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def get_active_session():
    """Newest active session app-wide - kept for callers that genuinely
    want the single-session fallback (e.g. a rule with no fund_id yet).
    Signal routing should use get_active_session_for_channel instead,
    since more than one session can be active at once."""
    return database.get_active_trading_session()


def get_active_session_for_channel(channel_row):
    """The active session (if any) that actually covers this channel -
    the correct scoping now that different Funds can each have their own
    concurrently active session. Different from get_active_session,
    which would only ever see the most-recently-started one."""
    if channel_row is None:
        return None
    return database.get_active_trading_session_for_channel(channel_row["id"])


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
    rather than duplicating it at each call site. Also clears out any
    session-scoped rules (Rule Builder "this session only" overrides) -
    they're temporary by design and must not outlive the session."""
    database.stop_trading_session(session_id, status, reason)
    risk_engine.on_session_ended(session_id)
    database.delete_session_rules(session_id)


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

    if session["loss_limit"] > 0:
        # Pessimistic: every currently-open (placed, unresolved) trade is
        # treated as a worst-case loss of its full stake - see
        # database.get_session_pending_stake's docstring for why closed-
        # only realized_pnl alone isn't enough to enforce this promise
        # under a burst of signals.
        pending_stake = database.get_session_pending_stake(session_id)
        effective_pnl = session["realized_pnl"] - pending_stake
        if effective_pnl <= -session["loss_limit"]:
            end_session(session_id, "stopped_loss_limit",
                        f"realized P/L ${session['realized_pnl']:.2f} with ${pending_stake:.2f} at risk in "
                        f"open trades would breach loss limit ${session['loss_limit']:.2f} if they all lose")
            raise SessionLimitReached("session_loss_limit",
                                       f"session {session_id} breached its loss limit - session stopped")

    if session["max_trades"] > 0:
        pending_count = database.count_session_pending_trades(session_id)
        if session["trades_count"] + pending_count >= session["max_trades"]:
            end_session(session_id, "stopped_max_trades",
                        f"{session['trades_count']} closed + {pending_count} open trades reached max {session['max_trades']}")
            raise SessionLimitReached("session_max_trades",
                                       f"session {session_id} reached its max trades - session stopped")


def record_trade_started(session_id):
    if session_id is not None:
        database.record_session_trade(session_id)


async def wait_for_trade_confirmation(trade_id, session_id, asset, direction, expiry, amount):
    """Gate for a session's require_confirmation setting - a no-op unless
    the session BOTH has it enabled AND is actually in LIVE mode (the
    checkbox is explicitly labeled "in Live mode" - a Demo session with
    it checked never gates, matching the UI's own copy). Writes a
    pending_trade_confirmations row and polls for an operator's
    Confirm/Reject from any page (web/shell.js) up to
    TRADE_CONFIRMATION_TIMEOUT_SECONDS.

    Fails closed: an explicit reject OR silence until the timeout both
    raise TradeNotConfirmed - there is no path where an un-answered
    Live-mode trade proceeds anyway."""
    if session_id is None:
        return
    session = database.get_trading_session(session_id)
    if session is None or not session["require_confirmation"] or session["account_mode"] != "LIVE":
        return

    database.create_pending_trade_confirmation(trade_id, session_id, asset, direction, expiry, amount)
    logger.info("session_manager: trade_id=%s awaiting Live-mode confirmation (timeout=%ss)",
                trade_id, TRADE_CONFIRMATION_TIMEOUT_SECONDS)

    deadline = time.monotonic() + TRADE_CONFIRMATION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        row = database.get_pending_trade_confirmation(trade_id)
        if row is None:
            # Should not happen - the row we just created is gone. Fail
            # closed rather than guess what happened.
            raise TradeNotConfirmed("trade_not_confirmed", "confirmation record disappeared")
        if row["status"] == "confirmed":
            logger.info("session_manager: trade_id=%s confirmed by %s", trade_id, row["decided_by"])
            return
        if row["status"] == "rejected":
            reason = f"rejected by {row['decided_by']}" if row["decided_by"] else "rejected"
            raise TradeNotConfirmed("trade_not_confirmed", reason)
        await asyncio.sleep(0.5)

    database.expire_trade_confirmation(trade_id)
    raise TradeNotConfirmed(
        "trade_not_confirmed",
        f"no confirmation within {TRADE_CONFIRMATION_TIMEOUT_SECONDS}s - trade rejected",
    )


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

    session = database.get_trading_session(session_id)
    if session is not None and session["fund_id"] is not None:
        import fund_manager
        try:
            fund_manager.check_fund_limits(session["fund_id"])
        except fund_manager.FundLimitReached as e:
            logger.info("session_manager: trade_id=%s closed fund %s: %s", trade_id, session["fund_id"], e.reason)

    rule_engine.evaluate_all()


def register(event_bus):
    """Call once at listener startup (core/telegram_listener.py)."""
    event_bus.subscribe("trade.closed", _on_trade_closed)
