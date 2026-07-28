import sys
from datetime import datetime, timedelta
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = CORE_DIR.parent / "config"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
from logger import get_logger
from settings import (
    ACCOUNT,
    MAX_TRADE_AMOUNT,
    MAX_TRADES_PER_HOUR,
    MAX_CONSECUTIVE_LOSSES,
    COOLDOWN_AFTER_LOSS_SECONDS,
    DUPLICATE_SIGNAL_WINDOW_SECONDS,
    MINIMUM_PAYOUT,
    MAX_DAILY_LOSS,
)

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def _setting(key, static_default):
    """Reads a UI-editable value (ui_settings, set via the web UI/API)
    with the static .env-derived config/settings.py constant as the
    fallback - so a setting never touched in the UI behaves exactly as it
    did before the UI existed, and a value the operator DID set there
    takes effect on the very next signal, no restart required."""
    return database.get_setting(key, default=static_default)


# Maps each per-account-overridable safety-limit setting key to its
# broker_accounts override column (2026-07-25 Execution Reliability
# directive: "Independent limits for each broker account").
_ACCOUNT_OVERRIDE_COLUMNS = {
    "max_consecutive_losses": "max_consecutive_losses_override",
    "cooldown_after_loss_seconds": "cooldown_after_loss_seconds_override",
    "max_daily_loss": "max_daily_loss_override",
    "daily_profit_target": "daily_profit_target_override",
    "max_trades_per_hour": "max_trades_per_hour_override",
    "max_trades_per_day": "max_trades_per_day_override",
}


def _account_setting(broker_account_id, key, static_default):
    """3-tier resolution for a per-broker-account-overridable safety
    limit: this account's own explicit override
    (broker_accounts.<key>_override), if set -> the shared global UI
    setting -> the static .env-derived default. An account with no
    override configured behaves exactly like the shared-limit behavior
    every check already had before per-account overrides existed - only
    an operator explicitly setting one changes anything for that
    specific account, never silently for every account at once."""
    if broker_account_id is not None:
        column = _ACCOUNT_OVERRIDE_COLUMNS[key]
        account = database.get_broker_account(broker_account_id)
        if account is not None and account[column] is not None:
            return account[column]
    return _setting(key, static_default)


_ACCOUNT_LIMIT_STATIC_DEFAULTS = {
    "max_consecutive_losses": lambda: MAX_CONSECUTIVE_LOSSES,
    "cooldown_after_loss_seconds": lambda: COOLDOWN_AFTER_LOSS_SECONDS,
    "max_daily_loss": lambda: MAX_DAILY_LOSS,
    "daily_profit_target": lambda: 0,
    "max_trades_per_hour": lambda: MAX_TRADES_PER_HOUR,
    "max_trades_per_day": lambda: 0,
}


def get_effective_limits(broker_account_id=None):
    """Every per-account-overridable safety limit's resolved value for
    this account (its own override if set, else the shared global
    setting, else the static default) - the API/UI's one source of
    truth for "what will actually apply here", so a settings page never
    re-implements _account_setting's 3-tier resolution itself and risks
    drifting out of sync with what real signals are actually checked
    against."""
    return {
        key: _account_setting(broker_account_id, key, default_fn())
        for key, default_fn in _ACCOUNT_LIMIT_STATIC_DEFAULTS.items()
    }


class RiskViolation(Exception):
    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def check_demo_only():
    if ACCOUNT.upper() != "DEMO":
        raise RiskViolation("demo_only", f"ACCOUNT is {ACCOUNT!r}, not DEMO - refusing to execute")


