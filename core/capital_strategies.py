"""AXIM Capital Strategies (tm) - Phase 1 calculation engine.

Confirmed product direction (2026-07-11 session; see memory/
project_axim_capital_strategies.md and docs/AXIM_CAPITAL_STRATEGIES.md).
This module is a REBRAND + EXPANSION of core/risk_engine.py's existing
sizing math, not a parallel system - existing sizing_mode values
(fixed/percent/dynamic/kelly), Martingale, Compounding, and Profit Vault
are untouched and still work exactly as before. This module adds the
genuinely new calculations the Capital Strategies spec calls for, each a
pure function (bankroll/settings/session state in, a number or decision
out) so every one is directly unit-testable without a live session -
see tests/test_capital_strategies.py.

Naming map (spec name -> what already existed / what's new here):
- Foundation (tm)       = existing sizing_mode='fixed', unchanged.
- Titan Allocation (tm) = existing sizing_mode='dynamic' (recalculates
                          against CURRENT bankroll every trade, which is
                          what the spec's own worked example describes -
                          the older static sizing_mode='percent' does
                          NOT contract with bankroll and is intentionally
                          left as a separate, un-renamed legacy option).
- QuantEdge (tm)        = existing sizing_mode='kelly', unchanged.
- Dominion (tm)         = the existing multi-Fund architecture
                          (core/fund_manager.py) - relabeled, not
                          recalculated; nothing in this module.
- Axiom Vault (tm)      = existing Profit Vault (risk_engine.py's
                          milestone_vault_skim/every_winning_session_vault_skim),
                          relabeled; nothing new in this module yet
                          (Phase 2 extends it with more trigger types).
- Phoenix (tm)          = existing Martingale (risk_engine.py's
                          _apply_martingale) - already step-capped by
                          design, relabeled as an explicit high-risk
                          strategy; nothing new here (Phase 3).
- Apex Ascension (tm), Cashflow (tm), Strike (tm), Sentinel (tm) are
  genuinely new calculations, implemented below.

Phase 2 additions (2026-07-11): Momentum (tm), Fortress (tm), and Empire
(tm) are also genuinely new calculations, implemented below following
the same pure-function/fully-tested discipline. Axiom Vault's extended
trigger types (per_trade, manual) are implemented here too, alongside
the Phase 1 milestone_vault_skim/every_winning_session_vault_skim they
join (those two remain in core/risk_engine.py, unmoved, since they were
already there before this module existed - see per_trade_vault_skim
below for the new one, plus a note on "manual" in the same section).
"""
import json
import math
import random


# ---------------------------------------------------------------------
# Apex Ascension (tm) - tiered unit-value milestone staircase.
# ---------------------------------------------------------------------

def apex_ascension_tier(settings, current_bankroll):
    """Pure: derives the tier index and unit value purely from current
    bankroll vs. the configured milestones - NOT from stored history, so
    this is always correct even if it's never been called before for
    this bankroll level (matches the spec's own worked example exactly:
    $1,000 bankroll -> tier 0/$10 unit; $2,500 -> tier 1/$20; $3,500 ->
    tier 2/$30; ... +$10 unit per +$1,000 bankroll after the initial
    $2,500 threshold, unit frozen below it).

    Returns a dict: tier_index, unit_value, next_threshold,
    amount_remaining_to_next (both bankroll-denominated, not
    profit-denominated - "amount remaining" is real dollars still needed
    to reach the next tier from where the bankroll is right now)."""
    threshold = settings["first_reset_threshold"]
    increment = settings["reset_increment"]
    unit_step = settings["reset_unit_step"]
    base_unit = settings["starting_unit_value"]

    if current_bankroll < threshold:
        return {
            "tier_index": 0,
            "unit_value": base_unit,
            "next_threshold": threshold,
            "amount_remaining_to_next": round(threshold - current_bankroll, 2),
        }

    tier = 1 + math.floor((current_bankroll - threshold) / increment)
    unit_value = base_unit + tier * unit_step
    next_threshold = threshold + tier * increment
    return {
        "tier_index": tier,
        "unit_value": unit_value,
        "next_threshold": next_threshold,
        "amount_remaining_to_next": round(next_threshold - current_bankroll, 2),
    }


