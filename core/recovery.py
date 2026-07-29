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
    context's worker pool is up.

    2026-07-29 reconciliation-gap fix (verified production incident:
    series 105): this used to scan ONLY database.get_open_trades()
    (execution_status IN trade_clicked/trade_opened). That is exactly the
    two narrowly-defined statuses the 2026-07-29 recovery review called
    out - a trade whose result-monitoring crashed (pocket_executor.
    track_outcome's own except block sets execution_status='error',
    result=f"error:{e}") falls OUT of those two statuses and became
    invisible to this scan entirely, even though a real position may
    still be open on the broker. Now scans database.
    get_unresolved_submitted_trades() instead - possibly_submitted
    (opened_at is set) AND not_authoritatively_resolved (no win/loss/draw
    recorded) - independent of whatever execution_status string a crash
    happened to leave behind. NOTE: this is deliberately a DIFFERENT,
    broader query than get_open_trades(), which core/fund_manager.py
    still uses, unchanged, for its own "genuinely open right now" exposure
    calculations - broadening that one instead would have altered a
    completely unrelated subsystem's semantics.

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
    # get_unresolved_submitted_trades() alone is not quite a superset of
    # the old get_open_trades() scan - a row stuck at trade_clicked/
    # trade_opened with NO opened_at at all (the process died between
    # setting execution_status and recording opened_at, before any real
    # position could exist) satisfies neither "possibly_submitted" nor
    # anything get_unresolved_submitted_trades' opened_at IS NOT NULL
    # filter would match, yet still needs the OLD "no real position -
    # mark abandoned" handling below. Union both queries here (in this
    # caller, not by broadening either database function - get_open_
    # trades() must stay narrow for fund_manager.py's own use) so neither
    # case is lost.
    by_id = {row["id"]: row for row in database.get_open_trades(broker_account_id=broker_account_id)}
    for row in database.get_unresolved_submitted_trades(broker_account_id=broker_account_id):
        by_id.setdefault(row["id"], row)
    unresolved_trades = sorted(by_id.values(), key=lambda r: r["id"])

    if not unresolved_trades:
        logger.info("recovery: no pending trades to resume")
        return

    logger.info("recovery: found %d unresolved submitted trade(s) to reconcile", len(unresolved_trades))

    for row in unresolved_trades:
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
                # Not a resume attempt at all - a missing opened_at here
                # is an unexpected data inconsistency (not the normal
                # "never clicked" case, which mark_abandoned_
                # preparations() already handles separately) - worth
                # counting as a failed recovery, since ideally this trade
                # would have been resumable.
                database.record_recovery_event("resume_open_trade", "failed", f"trade_id={trade_id}: missing opened_at")


async def _resume_one(row, warmup_service):
    """Returns True if this row was handled (either re-attached to live
    tracking, or definitively reconciled against broker history), False
    if it couldn't be resumed at all (missing opened_at)."""
    trade_id = row["id"]
    asset = row["asset"]
    direction = row["direction"]
    expiry = row["timeframe"]
    amount = row.get("trade_amount")
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

    if remaining <= 0:
        # The trade's own contractual expiry has already passed while
        # this process was down - by the time we're back up, live
        # monitoring (wait_for_trade_result's own nearest-time matching)
        # is no longer the right tool, since an arbitrary, unknown amount
        # of wall-clock time may have elapsed and other same-asset/
        # direction trades may have closed since. Use the stricter,
        # fail-closed broker-history matcher instead of guessing.
        match_status, match = await pocket_dom.find_closed_trade_by_criteria(
            warmup_service, asset, direction, amount, opened_at, total_expiry_seconds,
        )
        if match_status != "unique":
            logger.error(
                "recovery: trade_id=%s already past expiry at restart, broker-history match=%s - "
                "failing closed, marking reconciliation_required",
                trade_id, match_status,
            )
            database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:reconciliation_required")
            series_id = row.get("series_id")
            if series_id:
                database.mark_series_reconciliation_required(series_id, f"broker_history_{match_status}")
            return True

        profit_loss = None
        if match["final_value"] is not None and match["stake"] is not None:
            profit_loss = match["final_value"] - match["stake"]
        logger.warning(
            "recovery: trade_id=%s already past expiry at restart, uniquely matched broker history: "
            "result=%s - reconciling automatically",
            trade_id, match["result"],
        )
        # Mirrors pocket_executor.track_outcome's own two-step update
        # (TRADE_CLOSED, then the final RESULT_WIN/LOSS/DRAW status) so a
        # broker-history-reconciled trade's execution_status ends up
        # identical to one resolved through live tracking, not a
        # distinguishable "trade_closed"-and-nothing-more state.
        closed_at = datetime.now().isoformat()
        database.update_trade_status(
            trade_id, TradeStatus.TRADE_CLOSED, closed_at=closed_at, result=match["result"], profit_loss=profit_loss,
        )
        result_status = {
            "win": TradeStatus.RESULT_WIN, "loss": TradeStatus.RESULT_LOSS, "draw": TradeStatus.RESULT_DRAW,
        }.get(match["result"], TradeStatus.ERROR)
        database.update_trade_status(
            trade_id, result_status, closed_at=closed_at, result=match["result"], profit_loss=profit_loss,
        )
        try:
            from event_bus import get_event_bus
            await get_event_bus().publish("trade.closed", {
                "trade_id": trade_id, "result": match["result"], "profit_loss": profit_loss,
            })
        except Exception as e:
            logger.error("recovery: trade_id=%s failed to publish trade.closed event: %s", trade_id, e)
        return True

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