def check_not_stopped(broker_account_id=None):
    """core/telegram_listener.py's handler already checks emergency_stop
    before EVER calling trade_coordinator.handle_signal() for a brand-new
    incoming message - but nothing re-checked it once a signal was
    already inside the pipeline. A signal that entered before Emergency
    Stop was pressed could sit for a real amount of time (waiting on a
    require_confirmation session's human approval, or a busy worker pool)
    and, without this check, would sail through every other risk check
    (none of which look at control state) and reach real execution after
    the operator had already hit Stop. Called first in trade_coordinator's
    preflight (before any other check) AND re-checked immediately before
    worker-pool acquisition, the two points separated by the pipeline's
    only genuinely long, unbounded waits.

    broker_account_id, when this signal is routed to a specific multi-
    broker-account Fund, ALSO checks that account's own Emergency Stop -
    independent of the global one, so a problem on one account can be
    halted without forcing every other account's Fund to stop too, and
    the global Emergency Stop still halts everything regardless of which
    account a signal is routed to."""
    state = database.get_control_state()
    if state.get("emergency_stop"):
        raise RiskViolation("emergency_stop", "emergency stop is active - refusing to execute")
    if state.get("paused"):
        raise RiskViolation("paused", "trading is paused - refusing to execute")
    if broker_account_id is not None:
        account = database.get_broker_account(broker_account_id)
        if account is not None and account["emergency_stopped"]:
            raise RiskViolation(
                "account_emergency_stop",
                f"emergency stop is active on broker account {account['name']!r} - refusing to execute",
            )


def check_max_trade_amount(amount):
    limit = _setting("max_trade_amount", MAX_TRADE_AMOUNT)
    if amount > limit:
        raise RiskViolation(
            "max_trade_amount",
            f"stake ${amount} exceeds MAX_TRADE_AMOUNT ${limit}",
        )


def check_max_trades_per_hour(broker_account_id=None):
    """broker_account_id, when this signal is routed to a specific multi-
    broker-account Fund, scopes the count to just that account - each
    account gets its own independent quota, rather than every account's
    trades counting against one shared global bucket (previously a busy
    account could silently exhaust the quota for every other account
    too). Also resolves this account's own limit OVERRIDE if one is set
    (2026-07-25: "Independent limits for each broker account") - falling
    back to the shared configured limit otherwise. session_id=None's
    legacy single-shared-connection path has no account to scope to, so
    it keeps counting globally against the shared limit, unchanged."""
    limit = _account_setting(broker_account_id, "max_trades_per_hour", MAX_TRADES_PER_HOUR)
    since = (datetime.now() - timedelta(hours=1)).isoformat()
    count = database.count_trades_since(since, broker_account_id=broker_account_id)
    if count >= limit:
        raise RiskViolation(
            "max_trades_per_hour",
            f"{count} trades in the last hour >= limit {limit}",
        )


def check_max_trades_per_day(broker_account_id=None):
    """A coarser, complementary cap to check_max_trades_per_hour - off by
    default (0), since it's a brand-new concept this project didn't have
    before the UI, and MAX_TRADES_PER_HOUR already provides real
    rate-limiting; this only activates once the operator sets a real value
    via the UI (globally or for this specific account). broker_account_id
    scopes the count the same way check_max_trades_per_hour does - see
    its own docstring."""
    limit = _account_setting(broker_account_id, "max_trades_per_day", 0)
    if limit <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    count = database.count_trades_since(midnight, broker_account_id=broker_account_id)
    if count >= limit:
        raise RiskViolation(
            "max_trades_per_day",
            f"{count} trades today >= limit {limit}",
        )


def _consecutive_loss_reset_key(broker_account_id):
    """2026-07-25 Execution Reliability directive: per-broker-account
    safety hierarchy - each account gets its own independent reset
    marker, keyed by account, so recovering one account's lock can never
    silently also clear (or leave untouched) a different account's real
    streak. None keeps the legacy global/unscoped key, for callers with
    no specific account routing."""
    return "consecutive_loss_reset_at" if broker_account_id is None else f"consecutive_loss_reset_at:{broker_account_id}"


