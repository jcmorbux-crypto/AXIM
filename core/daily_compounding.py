"""Daily Compounding (tm) - AXIM's 5th official Money Management Studio
strategy (2026-07-18 product directive). Genuinely different in kind
from every other sizing_mode: risk per trade, the daily profit target,
and the daily loss limit are all fractions of the Fund's trading
balance AT THE START OF A CALENDAR DAY (in the fund's own configured
timezone) - recalculated once at the day boundary, never per-trade or
per-session.

Every function here is pure (no DB access) so the exact same math can
be shared, unmodified, between:
- core/risk_engine.py's _base_amount (live Demo/Live execution), which
  supplies starting_balance/realized_pnl_today from real DB-backed
  state (see core/database.py's fund_daily_starting_balance table and
  get_fund_realized_pnl_since).
- core/backtest_engine.py's simulate_strategy, which supplies the same
  two numbers from its own day-grouped in-memory simulation state.

Per the product directive: "Do not create one simplified UI rule and a
separate engine implementation" - this module IS the one authoritative
implementation; nothing about Daily Compounding's rules lives anywhere
else.
"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_RISK_PERCENT = 1.0
DEFAULT_PROFIT_TARGET_PERCENT = 50.0
DEFAULT_LOSS_LIMIT_PERCENT = 25.0
MIN_RISK_PERCENT = 1.0


def trading_date_for(tz_name, now=None):
    """Today's calendar date (YYYY-MM-DD) in the given IANA timezone -
    the fund's own configured daily_compounding.timezone, never the
    server's local time or a bare UTC assumption, per the product
    directive's "use the configured Fund/account timezone for the daily
    boundary." An unknown/invalid timezone string fails safe to UTC
    rather than raising - a bad value saved from the UI must never take
    down live trade sizing."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    moment = now.astimezone(tz) if now is not None else datetime.now(tz)
    return moment.strftime("%Y-%m-%d")


def day_start_iso(tz_name, trading_date):
    """The ISO timestamp (UTC-naive, matching how every other timestamp
    in this codebase is stored - see core/database.py's datetime.now().
    isoformat() convention) of local midnight for trading_date in
    tz_name - the lower bound for "today's" realized P/L queries."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    local_midnight = datetime.strptime(trading_date, "%Y-%m-%d").replace(tzinfo=tz)
    return local_midnight.astimezone().replace(tzinfo=None).isoformat()


def compute_risk_per_trade(settings, starting_balance):
    """Pure. A fixed-dollar override takes priority over the percentage
    if both are set (same "fixed overrides percent" convention as every
    other sizing layer in this codebase). risk_percent is validated to
    be >= MIN_RISK_PERCENT at save time (api/risk_engine_routes.py), not
    re-clamped here - this function trusts its input, same as every
    other pure function in core/capital_strategies.py."""
    if settings.get("risk_fixed_amount"):
        return round(settings["risk_fixed_amount"], 2)
    percent = settings.get("risk_percent") or DEFAULT_RISK_PERCENT
    return round(max(starting_balance, 0) * (percent / 100.0), 2)


def compute_profit_target(settings, starting_balance):
    """Pure. Fixed override takes priority, same convention as above."""
    if settings.get("profit_target_fixed_amount"):
        return round(settings["profit_target_fixed_amount"], 2)
    percent = settings.get("profit_target_percent") or DEFAULT_PROFIT_TARGET_PERCENT
    return round(max(starting_balance, 0) * (percent / 100.0), 2)


def compute_loss_limit(settings, starting_balance):
    """Pure. Fixed override takes priority, same convention as above.
    Always returned as a positive number (the magnitude of the limit) -
    callers compare realized_pnl <= -loss_limit, matching every other
    loss-limit check in this codebase (session_manager.check_session_
    limits, fund_manager.check_fund_limits, risk_manager.check_max_
    daily_loss)."""
    if settings.get("loss_limit_fixed_amount"):
        return round(settings["loss_limit_fixed_amount"], 2)
    percent = settings.get("loss_limit_percent") or DEFAULT_LOSS_LIMIT_PERCENT
    return round(max(starting_balance, 0) * (percent / 100.0), 2)


def check_should_stop(settings, starting_balance, realized_pnl_today, pending_stake_today=0.0):
    """Pure. Returns None (keep trading) or "profit_target"/"loss_limit"
    - which condition fired, checked in that order (profit before loss,
    matching every other dual profit/loss check in this codebase).
    Pending (still-open) trades placed today are folded in pessimistically
    for the loss-limit side only (assume they all lose, same reasoning as
    risk_manager.check_max_daily_loss/session_manager.check_session_limits)
    - never for the profit-target side, since an unresolved trade is not
    yet a win and must not be counted as one.

    stop_after_target/stop_after_loss_limit (both default True, real
    editable settings) let an operator track a threshold for display
    purposes only, without actually halting trading when it's crossed -
    an explicit opt-out, not a bug."""
    target = compute_profit_target(settings, starting_balance)
    if settings.get("stop_after_target", True) and target > 0 and realized_pnl_today >= target:
        return "profit_target"

    limit = compute_loss_limit(settings, starting_balance)
    if settings.get("stop_after_loss_limit", True) and limit > 0:
        effective_pnl = realized_pnl_today - pending_stake_today
        if effective_pnl <= -limit:
            return "loss_limit"

    return None


def vault_skim_on_target(settings, starting_balance, realized_pnl_before, realized_pnl_after):
    """Pure. Skims vault_percent_on_target% of today's realized P/L the
    moment (and only the moment) this trade's own close pushes the day's
    running total from below the profit target to at-or-above it - the
    caller (core/risk_engine.py's on_trade_closed) supplies the
    before/after values for just this one trade's contribution, so this
    never double-skims on a later check that only re-reads an
    already-crossed total."""
    if not settings.get("vault_enabled") or settings.get("vault_percent_on_target", 0) <= 0:
        return 0
    target = compute_profit_target(settings, starting_balance)
    if target <= 0:
        return 0
    if realized_pnl_before < target <= realized_pnl_after:
        return round(realized_pnl_after * (settings["vault_percent_on_target"] / 100.0), 2)
    return 0
