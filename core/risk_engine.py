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
  sizing_mode="daily_compounding" (see core/daily_compounding.py) is the
  genuine calendar-day-spanning tracking layer this note describes as
  missing - built specifically for that one sizing mode, not retrofitted
  onto Compounding's older "daily"/"weekly" mode names above.
- Martingale's same_asset_only/same_source_only fields are stored but NOT
  YET enforced (would need last-trade asset/source tracking per session,
  a real follow-up, not fabricated behavior) - same honesty pattern as
  every other "stored but not enforced" field elsewhere in this codebase
  (e.g. trading_sessions.require_confirmation).
- Kelly win-rate/payout are user-supplied ESTIMATES on the profile, not
  derived from a live-updating empirical win rate - a profile with only
  a handful of trades has too little data for empirical estimation to be
  meaningful; the user is expected to set a deliberate, honest estimate.

AXIM Capital Strategies (tm) - Apex Ascension is wired in as a genuinely
new sizing_mode branch below (additive, existing modes unchanged). Sentinel
and Cashflow are wired in as OPT-IN post-processing layers, both defaulting
to enabled=0 in the DB - every risk_profile that existed before this
feature landed has enabled=0 on both, so this is a strict no-op for them;
only a profile an operator has deliberately turned one on for is affected.
Sentinel's drawdown_percent and Cashflow's period_realized_pnl both reuse
this module's existing session-scoped-P&L simplification (same pattern
already documented above for Compounding/Vault) rather than adding new
peak-tracking state - a real simplification, stated plainly, not
fabricated behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json

import database
import capital_strategies
import daily_compounding
import fund_manager
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def _effective_risk_percent(base_percent, compounding, current_pnl, bankroll, trades_count=0):
    """Applies the compounding profile's profit-milestone steps (and
    drawdown reset) to a base risk percent. All modes besides 'disabled'
    and 'alternating_cycle' use the same profit-milestone-step mechanism -
    see module docstring for the daily/weekly scoping simplification.

    'alternating_cycle' (Money Management Studio's Alternating Compound
    strategy) is fundamentally different: risk follows the SESSION's own
    trade count, cycling through steps_json's percents in order and
    repeating, never reacting to P&L at all - trades_count is the count
    of trades already completed THIS session before the current one
    (matches trading_sessions.trades_count's own timing: incremented only
    once a signal reaches execution, read here before that happens for
    the current trade), so trade 1 gets steps_json[0], trade 2 gets
    steps_json[1], and so on, wrapping via modulo. Real, exact cycling -
    not the flat average-percent approximation this strategy used before
    risk_engine gained this mode."""
    if compounding is None or compounding["mode"] == "disabled":
        return base_percent

    if compounding["mode"] == "alternating_cycle":
        cycle = json.loads(compounding["steps_json"]) if compounding["steps_json"] else []
        if not cycle:
            return base_percent
        return cycle[trades_count % len(cycle)]

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