def check_max_consecutive_losses(broker_account_id=None):
    """Pessimistic: every currently-open (placed, unresolved) trade is
    treated as a hypothetical loss extending the current streak - a burst
    of signals arriving within one expiry window would otherwise all read
    the same closed-only get_recent_results and could all pass this check
    before any of them resolve (execution/pocket_executor.py's
    track_outcome docstring: MAX_CONCURRENT_WORKERS bounds simultaneous
    placements, not simultaneous open positions).

    broker_account_id (2026-07-25 Execution Reliability directive: the
    per-broker-account safety hierarchy) scopes this to just that
    account - a losing streak on a different account must never lock
    this one, matching check_max_trades_per_hour/_per_day's own
    established per-account scoping. Also resolves this account's own
    limit OVERRIDE if one is set. None keeps the legacy global/unscoped
    behavior for the single-shared-connection path."""
    limit = _account_setting(broker_account_id, "max_consecutive_losses", MAX_CONSECUTIVE_LOSSES)
    pending_count = database.count_pending_trades(broker_account_id=broker_account_id)
    if pending_count >= limit:
        raise RiskViolation(
            "max_consecutive_losses",
            f"{pending_count} trade(s) currently open - if they all lose, that alone reaches the limit of {limit} consecutive losses",
        )
    remaining = limit - pending_count
    reset_at = database.get_setting(_consecutive_loss_reset_key(broker_account_id), default=None)
    recent = database.get_recent_results(remaining, broker_account_id=broker_account_id, since=reset_at)
    if len(recent) == remaining and all(r == "loss" for r in recent):
        raise RiskViolation(
            "max_consecutive_losses",
            f"last {remaining} closed trades were all losses, plus {pending_count} more currently open - "
            f"would reach the limit of {limit} if they also lose",
        )


def reset_consecutive_loss_lock(reset_by, reason, broker_account_id=None):
    """The explicit, logged, reversible recovery action a hard
    consecutive-loss lock needs (2026-07-19 product-design directive): a
    lock that can ONLY clear via a future winning trade is unrecoverable
    by construction, since the lock itself blocks every further trade,
    win or lose. Does NOT erase or falsify the real loss streak - trades
    before this moment stay exactly as they happened, they just stop
    counting toward a NEW streak going forward. Distinct from silently
    raising max_consecutive_losses (which would also let a WORSE streak
    through) - this only affects the window boundary, the limit itself
    is untouched.

    broker_account_id (2026-07-25 Execution Reliability directive: the
    per-broker-account safety hierarchy) scopes the reset to just that
    account's own lock - recovering one account never touches a
    different account's real streak.

    reason (2026-07-27 audit-hardening directive, prompted by using this
    exact action to unblock a controlled Demo latency-validation
    campaign): this overrides a live safety mechanism, not a routine
    settings tweak - reset_by alone answers "who" but not "why", so a
    non-empty reason is required on every call, same as
    provider_profile.revert_to_observation's own reason requirement.
    Every invocation is logged with both, plus the streak length being
    cleared, for a complete audit trail."""
    if not reset_by:
        raise ValueError("reset_consecutive_loss_lock requires reset_by - this is a real, attributable decision")
    if not reason or not reason.strip():
        raise ValueError("reset_consecutive_loss_lock requires a non-empty reason - this is a real, attributable decision")
    previous_streak_count = 0
    for result in database.get_recent_results(50, broker_account_id=broker_account_id):
        if result != "loss":
            break
        previous_streak_count += 1
    reset_at = datetime.now().isoformat()
    database.set_setting(_consecutive_loss_reset_key(broker_account_id), reset_at,
                          changed_by=reset_by, reason=reason, source="consecutive_loss_lock_reset")
    logger.warning(
        "risk_manager: consecutive-loss lock reset by %s at %s (broker_account_id=%s, reason=%s, "
        "previous_streak_count=%d, new_streak_count=0)",
        reset_by, reset_at, broker_account_id, reason, previous_streak_count,
    )
    return {
        "reset_at": reset_at, "reset_by": reset_by, "reason": reason, "broker_account_id": broker_account_id,
        "previous_streak_count": previous_streak_count, "new_streak_count": 0,
    }