def apex_ascension_deployment(settings, current_bankroll):
    """Standard deployment size (unit_value * standard_units) at the
    EFFECTIVE tier - the derived tier, floored at highest_tier_reached
    if downgrade_protection is on, so a drawdown can't silently demote an
    already-earned tier unless the operator has turned that protection
    off. Returns (amount, effective_tier_info) - the caller (risk_engine
    integration) is responsible for calling database.record_tier_event
    if effective_tier_info['tier_index'] > settings['highest_tier_reached'],
    not this pure function (no DB access here by design)."""
    derived = apex_ascension_tier(settings, current_bankroll)
    if settings["downgrade_protection"] and settings["highest_tier_reached"] > derived["tier_index"]:
        protected_tier = settings["highest_tier_reached"]
        unit_value = settings["starting_unit_value"] + protected_tier * settings["reset_unit_step"]
        effective = {
            "tier_index": protected_tier,
            "unit_value": unit_value,
            "next_threshold": derived["next_threshold"],
            "amount_remaining_to_next": derived["amount_remaining_to_next"],
        }
    else:
        effective = derived
    amount = round(effective["unit_value"] * settings["standard_units"], 2)
    return amount, effective


# ---------------------------------------------------------------------
# Sentinel (tm) - graduated drawdown-based deployment reduction.
# ---------------------------------------------------------------------

# Approved default ladder (spec's own default drawdown behavior table).
# suspend_above_percent (drawdown_protection_settings' own column, not
# part of this list) is checked separately, above every band here.
DEFAULT_SENTINEL_BANDS = [
    {"max_drawdown_percent": 5, "action": "full", "reduction_percent": 0},
    {"max_drawdown_percent": 10, "action": "reduce", "reduction_percent": 25},
    {"max_drawdown_percent": 15, "action": "reduce", "reduction_percent": 50},
    {"max_drawdown_percent": 20, "action": "minimum", "reduction_percent": 0},
]


def sentinel_adjusted_amount(settings, base_amount, drawdown_percent, minimum_amount):
    """Pure: layered on top of whatever base sizing already computed
    (Foundation/Titan Allocation/Apex Ascension/... - Sentinel is a
    modifier, not a sizing mode of its own). Returns (amount, status)
    where status is one of "disabled"/"full"/"reduced"/"minimum"/
    "suspended" - the caller decides what "suspended" means operationally
    (e.g. reject the signal), this function only computes the number."""
    if not settings["enabled"]:
        return base_amount, "disabled"

    drawdown_percent = max(drawdown_percent, 0)
    if drawdown_percent > settings["suspend_above_percent"]:
        return 0, "suspended"

    bands = json.loads(settings["bands_json"]) if settings.get("bands_json") else DEFAULT_SENTINEL_BANDS
    bands = sorted(bands, key=lambda b: b["max_drawdown_percent"])
    for band in bands:
        if drawdown_percent <= band["max_drawdown_percent"]:
            if band["action"] == "full":
                return base_amount, "full"
            if band["action"] == "reduce":
                return round(base_amount * (1 - band["reduction_percent"] / 100.0), 2), "reduced"
            if band["action"] == "minimum":
                return minimum_amount, "minimum"
    # Drawdown exceeds every configured band but not suspend_above_percent
    # (e.g. a custom bands_json with gaps) - fail toward caution, not
    # toward the full base amount.
    return minimum_amount, "minimum"


# ---------------------------------------------------------------------
# Cashflow (tm) - income target with a partial-target size reduction.
# ---------------------------------------------------------------------

