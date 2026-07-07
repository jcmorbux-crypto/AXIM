"""Rule Builder evaluation engine (docs/AXIM_APP_PLAN.md) - visual IF/THEN
automation. Every condition evaluator is a pure function of current
database/trade_statistics state; every action executor calls an existing
real mutation function (session_manager.end_session,
database.update_risk_profile, database.set_channel_enabled, etc.) -
this module never invents a new mutation path of its own.

Rules use edge-triggering: a rule only fires on the false->true
transition of its condition (core/database.py's last_condition_state),
not on every evaluation where the condition happens to be true. This is
what stops "daily profit >= $100" from firing again on every trade for
the rest of the day, and "3 wins in a row" from firing again at 4, 5, 6
wins - without needing bespoke cooldown logic per condition type.

Hooked into the same event_bus "trade.closed" subscription
core/session_manager.py already owns (see evaluate_all below), mirroring
how core/risk_engine.py's on_trade_closed was wired in during Phase 4.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
import trade_statistics
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


# ---------------------------------------------------------------------
# Condition evaluators - each takes (params: dict) and returns a bool.
# No side effects; safe to call as often as needed.
# ---------------------------------------------------------------------

def _cond_daily_profit_gte(params):
    threshold = float(params.get("threshold", 0))
    return trade_statistics.daily_stats()["profit_loss"] >= threshold


def _cond_daily_loss_gte(params):
    threshold = float(params.get("threshold", 0))
    return trade_statistics.daily_stats()["profit_loss"] <= -threshold


def _cond_consecutive_wins_eq(params):
    count = int(params.get("count", 0))
    return trade_statistics.consecutive_wins() == count


def _cond_consecutive_losses_eq(params):
    count = int(params.get("count", 0))
    return trade_statistics.consecutive_losses() == count


def _cond_session_profit_gte(params):
    threshold = float(params.get("threshold", 0))
    session = database.get_active_trading_session()
    if session is None:
        return False
    return session["realized_pnl"] >= threshold


def _cond_session_loss_gte(params):
    threshold = float(params.get("threshold", 0))
    session = database.get_active_trading_session()
    if session is None:
        return False
    return session["realized_pnl"] <= -threshold


def _cond_lifetime_profit_gte(params):
    """Cumulative realized P/L across all closed trades ever - the
    closest honest equivalent to a "bankroll milestone" AXIM can compute,
    since no live broker account balance is tracked anywhere else in the
    app (see docs/AXIM_APP_PLAN.md known gaps)."""
    threshold = float(params.get("threshold", 0))
    return trade_statistics.lifetime_stats()["profit_loss"] >= threshold


def _cond_source_win_rate_below(params):
    channel_id = params.get("channel_id")
    threshold = float(params.get("threshold", 0))
    min_trades = int(params.get("min_trades", 10))
    if channel_id is None:
        return False
    channel = next((c for c in database.list_channels() if c["id"] == int(channel_id)), None)
    if channel is None:
        return False
    perf = database.get_channel_performance(channel["title"])
    if perf["total_closed"] < min_trades or perf["win_rate"] is None:
        return False
    return perf["win_rate"] < threshold


CONDITION_TYPES = {
    "daily_profit_gte": {
        "label": "Daily profit is at least",
        "params": {"threshold": "number"},
        "fn": _cond_daily_profit_gte,
    },
    "daily_loss_gte": {
        "label": "Daily loss is at least",
        "params": {"threshold": "number"},
        "fn": _cond_daily_loss_gte,
    },
    "consecutive_wins_eq": {
        "label": "Consecutive wins reaches exactly",
        "params": {"count": "number"},
        "fn": _cond_consecutive_wins_eq,
    },
    "consecutive_losses_eq": {
        "label": "Consecutive losses reaches exactly",
        "params": {"count": "number"},
        "fn": _cond_consecutive_losses_eq,
    },
    "session_profit_gte": {
        "label": "Active session profit is at least",
        "params": {"threshold": "number"},
        "fn": _cond_session_profit_gte,
    },
    "session_loss_gte": {
        "label": "Active session loss is at least",
        "params": {"threshold": "number"},
        "fn": _cond_session_loss_gte,
    },
    "lifetime_profit_gte": {
        "label": "Lifetime realized profit is at least",
        "params": {"threshold": "number"},
        "fn": _cond_lifetime_profit_gte,
    },
    "source_win_rate_below": {
        "label": "A signal source's win rate drops below",
        "params": {"channel_id": "channel", "threshold": "percent", "min_trades": "number"},
        "fn": _cond_source_win_rate_below,
    },
}


# ---------------------------------------------------------------------
# Action executors - each takes (params: dict, rule_name: str) and
# returns a human-readable string describing what happened (stored in
# the lifecycle log only, not persisted on the rule row).
# ---------------------------------------------------------------------

def _act_stop_active_session(params, rule_name):
    import session_manager
    session = database.get_active_trading_session()
    if session is None:
        return "no active session to stop"
    session_manager.end_session(session["id"], "stopped_rule", f"stopped by rule '{rule_name}'")
    return f"stopped active session {session['id']}"


def _act_emergency_stop(params, rule_name):
    import session_manager
    database.set_control_state(paused=True, emergency_stop=True)
    session = database.get_active_trading_session()
    if session is not None:
        session_manager.end_session(session["id"], "stopped_emergency", f"emergency stop by rule '{rule_name}'")
    return "emergency stop engaged"


def _act_increase_risk_profile_percent(params, rule_name):
    percent_increase = float(params.get("percent_increase", 0))
    session = database.get_active_trading_session()
    if session is None or session["risk_profile_id"] is None:
        return "no active session with a risk profile attached"
    profile = database.get_risk_profile(session["risk_profile_id"])
    if profile is None:
        return "session's risk profile no longer exists"
    new_percent = profile["percent_of_bankroll"] * (1 + percent_increase / 100)
    database.update_risk_profile(profile["id"], percent_of_bankroll=new_percent)
    return f"increased profile {profile['id']} percent_of_bankroll to {new_percent:.4f}"


def _act_switch_session_risk_profile(params, rule_name):
    risk_profile_id = params.get("risk_profile_id")
    session = database.get_active_trading_session()
    if session is None or risk_profile_id is None:
        return "no active session, or no target profile given"
    database.set_session_risk_profile(session["id"], int(risk_profile_id))
    return f"switched session {session['id']} to risk profile {risk_profile_id}"


def _act_disable_channel(params, rule_name):
    channel_id = params.get("channel_id")
    if channel_id is None:
        return "no channel given"
    database.set_channel_enabled(int(channel_id), False)
    return f"disabled channel {channel_id}"


ACTION_TYPES = {
    "stop_active_session": {
        "label": "Stop the active trading session",
        "params": {},
        "fn": _act_stop_active_session,
    },
    "emergency_stop": {
        "label": "Trigger a full emergency stop",
        "params": {},
        "fn": _act_emergency_stop,
    },
    "increase_risk_profile_percent": {
        "label": "Increase active session's risk profile size by",
        "params": {"percent_increase": "percent"},
        "fn": _act_increase_risk_profile_percent,
    },
    "switch_session_risk_profile": {
        "label": "Switch active session to risk profile",
        "params": {"risk_profile_id": "risk_profile"},
        "fn": _act_switch_session_risk_profile,
    },
    "disable_channel": {
        "label": "Disable signal source",
        "params": {"channel_id": "channel"},
        "fn": _act_disable_channel,
    },
}


def evaluate_rule(rule):
    """Evaluates one rule row (as returned by database.get_rule/list_rules)
    and fires its action on a false->true edge. Always records the new
    condition_state via database.record_rule_evaluation, whether or not
    it fired. Returns True if the action fired."""
    condition = CONDITION_TYPES.get(rule["condition_type"])
    action = ACTION_TYPES.get(rule["action_type"])
    if condition is None or action is None:
        logger.error("rule_engine: rule %s has unknown condition/action type", rule["id"])
        return False

    try:
        condition_now = bool(condition["fn"](rule["condition_params"]))
    except Exception:
        logger.exception("rule_engine: condition evaluation failed for rule %s", rule["id"])
        return False

    fired = condition_now and not rule["last_condition_state"]
    database.record_rule_evaluation(rule["id"], condition_now, fired)

    if fired:
        try:
            outcome = action["fn"](rule["action_params"], rule["name"])
            logger.info("rule_engine: rule '%s' (id=%s) fired: %s", rule["name"], rule["id"], outcome)
        except Exception:
            logger.exception("rule_engine: action execution failed for rule %s", rule["id"])
    return fired


def evaluate_all():
    """Evaluates every enabled rule. Called once per trade close from
    core/session_manager.py's event_bus subscription."""
    fired_any = False
    for rule in database.list_rules():
        if not rule["enabled"]:
            continue
        if evaluate_rule(rule):
            fired_any = True
    return fired_any