def _base_amount(profile, session, record_events=True):
    """Sizing before Martingale stepping - fixed/percent/dynamic/Kelly.

    record_events=False (used by core/backtest_engine.py) suppresses the
    apex_ascension tier-crossing DB write below - a backtest replays a
    signal pool through a PROFILE SNAPSHOT, not the live profile, and
    must never leave real rows in capital_tier_events. Every other
    caller (core/trade_coordinator.py's live path, indirectly) leaves
    this True, so live behavior is completely unchanged."""
    mode = profile["sizing_mode"]
    bankroll = profile["bankroll"]
    current_pnl = session["realized_pnl"]
    trades_count = session.get("trades_count", 0)

    if mode == "fixed":
        return profile["fixed_amount"]

    if mode == "percent":
        percent = _effective_risk_percent(profile["percent_of_bankroll"], profile["compounding"], current_pnl, bankroll, trades_count)
        return bankroll * (percent / 100.0) if bankroll > 0 else profile["fixed_amount"]

    if mode == "dynamic":
        percent = _effective_risk_percent(profile["percent_of_bankroll"], profile["compounding"], current_pnl, bankroll, trades_count)
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

    if mode == "apex_ascension":
        apex = profile["apex_ascension"]
        if not apex["enabled"]:
            return profile["fixed_amount"]
        current_bankroll = bankroll + current_pnl
        amount, effective_tier = capital_strategies.apex_ascension_deployment(apex, current_bankroll)
        if record_events and effective_tier["tier_index"] > apex["highest_tier_reached"]:
            database.record_tier_event(
                profile["id"], "apex_ascension", effective_tier["tier_index"],
                effective_tier["unit_value"], current_bankroll, fund_id=session.get("fund_id"),
            )
        return amount

    if mode == "empire":
        empire = profile["empire"]
        if not empire["enabled"]:
            return profile["fixed_amount"]
        stake, status = capital_strategies.empire_next_stake(empire)
        if status in ("challenge_complete", "terminated"):
            raise EmpireChallengeOver(status)
        return stake

    if mode == "daily_compounding":
        settings = profile["daily_compounding"]
        if not settings["enabled"]:
            return profile["fixed_amount"]

        if record_events:
            # Live: the day's starting balance is a real, persisted fact
            # (core/database.py's fund_daily_starting_balance) so it stays
            # fixed all day even as the fund's actual balance moves with
            # each trade - session.get("fund_id") is None for a profile-
            # less/legacy session, which has no Fund-scoped daily balance
            # to anchor to, so this mode simply isn't usable there.
            fund_id = session.get("fund_id")
            if fund_id is None:
                return profile["fixed_amount"]
            trading_date = daily_compounding.trading_date_for(settings["timezone"])
            day_starting_balance = database.get_or_create_fund_daily_starting_balance(
                fund_id, trading_date,
                lambda: fund_manager.get_fund_balances(fund_id)["trading_balance"],
            )
            since = daily_compounding.day_start_iso(settings["timezone"], trading_date)
            realized_pnl_today = database.get_fund_realized_pnl_since(fund_id, since)
            pending_stake_today = database.get_fund_pending_stake_since(fund_id, since)
        else:
            # Backtest: core/backtest_engine.py forces one simulated
            # "session" per calendar day for this sizing mode, so
            # profile["bankroll"] (fixed at the top of that day's loop)
            # and session["realized_pnl"] (that same day's own running
            # total) are already exactly the day-scoped numbers this mode
            # needs - no separate day-boundary bookkeeping required here.
            day_starting_balance = bankroll
            realized_pnl_today = current_pnl
            pending_stake_today = 0.0

        stop_reason = daily_compounding.check_should_stop(
            settings, day_starting_balance, realized_pnl_today, pending_stake_today,
        )
        if stop_reason:
            raise DailyCompoundingStopped(stop_reason, day_starting_balance)

        return daily_compounding.compute_risk_per_trade(settings, day_starting_balance)

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

    # Real-money bankroll auto-update (docs/AXIM_LIVE_READINESS_CHECKLIST.md's
    # "Risk-profile bankroll does not auto-update from real P&L" gap) -
    # profile["bankroll"] is a static, manually-set field that CAN be
    # shared across multiple Funds (nothing in the schema prevents
    # attaching the same risk_profile_id to more than one Fund), so
    # mutating it directly on every trade would incorrectly bleed one
    # Fund's real P&L into another Fund's next session. fund_manager's
    # trading_balance is already correctly Fund-scoped and vault-aware
    # (core/fund_manager.py's own docstring: "a fund's numbers can never
    # drift out of sync with its actual trade history") - reused here as
    # a local override, never written back to the shared profile row.
    # Subtracting this session's OWN realized_pnl converts trading_balance
    # (which already includes the still-open current session) back into
    # "balance as of the start of this session" - the same role
    # profile["bankroll"] already plays, since every sizing formula below
    # adds session["realized_pnl"] to it separately; without the
    # subtraction this session's own P&L would be counted twice.
    if session["fund_id"] is not None:
        fund_balances = fund_manager.get_fund_balances(session["fund_id"])
        if fund_balances is not None:
            profile["bankroll"] = fund_balances["trading_balance"] - session["realized_pnl"]

    amount = _base_amount(profile, session)
    if not session["martingale_disabled"]:
        amount = _apply_martingale(amount, profile["martingale"], session["current_martingale_step"])

    # Momentum (tm) - opposite trigger from Martingale (steps on a WIN
    # streak, not a loss streak) but the same "modifier on top of the
    # base stake" role. Stacks with Martingale if an operator somehow
    # enables both at once (an unusual, not specially blocked
    # configuration) rather than silently favoring one.
    # `or {"enabled": 0}`: defense-in-depth alongside the row-backfill
    # migration in core/database.py's initialize_database() - a profile
    # somehow still missing this settings row is treated as this
    # strategy simply being off, not a crash, matching every one of
    # these being an opt-in, default-disabled layer.
    momentum = profile["momentum"] or {"enabled": 0}
    if momentum["enabled"]:
        amount = capital_strategies.momentum_deployment(amount, momentum, session["current_momentum_step"])

    # Cashflow (tm) and Sentinel (tm) - opt-in post-processing layers on
    # top of whatever base sizing/Martingale/Momentum already computed.
    # All default to enabled=0, so this is a strict no-op for every
    # risk_profile that existed before this feature landed.
    cashflow = profile["cashflow"]
    if cashflow["enabled"]:
        amount, target_reached = capital_strategies.cashflow_adjusted_amount(
            cashflow, amount, session["realized_pnl"],
        )
        if target_reached:
            raise CashflowTargetReached(cashflow["target_amount"])

    drawdown_protection = profile["drawdown_protection"]
    if drawdown_protection["enabled"] and profile["bankroll"] > 0:
        drawdown_percent = max(0, -session["realized_pnl"] / profile["bankroll"] * 100) if session["realized_pnl"] < 0 else 0
        minimum_amount = profile["fixed_amount"]
        amount, sentinel_status = capital_strategies.sentinel_adjusted_amount(
            drawdown_protection, amount, drawdown_percent, minimum_amount,
        )
        if sentinel_status == "suspended":
            raise SentinelSuspended(drawdown_percent, drawdown_protection["suspend_above_percent"])

    # Fortress (tm) - caps amount at currently-available (unprotected)
    # profit once protection has triggered; persists protected_principal
    # only when it actually changed (a real write, but a rare one - most
    # calls just read the already-persisted value back unchanged).
    fortress = profile["fortress"]
    if fortress["enabled"]:
        current_bankroll = profile["bankroll"] + session["realized_pnl"]
        amount, new_protected, should_stop = capital_strategies.fortress_adjusted_amount(
            fortress, amount, current_bankroll, profile["bankroll"],
        )
        if new_protected != fortress["protected_principal"]:
            database.update_fortress_settings(profile["id"], protected_principal=new_protected)
        if should_stop:
            raise FortressPrincipalProtected(new_protected)

    if profile["max_trade_amount"] > 0:
        amount = min(amount, profile["max_trade_amount"])

    return round(amount, 2)