def cashflow_adjusted_amount(settings, base_amount, period_realized_pnl):
    """Pure. period_realized_pnl is whatever P&L figure matches the
    configured target_period - the caller resolves session-vs-day-vs-
    week/month aggregation (this module has no DB access), matching
    core/risk_engine.py's own documented daily/weekly scoping
    simplification (session-scoped unless a real calendar aggregate is
    wired in later). Returns (amount, target_reached) - target_reached
    True means the caller should stop opening new trades this period,
    same "stop, don't just shrink to zero silently" signal
    trading_sessions.profit_target already gives session_manager."""
    if not settings["enabled"] or settings["target_amount"] <= 0:
        return base_amount, False

    if period_realized_pnl >= settings["target_amount"]:
        return 0, True

    partial_threshold = settings["target_amount"] * (settings["partial_target_percent"] / 100.0)
    if period_realized_pnl >= partial_threshold:
        reduced = base_amount * (1 - settings["partial_reduction_percent"] / 100.0)
        return round(reduced, 2), False

    return base_amount, False


# ---------------------------------------------------------------------
# Strike (tm) - names and completes the session-termination conditions.
# ---------------------------------------------------------------------

def strike_should_terminate(profile, strike_settings, session_state):
    """Pure. profile is a risk_profiles row (reuses its own
    profit_target/max_session_loss/max_trades - Strike doesn't duplicate
    those, it names the existing combination and adds the one genuinely
    missing condition, a duration cap). session_state: dict with
    realized_pnl, trades_count, consecutive_losses, elapsed_minutes.
    Returns None (keep going) or a reason string identifying which
    condition fired, in priority order (profit before loss before
    trade-count before streak before duration - matches the order
    session_manager already checks its own equivalent conditions in)."""
    if not strike_settings["enabled"]:
        return None
    if profile["profit_target"] > 0 and session_state["realized_pnl"] >= profile["profit_target"]:
        return "profit_target"
    if profile["max_session_loss"] > 0 and session_state["realized_pnl"] <= -profile["max_session_loss"]:
        return "loss_limit"
    if profile["max_trades"] > 0 and session_state["trades_count"] >= profile["max_trades"]:
        return "max_trades"
    if strike_settings["max_consecutive_losses"] > 0 and session_state["consecutive_losses"] >= strike_settings["max_consecutive_losses"]:
        return "max_consecutive_losses"
    if strike_settings["max_session_duration_minutes"] > 0 and session_state["elapsed_minutes"] >= strike_settings["max_session_duration_minutes"]:
        return "max_duration"
    return None


# ---------------------------------------------------------------------
# Basic demo simulation (Phase 1 scope, explicitly "basic" per spec - the
# full Monte Carlo / historical-backtest Strategy Lab is Phase 3 and
# belongs in core/backtest_engine.py, not duplicated here).
# ---------------------------------------------------------------------

# Keyed by CATALOG key (capital_strategies_catalog.STRATEGIES), not by
# the underlying sizing_mode value - e.g. "foundation" here, not "fixed",
# since that's what api/capital_strategies_routes.py's simulate endpoint
# receives from the UI's strategy-detail page.
_SIZE_FUNCS = {
    "foundation": lambda bankroll, settings: settings["fixed_amount"],
    "titan_allocation": lambda bankroll, settings: max(bankroll, 0) * (settings["percent_of_bankroll"] / 100.0),
    "apex_ascension": lambda bankroll, settings: apex_ascension_deployment(settings, bankroll)[0],
}