def check_cooldown_after_loss(broker_account_id=None, series_id=None):
    """broker_account_id (2026-07-25 Execution Reliability directive: the
    per-broker-account safety hierarchy) scopes the cooldown to just that
    account - a loss on a different account must never trigger a
    cooldown here. Also resolves this account's own limit OVERRIDE if
    one is set. None keeps the legacy global/unscoped behavior.

    series_id (2026-07-27 Martin Trader next-five-minute-boundary
    redesign, explicit product requirement: "The ordinary cooldown-after-
    loss rule must not delay the next authorized Martin Trader series
    entry") - a retry within an ACTIVE Martin Trader series is already a
    pre-authorized, deliberate re-entry (core/trade_series_engine.py's
    own state machine is what decided to fire it, immediately after
    seeing this exact account's own prior loss), not a fresh, independent
    signal the cooldown is meant to guard against. Only ever non-None for
    that one caller (core/trade_series_engine.py._execute_entry) - every
    other signal's cooldown behavior is completely unchanged."""
    if series_id is not None:
        return
    limit = _account_setting(broker_account_id, "cooldown_after_loss_seconds", COOLDOWN_AFTER_LOSS_SECONDS)
    last_loss = database.get_last_loss_time(broker_account_id=broker_account_id)
    if not last_loss:
        return
    elapsed = (datetime.now() - datetime.fromisoformat(last_loss)).total_seconds()
    if elapsed < limit:
        remaining = limit - elapsed
        raise RiskViolation(
            "cooldown_after_loss",
            f"{remaining:.0f}s remaining in post-loss cooldown",
        )


def check_minimum_payout(payout):
    """Unlike the other rules, payout is only known after the browser has
    already selected the asset/expiry and read it live (pocket_dom.
    read_payout_percent) - it can't be pre-checked from signal/DB data
    alone, and a cached value would go stale (payout fluctuates
    continuously, unlike "is this asset tradeable"). Called from
    pocket_executor.prepare_trade right after that live read, not from
    TradeCoordinator's pre-flight stage with the other checks.

    A missing reading (payout=None, e.g. the DOM read itself failed) fails
    closed - rejected, not silently allowed - consistent with every other
    safety check in this codebase (demo-mode verification, asset-
    untradeable check, WATCH_CHANNELS enforcement all fail closed too)."""
    limit = _setting("minimum_payout", MINIMUM_PAYOUT)
    if payout is None:
        raise RiskViolation(
            "minimum_payout",
            f"payout could not be read - refusing to execute without confirming it meets MINIMUM_PAYOUT {limit}%",
        )
    if payout < limit:
        raise RiskViolation(
            "minimum_payout",
            f"payout {payout}% is below MINIMUM_PAYOUT {limit}%",
        )


def check_max_daily_loss(broker_account_id=None):
    """Drawdown circuit breaker - flagged as a genuine gap in
    docs/AXIM_LIVE_READINESS_REVIEW.md. check_max_consecutive_losses only
    catches an unbroken losing STREAK; it never trips on a steady bleed-out
    through an alternating win/loss pattern, which is exactly what a
    no-edge binary-options payout structure (paying out less than 100% on
    a win) produces on average over time. This checks realized net P/L
    since local midnight instead - a genuinely different signal, not a
    restatement of the consecutive-losses rule.

    limit <= 0 disables the check (an operator's explicit choice, not a
    default - see settings.py's own comment on why the static default is a
    real active threshold rather than 0).

    broker_account_id (2026-07-25 Execution Reliability directive: the
    per-broker-account safety hierarchy) scopes this to just that
    account's own day - a bad day on a different account must never
    pause this one. Also resolves this account's own limit OVERRIDE if
    one is set. This is the "Broker Account Safety" tier check; see
    check_global_daily_loss for the separate, optional "Global Safety"
    tier counterpart (deliberately never account-overridable - it's the
    one combined-across-every-account backstop)."""
    limit = _account_setting(broker_account_id, "max_daily_loss", MAX_DAILY_LOSS)
    if limit <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    realized_pnl = database.get_realized_pnl_since(midnight, broker_account_id=broker_account_id)
    # Pessimistic: every currently-open trade placed today is treated as
    # a worst-case loss of its full stake - same reasoning as
    # check_max_consecutive_losses above.
    pending_stake = database.get_pending_stake_since(midnight, broker_account_id=broker_account_id)
    effective_pnl = realized_pnl - pending_stake
    if effective_pnl <= -limit:
        raise RiskViolation(
            "max_daily_loss",
            f"realized P/L today is ${realized_pnl:.2f} with ${pending_stake:.2f} at risk in open trades - "
            f"would be at or beyond -MAX_DAILY_LOSS ${limit:.2f} if they all lose",
        )


