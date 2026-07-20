"""Risk Control Center (2026-07-19 UX consistency pass on Money
Management Studio) - answers, for one Fund, using only real computed
state: is it safe to trade, what will the next trade risk and why, what
protections are active, and what a win/loss would do to the balance.

Every field here traces to an existing, already-enforced computation
(fund_manager.can_trade/get_fund_diagnostics, risk_engine.
compute_position_size, risk_manager's global checks, session_manager.
session_progress) - nothing is fabricated or a placeholder. Where a
genuinely live number isn't available (no active session; payout on a
future win), that's stated honestly rather than guessed.

Read-only: compute_position_size is called with record_events=False, so
looking at this page can never itself advance real strategy state
(Apex Ascension tier crossings, Fortress principal protection) - only an
actual trade does that.
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = CORE_DIR.parent / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import fund_manager
import risk_engine
import risk_manager
import session_manager
from settings import TRADE_AMOUNT, MAX_DAILY_LOSS, MAX_CONSECUTIVE_LOSSES, MINIMUM_PAYOUT

# The exact set core/trade_coordinator.py already catches around a real
# compute_position_size call - a strategy-level protection actively
# blocking the next trade, not a bug. Reusing the tuple, not
# re-deriving it, so this can never silently drift out of sync with
# what a real trade would actually do.
_STRATEGY_STOP_EXCEPTIONS = (
    risk_engine.CashflowTargetReached, risk_engine.SentinelSuspended,
    risk_engine.FortressPrincipalProtected, risk_engine.EmpireChallengeOver,
    risk_engine.DailyCompoundingStopped,
)

# The real, illustrative-only payout assumption money_studio.py's own
# worked examples already use - "AXIM's real observed historical
# average," not a live read (that only happens at actual trade time,
# execution/pocket_dom.py's read_payout_and_check_tradeable).
_ILLUSTRATIVE_PAYOUT_PERCENT = 88


def _resolve_active_profile(fund, active_session):
    """Only trusts what risk_engine.compute_position_size will ACTUALLY
    use. For a RUNNING session, that is exclusively its own persisted
    risk_profile_id - api/sessions.py's fund-default fallback only runs
    once, at session START, and writes the resolved id onto the session
    row; a Fund's default changing afterward does not retroactively
    reattach anything, and compute_position_size looks at the session's
    own column, never the Fund's. Showing the Fund's default as "active"
    for an already-running session with no profile of its own would be a
    real mismatch between what's displayed and what's actually computed -
    so that fallback only applies with NO session running, where it's
    honestly "what a new session would use.\""""
    if active_session is not None:
        if active_session.get("risk_profile_id") is not None:
            return active_session["risk_profile_id"], "session"
        return None, None
    if fund["default_risk_profile_id"] is not None:
        return fund["default_risk_profile_id"], "fund_default"
    return None, None


def _reasoning_trail(session_row, profile):
    """Reconstructs a human explanation for the computed stake from real,
    already-persisted state - no new tracking, just narrating fields
    compute_position_size itself already reads."""
    mode = profile["sizing_mode"]
    parts = []
    if mode == "fixed":
        parts.append(f'"{profile["name"]}" sizes every trade at a fixed ${profile["fixed_amount"]:.2f}.')
    elif mode == "percent":
        parts.append(f'"{profile["name"]}" risks {profile["percent_of_bankroll"]}% of bankroll per trade.')
    elif mode == "dynamic":
        parts.append(f'"{profile["name"]}" risks {profile["percent_of_bankroll"]}% of the current (moving) balance per trade.')
    elif mode == "kelly":
        parts.append(f'"{profile["name"]}" uses Kelly sizing from its win-rate/payout estimates.')
    elif mode == "daily_compounding":
        parts.append(f'"{profile["name"]}" recalculates risk once at the start of each trading day.')
    elif mode == "apex_ascension":
        parts.append(f'"{profile["name"]}" (Apex Ascension) sizes from the current bankroll tier.')
    elif mode == "empire":
        parts.append(f'"{profile["name"]}" (Empire) is on a fixed ladder step regardless of balance.')
    else:
        parts.append(f'"{profile["name"]}" uses {mode} sizing.')

    martingale = profile.get("martingale")
    step = session_row["current_martingale_step"] if not session_row["martingale_disabled"] else 0
    if martingale and martingale["enabled"] and step > 0:
        parts.append(f"Currently at martingale step {step} after {step} consecutive loss(es) this session.")

    momentum = profile.get("momentum")
    if momentum and momentum["enabled"] and session_row["current_momentum_step"] > 0:
        parts.append(f"Currently at momentum step {session_row['current_momentum_step']} after a win streak.")

    return " ".join(parts)


