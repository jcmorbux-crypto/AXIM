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


class RiskViolation(Exception):
    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def check_demo_only():
    if ACCOUNT.upper() != "DEMO":
        raise RiskViolation("demo_only", f"ACCOUNT is {ACCOUNT!r}, not DEMO - refusing to execute")


def check_max_trade_amount(amount):
    limit = _setting("max_trade_amount", MAX_TRADE_AMOUNT)
    if amount > limit:
        raise RiskViolation(
            "max_trade_amount",
            f"stake ${amount} exceeds MAX_TRADE_AMOUNT ${limit}",
        )


def check_max_trades_per_hour():
    limit = _setting("max_trades_per_hour", MAX_TRADES_PER_HOUR)
    since = (datetime.now() - timedelta(hours=1)).isoformat()
    count = database.count_trades_since(since)
    if count >= limit:
        raise RiskViolation(
            "max_trades_per_hour",
            f"{count} trades in the last hour >= limit {limit}",
        )


def check_max_trades_per_day():
    """A coarser, complementary cap to check_max_trades_per_hour - off by
    default (0), since it's a brand-new concept this project didn't have
    before the UI, and MAX_TRADES_PER_HOUR already provides real
    rate-limiting; this only activates once the operator sets a real value
    via the UI."""
    limit = _setting("max_trades_per_day", 0)
    if limit <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    count = database.count_trades_since(midnight)
    if count >= limit:
        raise RiskViolation(
            "max_trades_per_day",
            f"{count} trades today >= limit {limit}",
        )


def check_max_consecutive_losses():
    limit = _setting("max_consecutive_losses", MAX_CONSECUTIVE_LOSSES)
    recent = database.get_recent_results(limit)
    if len(recent) == limit and all(r == "loss" for r in recent):
        raise RiskViolation(
            "max_consecutive_losses",
            f"last {limit} trades were all losses",
        )


def check_cooldown_after_loss():
    limit = _setting("cooldown_after_loss_seconds", COOLDOWN_AFTER_LOSS_SECONDS)
    last_loss = database.get_last_loss_time()
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


def check_max_daily_loss():
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
    real active threshold rather than 0)."""
    limit = _setting("max_daily_loss", MAX_DAILY_LOSS)
    if limit <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    realized_pnl = database.get_realized_pnl_since(midnight)
    if realized_pnl <= -limit:
        raise RiskViolation(
            "max_daily_loss",
            f"realized P/L today is ${realized_pnl:.2f}, at or beyond -MAX_DAILY_LOSS ${limit:.2f}",
        )


def check_daily_profit_target():
    """The upside mirror of check_max_daily_loss - stop trading for the
    day once a profit TARGET has been reached, not just a loss limit. Off
    by default (0): a target is inherently a discretionary choice (unlike
    a loss limit, there's no safety argument for a nonzero default), so
    this only activates once the operator sets a real value via the UI."""
    target = _setting("daily_profit_target", 0)
    if target <= 0:
        return
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    realized_pnl = database.get_realized_pnl_since(midnight)
    if realized_pnl >= target:
        raise RiskViolation(
            "daily_profit_target",
            f"realized P/L today is ${realized_pnl:.2f}, at or beyond the ${target:.2f} daily profit target",
        )


def check_duplicate_signal(asset, direction, expiry, exclude_id=None):
    window = _setting("duplicate_signal_window_seconds", DUPLICATE_SIGNAL_WINDOW_SECONDS)
    duplicate_id = database.find_recent_duplicate(
        asset, direction, expiry, window, exclude_id=exclude_id,
    )
    if duplicate_id is not None:
        raise RiskViolation(
            "duplicate_signal",
            f"identical signal (asset={asset}, direction={direction}, expiry={expiry}) "
            f"already recorded as trade id {duplicate_id} within {window}s",
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


def evaluate_all(asset, direction, expiry, amount, exclude_id=None):
    checks = [
        lambda: check_demo_only(),
        lambda: check_duplicate_signal(asset, direction, expiry, exclude_id=exclude_id),
        lambda: check_max_trade_amount(amount),
        lambda: check_max_trades_per_hour(),
        lambda: check_max_trades_per_day(),
        lambda: check_max_consecutive_losses(),
        lambda: check_cooldown_after_loss(),
        lambda: check_max_daily_loss(),
        lambda: check_daily_profit_target(),
    ]
    for check in checks:
        check()
    logger.info(
        "risk_manager: all checks passed for asset=%r direction=%r expiry=%r amount=%r",
        asset, direction, expiry, amount,
    )