class CashflowTargetReached(Exception):
    """Same (rule, reason) shape as risk_manager.RiskViolation /
    session_manager.SessionLimitReached, so core/trade_coordinator.py's
    existing _reject() helper handles this without a separate code path -
    raised by compute_position_size when Cashflow's income target has
    been hit."""
    def __init__(self, target_amount):
        self.rule = "cashflow_target_reached"
        self.reason = f"Cashflow target of ${target_amount} reached - no new trades this period"
        super().__init__(self.reason)


class SentinelSuspended(Exception):
    """Same (rule, reason) shape - see CashflowTargetReached above.
    Raised by compute_position_size when Sentinel's drawdown has crossed
    suspend_above_percent."""
    def __init__(self, drawdown_percent, suspend_above_percent):
        self.rule = "sentinel_suspended"
        self.reason = f"Sentinel suspended trading - drawdown {drawdown_percent:.1f}% exceeds {suspend_above_percent}%"
        super().__init__(self.reason)


class FortressPrincipalProtected(Exception):
    """Same (rule, reason) shape - see CashflowTargetReached above.
    Raised by compute_position_size when Fortress's active profit is
    fully depleted and protected principal must not be risked."""
    def __init__(self, protected_principal):
        self.rule = "fortress_principal_protected"
        self.reason = f"Fortress: active profit depleted - ${protected_principal} principal is protected, not trading it"
        super().__init__(self.reason)


class DailyCompoundingStopped(Exception):
    """Same (rule, reason) shape as CashflowTargetReached/SentinelSuspended/
    FortressPrincipalProtected above. Raised by _base_amount's
    "daily_compounding" branch once today's realized P/L has crossed the
    day's profit target or loss limit - deliberately does NOT pause the
    Fund (fund_manager.check_fund_limits' LIFETIME breach does that, and
    requires a manual resume): this is a same-calendar-day-only stop that
    lifts itself automatically the moment core/daily_compounding.
    trading_date_for rolls over to a new date, since that's a fresh
    (fund_id, trading_date) row with no prior realized P/L."""
    def __init__(self, reason, starting_balance):
        self.rule = f"daily_compounding_{reason}"
        label = "profit target" if reason == "profit_target" else "loss limit"
        self.reason = (
            f"Daily Compounding: today's {label} reached (today's starting balance ${starting_balance:.2f}) "
            f"- no new trades until the next trading day"
        )
        super().__init__(self.reason)