def _compute_next_trade(session_row, profile):
    if session_row is None:
        detail = (
            f'"{profile["name"]}" would size trades once a session is running - start one to see a live number.'
            if profile is not None else
            "No active session and no default strategy attached - the legacy global default would apply."
        )
        return {"amount": None, "is_live": False, "blocked_by": None, "reason": detail,
                "sizing_mode": profile["sizing_mode"] if profile else None}

    # profile may be None here (this running session has no risk_profile_id
    # of its own) - compute_position_size already falls through to the
    # legacy global default for exactly that case, so calling it either
    # way (rather than special-casing "no profile" ourselves) guarantees
    # this number always matches exactly what a real trade would compute
    # right now, never a second, potentially-drifting calculation.
    try:
        amount = risk_engine.compute_position_size(session_row["id"], TRADE_AMOUNT, record_events=False)
    except _STRATEGY_STOP_EXCEPTIONS as e:
        return {"amount": None, "is_live": True, "blocked_by": e.rule, "reason": e.reason,
                "sizing_mode": profile["sizing_mode"] if profile else None}

    reason = _reasoning_trail(session_row, profile) if profile is not None else (
        "No strategy attached to this session - using the legacy global default settings."
    )
    return {
        "amount": amount, "is_live": True, "blocked_by": None,
        "reason": reason, "sizing_mode": profile["sizing_mode"] if profile else None,
    }


def _global_protections():
    """The three GLOBAL (app-wide, not per-Fund) protections - see
    core/risk_manager.py's own check_max_daily_loss/
    check_max_consecutive_losses/check_minimum_payout. "tripped" is
    determined by actually calling the real enforcement check (same
    try/except pattern api/main.py's GET /api/settings already uses for
    consecutive_loss_lock_active), not a re-implementation of its logic."""
    checks = []

    daily_loss_limit = database.get_setting("max_daily_loss", MAX_DAILY_LOSS)
    if daily_loss_limit <= 0:
        checks.append({"scope": "global", "name": "Daily Loss Limit", "configured": False, "tripped": False,
                        "detail": "not configured"})
    else:
        try:
            risk_manager.check_max_daily_loss()
            checks.append({"scope": "global", "name": "Daily Loss Limit", "configured": True, "tripped": False,
                            "detail": f"limit ${daily_loss_limit:.2f} - not yet reached"})
        except risk_manager.RiskViolation as e:
            checks.append({"scope": "global", "name": "Daily Loss Limit", "configured": True, "tripped": True,
                            "detail": e.reason})

    max_consecutive = database.get_setting("max_consecutive_losses", MAX_CONSECUTIVE_LOSSES)
    if max_consecutive <= 0:
        # Unlike max_daily_loss, <= 0 here is NOT "disabled" - per
        # risk_manager.diagnose_settings, check_max_consecutive_losses
        # treats 0 currently-open trades as already at-or-past the limit,
        # hard-locking every trade immediately. configured=True because
        # it genuinely, actively is - this is a misconfiguration, not an
        # off switch.
        checks.append({"scope": "global", "name": "Consecutive-Loss Lock", "configured": True, "tripped": True,
                        "detail": f"max_consecutive_losses is {max_consecutive} - blocks every trade immediately"})
    else:
        try:
            risk_manager.check_max_consecutive_losses()
            checks.append({"scope": "global", "name": "Consecutive-Loss Lock", "configured": True, "tripped": False,
                            "detail": f"limit {max_consecutive} consecutive losses - not currently blocking"})
        except risk_manager.RiskViolation as e:
            checks.append({"scope": "global", "name": "Consecutive-Loss Lock", "configured": True, "tripped": True,
                            "detail": e.reason})

    min_payout = database.get_setting("minimum_payout", MINIMUM_PAYOUT)
    # Payout is only known live, right before a trade (execution/
    # pocket_dom.py) - "tripped" isn't knowable in advance, only whether
    # the filter is configured at all.
    checks.append({
        "scope": "global", "name": "Minimum Payout Filter", "configured": min_payout > 0, "tripped": False,
        "detail": f"requires >= {min_payout}% payout, checked live at trade time" if min_payout > 0 else "not configured",
    })

    return checks


