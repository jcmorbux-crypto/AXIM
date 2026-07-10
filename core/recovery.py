import asyncio
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
EXECUTION_DIR = PROJECT_ROOT / "execution"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(EXECUTION_DIR))

import database
from trade_lifecycle import TradeStatus
from logger import get_logger

import pocket_dom
import pocket_executor

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


async def resume_pending_trades(warmup_service, broker_account_id=None):
    """
    Called once per browser context (at process startup for the legacy
    default connection, and once per broker account the first time it's
    lazily built - see core/broker_account_manager.py), after that
    context's worker pool is up. Any trade left in trade_clicked/
    trade_opened when the process last stopped had a real position opened
    on Pocket Option that was never resolved in our database - re-attach
    outcome tracking to it (via warmup_service's own dedicated page, not a
    placement worker - see pocket_executor.track_outcome) for the
    remaining time until expiry.

    broker_account_id scopes which trades this call looks at - resuming a
    trade under the wrong account's browser context would check the wrong
    Pocket Option account's Closed-trades list entirely. None (default)
    means the legacy single-connection behavior, unscoped.

    A trade stuck at trade_prepared was never clicked (either ARMED was
    false or the process died before the click), so there is no real
    position to reconcile - it's marked abandoned rather than "resumed"
    (handled separately, once, by mark_abandoned_preparations() - not
    scoped per account, since it never touches a browser).
    """
    open_trades = database.get_open_trades(broker_account_id=broker_account_id)
    if not open_trades:
        logger.info("recovery: no pending trades to resume")
        return

    logger.info("recovery: found %d pending trade(s) to resume", len(open_trades))

    for row in open_trades:
        trade_id = row["id"]
        try:
            resumed = await _resume_one(row, warmup_service)
        except Exception as e:
            logger.error("recovery: trade_id=%s failed to resume: %s", trade_id, e)
            database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:recovery_failed:{e}")
            database.record_recovery_event("resume_open_trade", "failed", f"trade_id={trade_id}: {e}")
        else:
            if resumed:
                database.record_recovery_event("resume_open_trade", "succeeded", f"trade_id={trade_id}")
            else:
                # Not a resume attempt at all - get_open_trades() only
                # returns trade_clicked/trade_opened rows, so a missing
                # opened_at here is an unexpected data inconsistency (not
                # the normal "never clicked" case, which mark_abandoned_
                # preparations() already handles separately) - worth
                # counting as a failed recovery, since ideally this trade
                # would have been resumable.
                database.record_recovery_event("resume_open_trade", "failed", f"trade_id={trade_id}: missing opened_at")


async def _resume_one(row, warmup_service):
    """Returns True if outcome tracking was actually re-attached, False if
    this row couldn't be resumed (missing opened_at)."""
    trade_id = row["id"]
    asset = row["asset"]
    direction = row["direction"]
    expiry = row["timeframe"]
    opened_at_raw = row["opened_at"]

    if not opened_at_raw:
        logger.warning("recovery: trade_id=%s has no opened_at - marking abandoned", trade_id)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:abandoned_on_restart")
        return False

    opened_at = datetime.fromisoformat(opened_at_raw)
    total_expiry_seconds = pocket_dom.expiry_to_seconds(expiry)
    elapsed = (datetime.now() - opened_at).total_seconds()
    remaining = max(0, total_expiry_seconds - elapsed)

    logger.info(
        "recovery: trade_id=%s asset=%r opened_at=%s elapsed=%.1fs remaining=%.1fs",
        trade_id, asset, opened_at_raw, elapsed, remaining,
    )

    # track_outcome reads outcomes from warmup_service's own dedicated
    # (otherwise-idle) page, never touching the placement worker pool.
    asyncio.create_task(
        pocket_executor.track_outcome(warmup_service, trade_id, remaining, asset=asset, direction=direction)
    )
    return True


def mark_abandoned_preparations():
    """A trade stuck at trade_prepared (never clicked) across a restart has
    no real position to reconcile - close it out cleanly for audit purposes."""
    conn = database.get_connection()
    rows = conn.execute(
        "SELECT id FROM signals WHERE execution_status = ?",
        (TradeStatus.TRADE_PREPARED.value,),
    ).fetchall()
    conn.close()

    for row in rows:
        trade_id = row["id"]
        logger.info("recovery: trade_id=%s abandoned at trade_prepared - no position to resume", trade_id)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:abandoned_on_restart")


async def run_recovery(warmup_service, broker_account_id=None, skip_abandoned_pass=False):
    """Single entry point for startup: reconcile abandoned preparations,
    then resume tracking for any genuinely open positions. Must be called
    after the worker pool and warmup_service are both up.

    skip_abandoned_pass=True for a per-account lazy-build call (see
    core/broker_account_manager.py) - mark_abandoned_preparations() scans
    every trade_prepared row process-wide and never touches a browser, so
    it only needs to run once, at real process startup, not redundantly
    for every account built after that."""
    if not skip_abandoned_pass:
        mark_abandoned_preparations()
    await resume_pending_trades(warmup_service, broker_account_id=broker_account_id)