def check_global_daily_loss():
    """The "Global Safety" tier counterpart of check_max_daily_loss's
    per-account "Broker Account Safety" tier check (2026-07-25 Execution
    Reliability directive: the permanent per-broker-account safety
    hierarchy - Global Emergency Stop -> Broker Account limits -> Fund
    limits -> Provider limits -> execution). Off by default (0) - an
    operator's explicit, optional choice to ALSO cap realized P/L across
    every broker account combined, independent of and in addition to each
    account's own per-account limit. Deliberately a separate setting
    (global_max_daily_loss) from max_daily_loss, so enabling one never
    silently changes the meaning of the other."""
    limit = _setting("global_max_daily_loss", 0)
    if limit <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    realized_pnl = database.get_realized_pnl_since(midnight)
    pending_stake = database.get_pending_stake_since(midnight)
    effective_pnl = realized_pnl - pending_stake
    if effective_pnl <= -limit:
        raise RiskViolation(
            "global_max_daily_loss",
            f"realized P/L today across all broker accounts is ${realized_pnl:.2f} with ${pending_stake:.2f} "
            f"at risk in open trades - would be at or beyond -GLOBAL_MAX_DAILY_LOSS ${limit:.2f} if they all lose",
        )


def check_daily_profit_target(broker_account_id=None):
    """The upside mirror of check_max_daily_loss - stop trading for the
    day once a profit TARGET has been reached, not just a loss limit. Off
    by default (0): a target is inherently a discretionary choice (unlike
    a loss limit, there's no safety argument for a nonzero default), so
    this only activates once the operator sets a real value via the UI.

    broker_account_id (2026-07-25 Execution Reliability directive: the
    per-broker-account safety hierarchy) scopes this to just that
    account's own day, matching check_max_daily_loss. Also resolves this
    account's own limit OVERRIDE if one is set."""
    target = _account_setting(broker_account_id, "daily_profit_target", 0)
    if target <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    realized_pnl = database.get_realized_pnl_since(midnight, broker_account_id=broker_account_id)
    if realized_pnl >= target:
        raise RiskViolation(
            "daily_profit_target",
            f"realized P/L today is ${realized_pnl:.2f}, at or beyond the ${target:.2f} daily profit target",
        )


def check_duplicate_signal(asset, direction, expiry, exclude_id=None, channel_id=None, series_id=None):
    """series_id (2026-07-27 Martin Trader next-five-minute-boundary
    redesign, explicit product requirement: "The normal duplicate
    detector must recognize that Entry #1, #2, #3, and #4 are separate
    authorized entries inside the same series") - every retry within one
    Martin Trader series deliberately shares the same asset/direction/
    expiry (the whole point of a retry), so the ordinary same-signal
    dedup window would otherwise misclassify entry #2/#3/#4 as a
    duplicate of #1. core/trade_series_engine.py's own idempotency check
    (one series per real Telegram message, via get_trade_series_by_message)
    is what actually guards against a genuine duplicate delivery here -
    this check is redundant, and wrong, for an already-authorized
    same-series retry. Only ever non-None for that one caller; every
    other signal's duplicate protection is completely unchanged."""
    if series_id is not None:
        return
    window = _setting("duplicate_signal_window_seconds", DUPLICATE_SIGNAL_WINDOW_SECONDS)
    duplicate_id = database.find_recent_duplicate(
        asset, direction, expiry, window, exclude_id=exclude_id, channel_id=channel_id,
    )
    if duplicate_id is not None:
        raise RiskViolation(
            "duplicate_signal",
            f"identical signal (asset={asset}, direction={direction}, expiry={expiry}) "
            f"already recorded as trade id {duplicate_id} within {window}s on the same source",
        )


