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

Rules belong to a Fund (rule["fund_id"]), not a session - a Fund is the
permanent trading object, sessions are disposable. Every condition/action
that needs "the" session resolves it via _resolve_session_for_rule below:
a scope='fund' rule uses whichever session is CURRENTLY active for that
fund (or none); a scope='session' rule is a temporary override tied to
one specific trading_sessions row, cleaned up by
session_manager.end_session when that session ends. This is what makes
these evaluators safe now that different Funds can each have their own
concurrently active session - "the active session" is never resolved
globally except as a legacy fallback for fund_id-less rules.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
import trade_statistics
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def _resolve_session_for_rule(rule):
    """The trading_sessions row this rule's session-scoped conditions/
    actions should act on. scope='session' rules are pinned to one
    specific session (only while it's still active). scope='fund' rules
    (the common case) follow whichever session is currently active for
    that fund - there may be none, and that's a normal, common state, not
    an error. Rules with no fund_id at all (legacy, pre-dating the
    Fund-owned rules redesign) fall back to the old app-wide newest-
    active-session lookup."""
    if rule.get("scope") == "session" and rule.get("session_id") is not None:
        session = database.get_trading_session(rule["session_id"])
        return session if session and session["status"] == "active" else None
    if rule.get("fund_id") is not None:
        return database.get_active_trading_session_for_fund(rule["fund_id"])
    return database.get_active_trading_session()


# ---------------------------------------------------------------------
# Condition evaluators - each takes (params: dict, rule: dict) and
# returns a bool. No side effects; safe to call as often as needed.
# ---------------------------------------------------------------------

def _cond_daily_profit_gte(params, rule):
    threshold = float(params.get("threshold", 0))
    return trade_statistics.daily_stats(fund_id=rule.get("fund_id"))["profit_loss"] >= threshold


def _cond_daily_loss_gte(params, rule):
    threshold = float(params.get("threshold", 0))
    return trade_statistics.daily_stats(fund_id=rule.get("fund_id"))["profit_loss"] <= -threshold


def _cond_consecutive_wins_eq(params, rule):
    count = int(params.get("count", 0))
    return trade_statistics.consecutive_wins(fund_id=rule.get("fund_id")) == count


def _cond_consecutive_losses_eq(params, rule):
    count = int(params.get("count", 0))
    return trade_statistics.consecutive_losses(fund_id=rule.get("fund_id")) == count


def _cond_session_profit_gte(params, rule):
    threshold = float(params.get("threshold", 0))
    session = _resolve_session_for_rule(rule)
    if session is None:
        return False
    return session["realized_pnl"] >= threshold


def _cond_session_loss_gte(params, rule):
    threshold = float(params.get("threshold", 0))
    session = _resolve_session_for_rule(rule)
    if session is None:
        return False
    return session["realized_pnl"] <= -threshold


def _cond_martingale_step_gte(params, rule):
    step = int(params.get("step", 0))
    session = _resolve_session_for_rule(rule)
    if session is None:
        return False
    return session["current_martingale_step"] >= step


def _cond_broker_disconnected(params, rule):
    """True only when the rule's Fund has an attached broker account
    that is observably not connected - never fabricated as True just
    because no account is attached at all (that's a different, separate
    problem fund_manager.can_trade already surfaces)."""
    fund_id = rule.get("fund_id")
    if fund_id is None:
        return False
    account = database.get_fund_primary_broker_account(fund_id)
    if account is None:
        return False
    return account["connection_status"] != "connected"


def _cond_channel_disabled(params, rule):
    channel_id = params.get("channel_id")
    if channel_id is None:
        return False
    channel = next((c for c in database.list_channels() if c["id"] == int(channel_id)), None)
    if channel is None:
        return False
    return not channel["enabled"]


def _cond_lifetime_profit_gte(params, rule):
    """Cumulative realized P/L across all closed trades ever - the
    closest honest equivalent to a "bankroll milestone" AXIM can compute,
    since no live broker account balance is tracked anywhere else in the
    app (see docs/AXIM_APP_PLAN.md known gaps)."""
    threshold = float(params.get("threshold", 0))
    return trade_statistics.lifetime_stats(fund_id=rule.get("fund_id"))["profit_loss"] >= threshold


