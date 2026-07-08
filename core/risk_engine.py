"""Risk Engine (docs/AXIM_APP_PLAN.md Phase 4) - real position-sizing
math for a session's attached risk_profile: fixed/percent/dynamic/Kelly
sizing, Martingale stepping, Compounding risk-percent adjustment, and
Profit Vault skimming.

Scoping notes, stated plainly rather than silently overclaimed:
- Compounding's "daily"/"weekly" modes and the Vault's "daily_target"/
  "weekly_target" triggers are evaluated against the SESSION's own
  realized P&L, not a true calendar-spanning daily/weekly aggregate
  across multiple sessions - that would need its own tracking layer this
  phase doesn't build. Sessions are this system's unit of trading, so
  this is a reasonable placeholder, not a bug, but it is a simplification.
- Martingale's same_asset_only/same_source_only fields are stored but NOT
  YET enforced (would need last-trade asset/source tracking per session,
  a real follow-up, not fabricated behavior) - same honesty pattern as
  every other "stored but not enforced" field elsewhere in this codebase
  (e.g. trading_sessions.require_confirmation).
- Kelly win-rate/payout are user-supplied ESTIMATES on the profile, not
  derived from a live-updating empirical win rate - a profile with only
  a handful of trades has too little data for empirical estimation to be
  meaningful; the user is expected to set a deliberate, honest estimate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json

import database
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def _effective_risk_percent(base_percent, compounding, current_pnl, bankroll):
    """Applies the compounding profile's profit-milestone steps (and
    drawdown reset) to a base risk percent. All modes besides 'disabled'
    use the same profit-milestone-step mechanism - see module docstring
    for the daily/weekly scoping simplification."""
    if compounding is None or compounding["mode"] == "disabled":
        return base_percent

    effective = base_percent
    steps = json.loads(compounding["steps_json"]) if compounding["steps_json"] else []
    for step in sorted(steps, key=lambda s: s["profit_threshold"]):
        if current_pnl >= step["profit_threshold"]:
            effective = step["risk_percent"]

    if compounding["drawdown_reset_percent"] > 0 and bankroll > 0 and current_pnl < 0:
        drawdown_pct = (-current_pnl / bankroll) * 100
        if drawdown_pct >= compounding["drawdown_reset_percent"]:
            effective = base_percent

    if compounding["max_risk_percent"] > 0:
        effective = min(effective, compounding["max_risk_percent"])
    if compounding["min_risk_percent"] > 0:
        effective = max(effective, compounding["min_risk_percent"])
    return effective


def _base_amount(profile, session):
    """Sizing before Martingale stepping - fixed/percent/dynamic/Kelly."""
    mode = profile["sizing_mode"]
    bankroll = profile["bankroll"]
    current_pnl = session["realized_pnl"]

    if mode == "fixed":
        return profile["fixed_amount"]

    if mode == "percent":
        percent = _effective_risk_percent(profile["percent_of_bankroll"], profile["compounding"], current_pnl, bankroll)
        return bankroll * (percent / 100.0) if bankroll > 0 else profile["fixed_amount"]

    if mode == "dynamic":
        percent = _effective_risk_percent(profile["percent_of_bankroll"], profile["compounding"], current_pnl, bankroll)
        current_bankroll = bankroll + current_pnl
        return current_bankroll * (percent / 100.0) if current_bankroll > 0 else profile["fixed_amount"]

    if mode == "kelly":
        p = profile["kelly_win_rate_estimate"]
        b = profile["kelly_payout_estimate"]
        if p is None or b is None or b <= 0:
            return profile["fixed_amount"]
        f_star = p - (1 - p) / b
        f_star = max(f_star, 0) * profile["kelly_fraction_multiplier"]
        current_bankroll = bankroll + current_pnl
        return current_bankroll * f_star if current_bankroll > 0 else profile["fixed_amount"]

    return profile["fixed_amount"]


def _apply_martingale(amount, martingale, step):
    """Steps the base amount up according to the martingale ladder - a
    custom ladder (explicit dollar amounts per step) takes priority over
    the multiplier if both are set. Clamps at max_steps (stops escalating
    further, doesn't error) and at max_total_exposure (0 = uncapped)."""
    if martingale is None or not martingale["enabled"] or step <= 0:
        return amount

    effective_step = min(step, martingale["max_steps"]) if martingale["max_steps"] > 0 else step

    ladder = json.loads(martingale["custom_ladder_json"]) if martingale["custom_ladder_json"] else None
    if ladder:
        index = min(effective_step, len(ladder) - 1)
        stepped = ladder[index]
    else:
        stepped = amount * (martingale["multiplier"] ** effective_step)

    if martingale["max_total_exposure"] > 0:
        stepped = min(stepped, martingale["max_total_exposure"])
    return stepped


def compute_position_size(session_id, static_default_amount):
    """The one entry point trade_coordinator.py calls. Returns
    risk_manager.compute_trade_amount(static_default_amount) unchanged if
    the session has no risk_profile_id - a profile-less session's sizing
    is completely untouched by this module."""
    if session_id is None:
        import risk_manager
        return risk_manager.compute_trade_amount(static_default_amount)

    session = database.get_trading_session(session_id)
    if session is None or session["risk_profile_id"] is None:
        import risk_manager
        return risk_manager.compute_trade_amount(static_default_amount)

    profile = database.get_risk_profile(session["risk_profile_id"])
    if profile is None:
        import risk_manager
        return risk_manager.compute_trade_amount(static_default_amount)

    amount = _base_amount(profile, session)
    amount = _apply_martingale(amount, profile["martingale"], session["current_martingale_step"])

    if profile["max_trade_amount"] > 0:
        amount = min(amount, profile["max_trade_amount"])

    return round(amount, 2)


def project_martingale_exposure(martingale, base_amount):
    """Projected exposure preview for the Risk Engine UI - the ladder of
    stake sizes and their running total, exactly as _apply_martingale
    would compute them, without needing a live session."""
    if not martingale["enabled"]:
        return {"steps": [], "total_exposure": 0}

    ladder = json.loads(martingale["custom_ladder_json"]) if martingale["custom_ladder_json"] else None
    steps = []
    max_steps = martingale["max_steps"] or (len(ladder) if ladder else 5)
    for step in range(max_steps):
        if ladder:
            amount = ladder[min(step, len(ladder) - 1)]
        else:
            amount = base_amount * (martingale["multiplier"] ** step)
        if martingale["max_total_exposure"] > 0:
            amount = min(amount, martingale["max_total_exposure"])
        steps.append(round(amount, 2))
    return {"steps": steps, "total_exposure": round(sum(steps), 2)}


def milestone_vault_skim(vault, realized_pnl, vaulted_amount):
    """Pure: how much to skim to the vault right now for a
    'milestone_based' trigger, given the session's current realized_pnl
    and how much has already been vaulted this session. Returns 0 if no
    new milestone has been crossed. Also used by core/backtest_engine.py
    so simulated vault behavior matches live behavior exactly - not a
    separately-maintained copy of this math."""
    if not vault["enabled"] or vault["trigger_event"] != "milestone_based" or vault["milestone_amount"] <= 0:
        return 0
    skim_per_milestone = vault["milestone_amount"] * (vault["vault_percent"] / 100.0)
    milestones_crossed = int(realized_pnl // vault["milestone_amount"])
    # Every milestone-based skim adds exactly skim_per_milestone to
    # vaulted_amount, so dividing back out tells us how many milestones
    # have already been paid - no separate counter needed.
    already_vaulted_milestones = int(vaulted_amount // skim_per_milestone) if skim_per_milestone > 0 else 0
    if milestones_crossed > already_vaulted_milestones and milestones_crossed > 0:
        return skim_per_milestone
    return 0


def every_winning_session_vault_skim(vault, realized_pnl):
    """Pure: how much to skim to the vault when a session ends, for an
    'every_winning_session' trigger. Returns 0 if not applicable."""
    if vault["enabled"] and vault["trigger_event"] == "every_winning_session" and realized_pnl > 0:
        return realized_pnl * (vault["vault_percent"] / 100.0)
    return 0


def on_trade_closed(session_id, won, profit_loss):
    """Advances/resets the Martingale step and skims the Profit Vault on
    a milestone trigger - called by core/session_manager.py's
    trade.closed subscriber, right alongside its own P&L update, so both
    happen from the same single hook into the outcome-tracking path."""
    session = database.get_trading_session(session_id)
    if session is None or session["risk_profile_id"] is None:
        return
    profile = database.get_risk_profile(session["risk_profile_id"])
    if profile is None:
        return

    martingale = profile["martingale"]
    if martingale["enabled"]:
        if won and martingale["reset_after_win"]:
            database.reset_martingale_step(session_id)
        elif not won:
            database.advance_martingale_step(session_id)

    vault = profile["profit_vault"]
    session = database.get_trading_session(session_id)  # refreshed realized_pnl
    skim = milestone_vault_skim(vault, session["realized_pnl"], session["vaulted_amount"])
    if skim > 0:
        database.add_to_vault(session_id, skim)
        logger.info("risk_engine: vaulted $%.2f for session %s at a milestone", skim, session_id)


def on_session_ended(session_id):
    """Every-winning-session vault trigger - called when a session
    transitions to any stopped_* status (core/session_manager.py and
    api/sessions.py's manual/emergency stop both route through
    core/session_manager.end_session so this always runs)."""
    session = database.get_trading_session(session_id)
    if session is None or session["risk_profile_id"] is None:
        return
    profile = database.get_risk_profile(session["risk_profile_id"])
    if profile is None:
        return
    vault = profile["profit_vault"]
    skim = every_winning_session_vault_skim(vault, session["realized_pnl"])
    if skim > 0:
        database.add_to_vault(session_id, skim)
        logger.info("risk_engine: vaulted $%.2f for session %s at session end", skim, session_id)