def compute_trade_amount(static_default_amount):
    """Position sizing: either a flat amount (trade_sizing_mode="fixed",
    the default - reads the "fixed_trade_amount" UI setting, falling back
    to the static TRADE_AMOUNT the caller passes in) or a percentage of
    current bankroll (trade_sizing_mode="percent"), where current bankroll
    = starting_bankroll (a UI setting, no static equivalent - defaults to
    0) + lifetime realized P/L. Percent mode with no starting_bankroll
    configured resolves to a bankroll of 0 - falls back to the fixed
    amount rather than trading $0, since a $0 stake is never a sensible
    real answer to "how much should this trade be."."""
    mode = _setting("trade_sizing_mode", "fixed")
    fixed_amount = _setting("fixed_trade_amount", static_default_amount)
    if mode != "percent":
        return fixed_amount

    starting_bankroll = _setting("starting_bankroll", 0)
    percent = _setting("trade_sizing_percent", 1.0)
    bankroll = starting_bankroll + database.get_lifetime_realized_pnl()
    if bankroll <= 0:
        return fixed_amount
    return round(bankroll * (percent / 100.0), 2)


def diagnose_settings(effective):
    """Config validation for the Settings screen - every finding here is
    traced against the exact enforcement code above, not a generic
    "looks risky" heuristic, so each message states precisely what would
    happen on the next signal. `effective` is the {key: value} dict
    api/main.py's GET /api/settings already computes (UI override, else
    the static .env-derived default) - this is a pure function over that,
    no DB access of its own.

    severity: "critical" (would silently block ALL trading), "warning"
    (a real but narrower misconfiguration), "info" (an override that's
    currently being ignored by the active mode - not wrong, just inert)."""
    findings = []

    def add(severity, key, message):
        findings.append({"severity": severity, "key": key, "message": message})

    mode = effective.get("trade_sizing_mode")
    fixed_amount = effective.get("fixed_trade_amount")
    max_amount = effective.get("max_trade_amount")
    percent = effective.get("trade_sizing_percent")

    if mode == "percent":
        add("info", "fixed_trade_amount",
            "Trade sizing mode is \"percent\" - fixed_trade_amount only takes effect as a fallback "
            "when starting_bankroll + lifetime P/L is $0 or negative.")
        if percent is not None and percent <= 0:
            add("critical", "trade_sizing_percent",
                f"trade_sizing_percent is {percent}% with a positive bankroll - every trade would be sized "
                "at $0 or less, which check_max_trade_amount and the broker will both reject.")
    else:
        add("info", "trade_sizing_percent",
            "Trade sizing mode is \"fixed\" (or unset) - trade_sizing_percent and starting_bankroll are "
            "stored but not used for position sizing until mode is switched to \"percent\".")
        if fixed_amount is not None and max_amount is not None and fixed_amount > max_amount:
            add("critical", "fixed_trade_amount",
                f"fixed_trade_amount (${fixed_amount}) exceeds max_trade_amount (${max_amount}) - "
                "check_max_trade_amount would reject every trade before it ever reaches the broker.")

    if max_amount is not None and max_amount <= 0:
        add("critical", "max_trade_amount",
            f"max_trade_amount is ${max_amount} - since a trade stake must be positive, every trade would "
            "be rejected by check_max_trade_amount.")

    max_per_hour = effective.get("max_trades_per_hour")
    if max_per_hour is not None and max_per_hour <= 0:
        add("critical", "max_trades_per_hour",
            f"max_trades_per_hour is {max_per_hour} - unlike max_trades_per_day, this limit has no "
            "\"0 = disabled\" case, so it would block every trade immediately.")

    max_losses = effective.get("max_consecutive_losses")
    if max_losses is not None and max_losses <= 0:
        add("critical", "max_consecutive_losses",
            f"max_consecutive_losses is {max_losses} - check_max_consecutive_losses treats 0 currently-open "
            "trades as already at or past this limit, so it would hard-lock trading immediately.")

    payout = effective.get("minimum_payout")
    if payout is not None and payout > 100:
        add("critical", "minimum_payout",
            f"minimum_payout is {payout}% - no real Pocket Option payout can ever reach above 100%, so "
            "check_minimum_payout would skip every trade.")
    elif payout is not None and payout < 0:
        add("warning", "minimum_payout",
            f"minimum_payout is {payout}%, below 0 - effectively the same as no minimum at all.")

    cooldown = effective.get("cooldown_after_loss_seconds")
    if cooldown is not None and cooldown < 0:
        add("warning", "cooldown_after_loss_seconds",
            f"cooldown_after_loss_seconds is {cooldown} - a negative cooldown never blocks, same as 0.")

    dup_window = effective.get("duplicate_signal_window_seconds")
    if dup_window is not None and dup_window < 0:
        add("warning", "duplicate_signal_window_seconds",
            f"duplicate_signal_window_seconds is {dup_window} - a negative window can never match a "
            "duplicate, same as 0 (duplicate detection effectively off).")

    return findings