class EmpireChallengeOver(Exception):
    """Same (rule, reason) shape - see CashflowTargetReached above.
    Raised by _base_amount when an Empire ladder challenge has either
    been completed (reached the target level) or terminated (a failed
    step under failure_behavior='terminate')."""
    def __init__(self, status):
        self.rule = f"empire_{status}"
        self.reason = (
            "Empire challenge complete - target level reached" if status == "challenge_complete"
            else "Empire challenge terminated by a failed step"
        )
        super().__init__(self.reason)


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

    # Momentum (tm) - exact inverse of Martingale's step handling above:
    # advances on a WIN, always resets on a loss (no reset_after_win-style
    # toggle - see momentum_settings' own schema comment for why).
    # `or {"enabled": 0}`: defense-in-depth alongside the row-backfill
    # migration in core/database.py's initialize_database() - a profile
    # somehow still missing this settings row is treated as this
    # strategy simply being off, not a crash, matching every one of
    # these being an opt-in, default-disabled layer.
    momentum = profile["momentum"] or {"enabled": 0}
    if momentum["enabled"]:
        if won:
            database.advance_momentum_step(session_id)
        else:
            database.reset_momentum_step(session_id)

    # Empire (tm) - ladder level advances/falls back based on this same
    # win/loss outcome, mirroring the Martingale/Momentum step pattern
    # just with a richer set of failure behaviors than a simple reset.
    empire = profile["empire"]
    if empire["enabled"] and empire["current_level"] >= 0:
        new_level = capital_strategies.empire_advance(empire, won)
        if new_level != empire["current_level"]:
            database.update_empire_settings(profile["id"], current_level=new_level)

    vault = profile["profit_vault"]
    session = database.get_trading_session(session_id)  # refreshed realized_pnl

    # Axiom Vault (tm) per_trade trigger - skims immediately on this one
    # trade's own profit, independent of the milestone/session-end
    # triggers below (mutually exclusive via trigger_event, same as
    # those two already are with each other).
    per_trade_skim = capital_strategies.per_trade_vault_skim(vault, profit_loss)
    if per_trade_skim > 0:
        database.add_to_vault(session_id, per_trade_skim)
        logger.info("risk_engine: vaulted $%.2f for session %s on this trade", per_trade_skim, session_id)

    skim = milestone_vault_skim(vault, session["realized_pnl"], session["vaulted_amount"])
    if skim > 0:
        database.add_to_vault(session_id, skim)
        logger.info("risk_engine: vaulted $%.2f for session %s at a milestone", skim, session_id)

    # Daily Compounding's own vault-on-target - a genuinely different
    # trigger from the three above (session-scoped): skims the moment
    # THIS trade's close pushes the FUND's today's realized P/L across
    # the day's profit target, computed fresh from the signals table
    # (see core/daily_compounding.py's own "compute, don't cache" note)
    # rather than a persisted counter, so there is nothing to double-skim
    # even if this hook somehow ran twice for the same trade.
    daily = profile["daily_compounding"]
    if daily["enabled"] and daily["vault_enabled"] and session["fund_id"] is not None:
        settings = daily
        trading_date = daily_compounding.trading_date_for(settings["timezone"])
        starting_balance = database.get_or_create_fund_daily_starting_balance(
            session["fund_id"], trading_date,
            lambda: fund_manager.get_fund_balances(session["fund_id"])["trading_balance"],
        )
        since = daily_compounding.day_start_iso(settings["timezone"], trading_date)
        realized_pnl_after = database.get_fund_realized_pnl_since(session["fund_id"], since)
        realized_pnl_before = realized_pnl_after - profit_loss
        daily_skim = daily_compounding.vault_skim_on_target(
            settings, starting_balance, realized_pnl_before, realized_pnl_after,
        )
        if daily_skim > 0:
            database.add_to_vault(session_id, daily_skim)
            logger.info(
                "risk_engine: vaulted $%.2f for session %s on Daily Compounding's target being hit",
                daily_skim, session_id,
            )


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