def simulate_strategy(strategy_key, settings, num_trades, win_rate, avg_payout_percent,
                       starting_bankroll=None, seed=None):
    """One deterministic (seedable) simulated run - single-path, not a
    Monte Carlo distribution (that's the Phase 3 Strategy Lab's job).
    Reuses the SAME sizing functions the live engine calls (apex_ascension_
    deployment, etc.) rather than a separately-maintained approximation,
    so a demo run and real behavior can never silently diverge. Returns a
    dict summary rather than the full trade-by-trade log, since this is a
    quick "does this look reasonable" preview, not an audit record."""
    if strategy_key not in _SIZE_FUNCS:
        raise ValueError(f"no basic simulation available for strategy {strategy_key!r} yet")
    if not 0 <= win_rate <= 1:
        raise ValueError("win_rate must be between 0 and 1")
    if avg_payout_percent <= 0:
        raise ValueError("avg_payout_percent must be positive")

    rng = random.Random(seed)
    bankroll = starting_bankroll if starting_bankroll is not None else settings.get("starting_bankroll", 1000)
    peak = bankroll
    max_drawdown_percent = 0.0
    wins = 0
    ruined_at = None

    for i in range(num_trades):
        if bankroll <= 0:
            ruined_at = i
            break
        stake = _SIZE_FUNCS[strategy_key](bankroll, settings)
        stake = max(min(stake, bankroll), 0)
        if stake == 0:
            continue
        if rng.random() < win_rate:
            bankroll += stake * (avg_payout_percent / 100.0)
            wins += 1
        else:
            bankroll -= stake
        peak = max(peak, bankroll)
        if peak > 0:
            drawdown = (peak - bankroll) / peak * 100
            max_drawdown_percent = max(max_drawdown_percent, drawdown)

    trades_run = ruined_at if ruined_at is not None else num_trades
    return {
        "starting_bankroll": round(starting_bankroll if starting_bankroll is not None else settings.get("starting_bankroll", 1000), 2),
        "ending_bankroll": round(bankroll, 2),
        "peak_bankroll": round(peak, 2),
        "max_drawdown_percent": round(max_drawdown_percent, 2),
        "trades_run": trades_run,
        "wins": wins,
        "losses": trades_run - wins,
        "ruined": ruined_at is not None,
    }


# ---------------------------------------------------------------------
# Momentum (tm) - Anti-Martingale / positive progression. Inverse of
# core/risk_engine.py's _apply_martingale: steps the base amount UP per
# consecutive WIN (not loss), always resets to the base step on a loss.
# ---------------------------------------------------------------------

def momentum_deployment(base_amount, settings, step):
    """Pure. `step` is trading_sessions.current_momentum_step - advanced
    on a win, reset to 0 on a loss (core/risk_engine.py's
    on_trade_closed), mirroring exactly how Martingale's step already
    works, just the opposite trigger."""
    if not settings["enabled"] or step <= 0:
        return base_amount
    effective_step = min(step, settings["max_steps"]) if settings["max_steps"] > 0 else step
    ladder = json.loads(settings["custom_ladder_json"]) if settings["custom_ladder_json"] else None
    if ladder:
        index = min(effective_step, len(ladder) - 1)
        return ladder[index]
    return round(base_amount * (settings["multiplier"] ** effective_step), 2)


def momentum_locked_profit(settings, running_profit_this_sequence):
    """Pure. Optional profit-lock the spec calls for: once
    profit_lock_percent > 0, this returns how much of the sequence's
    running profit should be considered "locked" (vaulted / not put back
    at risk on the next step) rather than compounded into the next
    deployment - a separate concern from momentum_deployment's stake
    sizing itself, so the caller (risk_engine.py, if this is wired to a
    vault) decides what "locking" actually does with the number."""
    if not settings["enabled"] or settings["profit_lock_percent"] <= 0:
        return 0
    return round(max(running_profit_this_sequence, 0) * (settings["profit_lock_percent"] / 100.0), 2)


# ---------------------------------------------------------------------
# Fortress (tm) - principal protection. A post-processing layer (like
# Sentinel/Cashflow), not a sizing mode of its own - caps whatever base
# sizing already computed at currently-available (unprotected) capital.
# ---------------------------------------------------------------------