def _fund_and_session_protections(fund, diagnostics, active_session):
    checks = []

    broker_check = next((c for c in diagnostics["checks"] if c["category"] == "broker_attachment"), None)
    if broker_check is not None:
        checks.append({"scope": "fund", "name": "Broker Connected", "configured": True,
                        "tripped": not broker_check["ok"], "detail": broker_check["detail"]})

    if fund["loss_limit"] > 0:
        allowed, reason, _ = fund_manager.can_trade(fund["id"])
        tripped = (not allowed) and reason is not None and "loss limit" in reason
        checks.append({"scope": "fund", "name": "Fund Loss Limit (lifetime)", "configured": True,
                        "tripped": tripped, "detail": reason if tripped else f"limit ${fund['loss_limit']:.2f} - not yet reached"})
    else:
        checks.append({"scope": "fund", "name": "Fund Loss Limit (lifetime)", "configured": False, "tripped": False,
                        "detail": "not configured"})

    if active_session is not None:
        if active_session["loss_limit"] > 0:
            headroom = active_session["remaining_to_loss_limit"]
            checks.append({"scope": "session", "name": "Session Loss Limit", "configured": True,
                            "tripped": headroom is not None and headroom <= 0,
                            "detail": f"${headroom:.2f} headroom remaining" if headroom is not None else "not configured"})
        if active_session["profit_target"] > 0:
            remaining = active_session["remaining_to_target"]
            checks.append({"scope": "session", "name": "Session Profit Target", "configured": True,
                            "tripped": remaining is not None and remaining <= 0,
                            "detail": f"${remaining:.2f} remaining to target" if remaining is not None else "not configured"})

    return checks


def _max_exposure_today(fund, diagnostics, active_session):
    exposure_check = next((c for c in diagnostics["checks"] if c["category"] == "exposure"), None)
    pending_stake = exposure_check["pending_stake"] if exposure_check else 0.0
    pending_count = exposure_check["pending_trade_count"] if exposure_check else 0

    headroom = None
    headroom_source = None
    if active_session is not None and active_session["loss_limit"] > 0 and active_session["remaining_to_loss_limit"] is not None:
        headroom = active_session["remaining_to_loss_limit"]
        headroom_source = "session loss limit"
    elif fund["loss_limit"] > 0:
        performance = fund_manager.get_fund_performance(fund["id"])
        headroom = max(fund["loss_limit"] + performance["profit_loss"] - pending_stake, 0)
        headroom_source = "fund lifetime loss limit"

    return {
        "pending_stake": pending_stake, "pending_count": pending_count,
        "headroom_to_loss_limit": headroom, "headroom_source": headroom_source,
    }


def _compute_outcomes(bankroll, next_trade):
    if next_trade["amount"] is None:
        return {"win": None, "loss": None}
    stake = next_trade["amount"]
    loss = {"amount": -stake, "bankroll_after": round(bankroll - stake, 2), "is_estimate": False}
    estimated_win_profit = round(stake * (_ILLUSTRATIVE_PAYOUT_PERCENT / 100.0), 2)
    win = {
        "amount": estimated_win_profit, "bankroll_after": round(bankroll + estimated_win_profit, 2),
        "is_estimate": True, "payout_assumption_percent": _ILLUSTRATIVE_PAYOUT_PERCENT,
    }
    return {"win": win, "loss": loss}


def get_risk_control_center(fund_id):
    """Returns None if the fund doesn't exist (caller 404s)."""
    fund = database.get_fund(fund_id)
    if fund is None:
        return None

    balances = fund_manager.get_fund_balances(fund_id)
    diagnostics = fund_manager.get_fund_diagnostics(fund_id)
    allowed, blocking_reason, _ = fund_manager.can_trade(fund_id)

    session_row = database.get_active_trading_session_for_fund(fund_id)
    active_session = session_manager.session_progress(session_row)

    profile_id, profile_source = _resolve_active_profile(fund, active_session)
    profile = database.get_risk_profile(profile_id) if profile_id is not None else None

    next_trade = _compute_next_trade(session_row, profile)
    protections = _global_protections() + _fund_and_session_protections(fund, diagnostics, active_session)
    outcomes = _compute_outcomes(balances["trading_balance"], next_trade)

    return {
        "fund_id": fund_id,
        "fund_name": fund["name"],
        "safe_to_trade": allowed and not any(c["configured"] and c["tripped"] for c in protections),
        "blocking_reason": blocking_reason,
        "current_bankroll": balances["trading_balance"],
        "active_profile": {"id": profile["id"], "name": profile["name"], "source": profile_source} if profile else None,
        "active_session": {"id": active_session["id"], "name": active_session["name"]} if active_session else None,
        "next_trade": next_trade,
        "max_exposure_today": _max_exposure_today(fund, diagnostics, active_session),
        "protections": protections,
        "if_it_wins": outcomes["win"],
        "if_it_loses": outcomes["loss"],
    }