def evaluate_all(asset, direction, expiry, amount, exclude_id=None, broker_account_id=None, channel_id=None):
    """Convenience bundle of every check trade_coordinator.py's
    _run_preflight_checks also runs individually (that stage-timed path
    is what real signals actually go through - this exists as one
    simple, well-tested entry point for anything else that needs the
    full check set, e.g. a manual test signal). broker_account_id/
    channel_id (2026-07-25 Execution Reliability directive: the
    permanent per-broker-account safety hierarchy) are threaded through
    to every check that needs them, so a caller here gets the exact same
    per-account isolation and per-channel duplicate scoping real signals
    get - not a stale, unscoped shortcut a future caller could
    accidentally rely on instead of the real preflight path."""
    checks = [
        lambda: check_not_stopped(broker_account_id),
        lambda: check_global_daily_loss(),
        lambda: check_demo_only(),
        lambda: check_duplicate_signal(asset, direction, expiry, exclude_id=exclude_id, channel_id=channel_id),
        lambda: check_max_trade_amount(amount),
        lambda: check_max_trades_per_hour(broker_account_id),
        lambda: check_max_trades_per_day(broker_account_id),
        lambda: check_max_consecutive_losses(broker_account_id),
        lambda: check_cooldown_after_loss(broker_account_id),
        lambda: check_max_daily_loss(broker_account_id),
        lambda: check_daily_profit_target(broker_account_id),
    ]
    for check in checks:
        check()
    logger.info(
        "risk_manager: all checks passed for asset=%r direction=%r expiry=%r amount=%r",
        asset, direction, expiry, amount,
    )


def get_account_safety_status(broker_account_id=None):
    """Real, live status for every per-account safety control - calls
    the exact same check functions real signals go through (never a
    re-implementation), so a settings page can never show "not locked"
    while the next real signal would actually be rejected, or vice
    versa. broker_account_id=None reports the legacy global/unscoped
    status, matching api/main.py's GET /api/settings."""
    limits = get_effective_limits(broker_account_id)

    def _active(check_fn):
        try:
            check_fn()
            return False
        except RiskViolation:
            return True

    since_hour = (datetime.now() - timedelta(hours=1)).isoformat()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    last_loss = database.get_last_loss_time(broker_account_id=broker_account_id)
    cooldown_remaining = None
    if last_loss and limits["cooldown_after_loss_seconds"] > 0:
        elapsed = (datetime.now() - datetime.fromisoformat(last_loss)).total_seconds()
        remaining = limits["cooldown_after_loss_seconds"] - elapsed
        if remaining > 0:
            cooldown_remaining = remaining

    return {
        "effective_limits": limits,
        "consecutive_loss_lock_active": _active(lambda: check_max_consecutive_losses(broker_account_id)),
        "cooldown_remaining_seconds": cooldown_remaining,
        "max_daily_loss_active": _active(lambda: check_max_daily_loss(broker_account_id)),
        "daily_profit_target_reached": _active(lambda: check_daily_profit_target(broker_account_id)),
        "trades_this_hour": database.count_trades_since(since_hour, broker_account_id=broker_account_id),
        "trades_today": database.count_trades_since(midnight, broker_account_id=broker_account_id),
        "daily_realized_pnl": database.get_realized_pnl_since(midnight, broker_account_id=broker_account_id),
    }
