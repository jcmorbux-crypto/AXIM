import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = CORE_DIR.parent / "config"
LOG_DIR = CORE_DIR.parent / "logs"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
from settings import (
    ACCOUNT,
    MAX_TRADE_AMOUNT,
    MAX_TRADES_PER_HOUR,
    MAX_CONSECUTIVE_LOSSES,
    COOLDOWN_AFTER_LOSS_SECONDS,
    DUPLICATE_SIGNAL_WINDOW_SECONDS,
    MINIMUM_PAYOUT,
)

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class RiskViolation(Exception):
    def __init__(self, rule, reason):
        self.rule = rule
        self.reason = reason
        super().__init__(f"{rule}: {reason}")


def check_demo_only():
    if ACCOUNT.upper() != "DEMO":
        raise RiskViolation("demo_only", f"ACCOUNT is {ACCOUNT!r}, not DEMO - refusing to execute")


def check_max_trade_amount(amount):
    if amount > MAX_TRADE_AMOUNT:
        raise RiskViolation(
            "max_trade_amount",
            f"stake ${amount} exceeds MAX_TRADE_AMOUNT ${MAX_TRADE_AMOUNT}",
        )


def check_max_trades_per_hour():
    since = (datetime.now() - timedelta(hours=1)).isoformat()
    count = database.count_trades_since(since)
    if count >= MAX_TRADES_PER_HOUR:
        raise RiskViolation(
            "max_trades_per_hour",
            f"{count} trades in the last hour >= limit {MAX_TRADES_PER_HOUR}",
        )


def check_max_consecutive_losses():
    recent = database.get_recent_results(MAX_CONSECUTIVE_LOSSES)
    if len(recent) == MAX_CONSECUTIVE_LOSSES and all(r == "loss" for r in recent):
        raise RiskViolation(
            "max_consecutive_losses",
            f"last {MAX_CONSECUTIVE_LOSSES} trades were all losses",
        )


def check_cooldown_after_loss():
    last_loss = database.get_last_loss_time()
    if not last_loss:
        return
    elapsed = (datetime.now() - datetime.fromisoformat(last_loss)).total_seconds()
    if elapsed < COOLDOWN_AFTER_LOSS_SECONDS:
        remaining = COOLDOWN_AFTER_LOSS_SECONDS - elapsed
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
    if payout is None:
        raise RiskViolation(
            "minimum_payout",
            f"payout could not be read - refusing to execute without confirming it meets MINIMUM_PAYOUT {MINIMUM_PAYOUT}%",
        )
    if payout < MINIMUM_PAYOUT:
        raise RiskViolation(
            "minimum_payout",
            f"payout {payout}% is below MINIMUM_PAYOUT {MINIMUM_PAYOUT}%",
        )


def check_duplicate_signal(asset, direction, expiry, exclude_id=None):
    duplicate_id = database.find_recent_duplicate(
        asset, direction, expiry, DUPLICATE_SIGNAL_WINDOW_SECONDS, exclude_id=exclude_id,
    )
    if duplicate_id is not None:
        raise RiskViolation(
            "duplicate_signal",
            f"identical signal (asset={asset}, direction={direction}, expiry={expiry}) "
            f"already recorded as trade id {duplicate_id} within {DUPLICATE_SIGNAL_WINDOW_SECONDS}s",
        )


def evaluate_all(asset, direction, expiry, amount, exclude_id=None):
    checks = [
        lambda: check_demo_only(),
        lambda: check_duplicate_signal(asset, direction, expiry, exclude_id=exclude_id),
        lambda: check_max_trade_amount(amount),
        lambda: check_max_trades_per_hour(),
        lambda: check_max_consecutive_losses(),
        lambda: check_cooldown_after_loss(),
    ]
    for check in checks:
        check()
    logger.info(
        "risk_manager: all checks passed for asset=%r direction=%r expiry=%r amount=%r",
        asset, direction, expiry, amount,
    )