def fortress_adjusted_amount(settings, base_amount, current_bankroll, starting_bankroll):
    """Pure. protected_principal (settings, currently persisted) is
    monotonic - once real profit crosses protection_threshold it locks in
    at starting_bankroll and this function never proposes decreasing it
    again, even through a later drawdown (matches the spec's "the
    principal does not return to the battlefield"). Returns (amount,
    new_protected_principal, should_stop) - the caller persists
    new_protected_principal only if it changed, same pattern as Apex
    Ascension's tier-event recording."""
    if not settings["enabled"]:
        return base_amount, settings["protected_principal"], False

    protected = settings["protected_principal"]
    if protected <= 0 and settings["protection_threshold"] > 0:
        realized_profit = current_bankroll - starting_bankroll
        if realized_profit >= settings["protection_threshold"]:
            protected = starting_bankroll

    if protected <= 0:
        return base_amount, protected, False

    available = current_bankroll - protected
    if available <= 0:
        return 0, protected, True
    return min(base_amount, available), protected, False


# ---------------------------------------------------------------------
# Empire (tm) - ladder challenge. starting_amount -> target_amount across
# a sequence of capital levels, with configurable checkpoint protection.
# ---------------------------------------------------------------------

def empire_generate_ladder(starting_amount, target_amount, num_levels):
    """Pure. Geometric progression so each level is the same PERCENTAGE
    step up from the last (not a fixed dollar step) - level 0 is exactly
    starting_amount, the last level is exactly target_amount."""
    if num_levels <= 1 or starting_amount <= 0:
        return [starting_amount]
    ratio = (target_amount / starting_amount) ** (1 / (num_levels - 1))
    return [round(starting_amount * (ratio ** i), 2) for i in range(num_levels)]


def _empire_ladder(settings):
    if settings.get("levels_json"):
        return json.loads(settings["levels_json"])
    return empire_generate_ladder(settings["starting_amount"], settings["target_amount"], settings["num_levels"])


def empire_next_stake(settings):
    """Pure. Returns (stake, status) for the CURRENT level - status is
    "in_progress", "challenge_complete" (already at/past the final
    level), or "terminated" (current_level was set to the -1 sentinel by
    empire_advance's 'terminate' failure_behavior)."""
    if settings["current_level"] < 0:
        return 0, "terminated"
    ladder = _empire_ladder(settings)
    level = min(settings["current_level"], len(ladder) - 1)
    status = "challenge_complete" if level >= len(ladder) - 1 else "in_progress"
    return ladder[level], status


def empire_advance(settings, won):
    """Pure. Returns the new current_level given a win/loss outcome and
    the configured failure_behavior - called from on_trade_closed,
    mirroring how Martingale/Momentum step advancement already works.
    'lose_ladder_only' means the failed step doesn't cost any ladder
    progress at all (stays at the same level, tries again) - the
    spec's own distinct option from a full reset or checkpoint return."""
    ladder = _empire_ladder(settings)
    max_level = len(ladder) - 1
    if won:
        return min(settings["current_level"] + 1, max_level)
    behavior = settings["failure_behavior"]
    if behavior == "reset_to_start":
        return 0
    if behavior == "return_to_checkpoint":
        return settings["checkpoint_level"]
    if behavior == "lose_ladder_only":
        return settings["current_level"]
    if behavior == "terminate":
        return -1
    return 0


# ---------------------------------------------------------------------
# Axiom Vault (tm) Phase 2 - per_trade trigger type (joins
# core/risk_engine.py's existing milestone_vault_skim and
# every_winning_session_vault_skim, which predate this module and stay
# there, unmoved). "manual" (the spec's other listed trigger type) needs
# no calculation at all - it's just database.add_to_vault called
# directly from an operator-triggered API action, already exposed.
# ---------------------------------------------------------------------

def per_trade_vault_skim(vault, profit_loss):
    """Pure. Skims immediately on every individual winning trade, unlike
    the milestone/session-end triggers which only skim at a boundary."""
    if not vault["enabled"] or vault["trigger_event"] != "per_trade" or profit_loss <= 0:
        return 0
    return round(profit_loss * (vault["vault_percent"] / 100.0), 2)