def _cond_source_win_rate_below(params, rule):
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
        "label": "This Fund's daily profit is at least",
        "params": {"threshold": "number"},
        "fn": _cond_daily_profit_gte,
    },
    "daily_loss_gte": {
        "label": "This Fund's daily loss is at least",
        "params": {"threshold": "number"},
        "fn": _cond_daily_loss_gte,
    },
    "consecutive_wins_eq": {
        "label": "This Fund's consecutive wins reaches exactly",
        "params": {"count": "number"},
        "fn": _cond_consecutive_wins_eq,
    },
    "consecutive_losses_eq": {
        "label": "This Fund's consecutive losses reaches exactly",
        "params": {"count": "number"},
        "fn": _cond_consecutive_losses_eq,
    },
    "session_profit_gte": {
        "label": "This Fund's active session profit is at least",
        "params": {"threshold": "number"},
        "fn": _cond_session_profit_gte,
    },
    "session_loss_gte": {
        "label": "This Fund's active session loss is at least",
        "params": {"threshold": "number"},
        "fn": _cond_session_loss_gte,
    },
    "martingale_step_gte": {
        "label": "Martingale reaches step",
        "params": {"step": "number"},
        "fn": _cond_martingale_step_gte,
    },
    "broker_disconnected": {
        "label": "This Fund's Pocket Option account disconnects",
        "params": {},
        "fn": _cond_broker_disconnected,
    },
    "channel_disabled": {
        "label": "A signal source is disabled",
        "params": {"channel_id": "channel"},
        "fn": _cond_channel_disabled,
    },
    "lifetime_profit_gte": {
        "label": "This Fund's lifetime realized profit is at least",
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
# Action executors - each takes (params: dict, rule_name: str, rule: dict)
# and returns a human-readable string describing what happened (stored
# in the lifecycle log only, not persisted on the rule row).
# ---------------------------------------------------------------------

def _act_stop_active_session(params, rule_name, rule):
    import session_manager
    session = _resolve_session_for_rule(rule)
    if session is None:
        return "no active session to stop"
    session_manager.end_session(session["id"], "stopped_rule", f"stopped by rule '{rule_name}'")
    return f"stopped active session {session['id']}"


def _act_emergency_stop(params, rule_name, rule):
    """Deliberately global regardless of the rule's own fund_id - this is
    the same "stop everything" safety primitive as the UI's Emergency
    Stop button, not scoped like the rest of this module's actions."""
    import session_manager
    database.set_control_state(paused=True, emergency_stop=True)
    for session in database.list_active_trading_sessions():
        session_manager.end_session(session["id"], "stopped_emergency", f"emergency stop by rule '{rule_name}'")
    return "emergency stop engaged"


def _act_increase_risk_profile_percent(params, rule_name, rule):
    percent_increase = float(params.get("percent_increase", 0))
    session = _resolve_session_for_rule(rule)
    if session is None or session["risk_profile_id"] is None:
        return "no active session with a risk profile attached"
    profile = database.get_risk_profile(session["risk_profile_id"])
    if profile is None:
        return "session's risk profile no longer exists"
    new_percent = profile["percent_of_bankroll"] * (1 + percent_increase / 100)
    database.update_risk_profile(profile["id"], percent_of_bankroll=new_percent)
    return f"increased profile {profile['id']} percent_of_bankroll to {new_percent:.4f}"


def _act_switch_session_risk_profile(params, rule_name, rule):
    risk_profile_id = params.get("risk_profile_id")
    session = _resolve_session_for_rule(rule)
    if session is None or risk_profile_id is None:
        return "no active session, or no target profile given"
    database.set_session_risk_profile(session["id"], int(risk_profile_id))
    return f"switched session {session['id']} to risk profile {risk_profile_id}"


def _act_disable_martingale_for_session(params, rule_name, rule):
    session = _resolve_session_for_rule(rule)
    if session is None:
        return "no active session"
    database.set_session_martingale_disabled(session["id"], True)
    return f"disabled martingale for the remainder of session {session['id']}"


def _act_move_profit_to_vault(params, rule_name, rule):
    session = _resolve_session_for_rule(rule)
    if session is None:
        return "no active session"
    unvaulted = session["realized_pnl"] - session["vaulted_amount"]
    if unvaulted <= 0:
        return "no unvaulted profit to move"
    database.add_to_vault(session["id"], unvaulted)
    return f"moved ${unvaulted:.2f} to the vault for session {session['id']}"


def _act_pause_fund(params, rule_name, rule):
    fund_id = rule.get("fund_id")
    if fund_id is None:
        return "this rule has no Fund attached"
    database.update_fund(fund_id, status="paused")
    return f"paused fund {fund_id}"


def _act_resume_fund(params, rule_name, rule):
    fund_id = rule.get("fund_id")
    if fund_id is None:
        return "this rule has no Fund attached"
    database.update_fund(fund_id, status="active")
    return f"resumed fund {fund_id}"


def _act_notify_owner(params, rule_name, rule):
    owner = database.get_owner_user()
    if owner is None:
        return "no owner account found"
    message = params.get("message") or f"Rule '{rule_name}' fired"
    database.create_notification(owner["id"], message, source=f"rule:{rule_name}")
    return f"notified owner: {message}"


def _act_disable_channel(params, rule_name, rule):
    channel_id = params.get("channel_id")
    if channel_id is None:
        return "no channel given"
    database.set_channel_enabled(int(channel_id), False)
    return f"disabled channel {channel_id}"


ACTION_TYPES = {
    "stop_active_session": {
        "label": "Stop today's session",
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
    "disable_martingale_for_session": {
        "label": "Disable Martingale for the remainder of this session",
        "params": {},
        "fn": _act_disable_martingale_for_session,
    },
    "move_profit_to_vault": {
        "label": "Move this session's profit to the Vault",
        "params": {},
        "fn": _act_move_profit_to_vault,
    },
    "pause_fund": {
        "label": "Pause this Fund",
        "params": {},
        "fn": _act_pause_fund,
    },
    "resume_fund": {
        "label": "Resume this Fund",
        "params": {},
        "fn": _act_resume_fund,
    },
    "notify_owner": {
        "label": "Notify the Owner",
        "params": {"message": "text"},
        "fn": _act_notify_owner,
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
    it fired. Returns True if the action fired.

    Whether this call fires is decided by database.record_rule_evaluation
    itself (an atomic compare-and-swap on last_condition_state), not by
    comparing against rule["last_condition_state"] here - that value is a
    snapshot from whenever the caller fetched this rule row, which can be
    stale by the time this function runs if another evaluate_all() call
    is racing this one (see that function's docstring for why)."""
    condition = CONDITION_TYPES.get(rule["condition_type"])
    action = ACTION_TYPES.get(rule["action_type"])
    if condition is None or action is None:
        logger.error("rule_engine: rule %s has unknown condition/action type", rule["id"])
        return False

    try:
        condition_now = bool(condition["fn"](rule["condition_params"], rule))
    except Exception:
        logger.exception("rule_engine: condition evaluation failed for rule %s", rule["id"])
        return False

    fired = database.record_rule_evaluation(rule["id"], condition_now)

    if fired:
        try:
            outcome = action["fn"](rule["action_params"], rule["name"], rule)
            logger.info("rule_engine: rule '%s' (id=%s) fired: %s", rule["name"], rule["id"], outcome)
            database.record_rule_firing(rule["id"], outcome)
        except Exception:
            logger.exception("rule_engine: action execution failed for rule %s", rule["id"])
            database.record_rule_firing(rule["id"], "action failed - see logs/lifecycle.log")
    return fired


def evaluate_all():
    """Evaluates every enabled rule. Called once per trade close from
    core/session_manager.py's event_bus subscription - once per Fund's
    own closed trade, not globally serialized, since different Funds can
    each have their own concurrently-active session (see this module's
    docstring). Two trades on two different Funds closing within
    milliseconds of each other can each trigger their own evaluate_all()
    call, both iterating every enabled rule app-wide at nearly the same
    moment. evaluate_rule -> database.record_rule_evaluation's atomic
    compare-and-swap on last_condition_state is what stops a rule from
    double-firing when that happens, not this function."""
    fired_any = False
    for rule in database.list_rules():
        if not rule["enabled"]:
            continue
        if evaluate_rule(rule):
            fired_any = True
    return fired_any
