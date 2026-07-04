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


async def resume_pending_trades(pool):
    """
    Called once at startup, after the browser worker pool is up. Any trade
    left in trade_clicked/trade_opened when the process last stopped had a
    real position opened on Pocket Option that was never resolved in our
    database - re-attach outcome tracking to it, using a worker from the
    pool (not a separate browser), for the remaining time until expiry.

    A trade stuck at trade_prepared was never clicked (either ARMED was
    false or the process died before the click), so there is no real
    position to reconcile - it's marked abandoned rather than "resumed".
    """
    open_trades = database.get_open_trades()
    if not open_trades:
        logger.info("recovery: no pending trades to resume")
        return

    logger.info("recovery: found %d pending trade(s) to resume", len(open_trades))

    for row in open_trades:
        trade_id = row["id"]
        try:
            await _resume_one(row, pool)
        except Exception as e:
            logger.error("recovery: trade_id=%s failed to resume: %s", trade_id, e)
            database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:recovery_failed:{e}")


async def _resume_one(row, pool):
    trade_id = row["id"]
    asset = row["asset"]
    expiry = row["timeframe"]
    opened_at_raw = row["opened_at"]

    if not opened_at_raw:
        logger.warning("recovery: trade_id=%s has no opened_at - marking abandoned", trade_id)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:abandoned_on_restart")
        return

    opened_at = datetime.fromisoformat(opened_at_raw)
    total_expiry_seconds = pocket_dom.expiry_to_seconds(expiry)
    elapsed = (datetime.now() - opened_at).total_seconds()
    remaining = max(0, total_expiry_seconds - elapsed)

    logger.info(
        "recovery: trade_id=%s asset=%r opened_at=%s elapsed=%.1fs remaining=%.1fs",
        trade_id, asset, opened_at_raw, elapsed, remaining,
    )

    # Blocks until a worker frees up - acceptable at startup since no live
    # signals are flowing yet. track_outcome releases the worker back to
    # the pool when the trade resolves, exactly as in the normal flow.
    worker = await pool.acquire_worker(timeout=None)
    asyncio.create_task(
        pocket_executor.track_outcome(worker, pool, trade_id, remaining)
    )


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


async def run_recovery(pool):
    """Single entry point for startup: reconcile abandoned preparations,
    then resume tracking for any genuinely open positions. Must be called
    after pool.start()."""
    mark_abandoned_preparations()
    await resume_pending_trades(pool)
