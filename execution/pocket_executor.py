import os
import sys
import asyncio
import time
from datetime import datetime
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import pocket_dom
import database
import risk_manager
from trade_lifecycle import TradeStatus
from timeline import TradeTimeline, get_current_timeline, clear_current
from logger import get_logger
from settings import SAVE_SCREENSHOTS

ARMED = os.getenv("ARMED", "false").lower() == "true"

SCREENSHOT_DIR = PROJECT_ROOT / "logs" / "trades"

lifecycle_logger = get_logger("axim.lifecycle", filename="lifecycle.log")


def _capture_screenshot_background(page, trade_id, label):
    """Fire-and-forget screenshot capture - previously `await
    _take_screenshot(...)` sat directly on the signal-to-click critical
    path (confirmed by the P0 benchmark: ~1.6s of otherwise-unaccounted
    time between amount_set and click_completed on the already-warm no-op
    path). Screenshots are diagnostic/audit only, never read by any
    execution or risk decision, so nothing downstream depends on this
    finishing before prepare_trade returns.

    Documented tradeoff (required before trading any latency for anything,
    including the inverse case of gaining latency at a reliability/
    diagnostic cost): the worker's page is released immediately after
    prepare_trade returns on every path now, including "clicked" (a worker
    is no longer held for a trade's full expiry - see track_outcome), so if
    this same worker's page gets reacquired by a new trade before the
    background capture actually runs, the screenshot could show the new
    trade's state instead. This never affects trade correctness or any risk
    decision - only what a saved diagnostic image shows - and requires the
    same worker being reacquired within a sub-second window to occur at
    all."""
    if not SAVE_SCREENSHOTS:
        return

    async def _do():
        # This task's context was copied (with the active timeline) at
        # asyncio.create_task() time below, but its own work runs
        # CONCURRENTLY with the rest of prepare_trade's sequential
        # execution, not as part of it - clearing the timeline here (only
        # affects this task's own copy) stops its screenshot IPC and DB
        # write from being double-counted against a trade whose "total"
        # duration doesn't include this overlapping background work.
        clear_current()
        try:
            path = await _take_screenshot(page, trade_id, label)
            database.append_screenshot_path(trade_id, path)
        except Exception as e:
            lifecycle_logger.error(
                "trade_id=%s failed to capture/persist %r screenshot: %s", trade_id, label, e,
            )

    asyncio.create_task(_do())


async def prepare_trade(trade_id, asset, direction, expiry, amount, worker, pool, warmup_service, timeline=None):
    """
    Runs the verified browser interaction sequence (unchanged internals -
    select_asset/select_expiry/set_amount/verify_direction_controls_ready/
    click_direction all live in pocket_dom.py exactly as before) against
    `worker.page`, one of BrowserWorkerPool's N warm pages.

    `worker`'s lock is already held by the time this is called (acquired
    by the caller via pool.acquire_worker()) - held for the whole
    synchronous part of this call, and transferred to the background
    outcome tracker (not released here) if a real click happens, since
    that task keeps using the same worker until the trade closes.

    "clicked" and "confirmation_detected" are marked inside pocket_dom.
    click_direction itself (via the active timeline, core/timeline.py),
    not here - closes a documented instrumentation gap where those two
    stages used to be marked back-to-back with no separating work.
    """
    timeline = timeline or TradeTimeline(trade_id=trade_id)
    timeline.trade_id = trade_id
    page = worker.page

    ownership_transferred = False
    try:
        await pocket_dom.select_asset(page, asset)
        timeline.mark("asset_selected")

        await pocket_dom.select_expiry(page, expiry)
        timeline.mark("expiry_set")

        await pocket_dom.set_amount(page, amount)
        timeline.mark("amount_set")

        await pocket_dom.verify_direction_controls_ready(page)

        # Combined single-pass read: is the asset still tradeable right
        # now (closing the narrow window between select_asset's own
        # pre-selection check and this point), and its current payout.
        payout, tradeable_now = await pocket_dom.read_payout_and_check_tradeable(page)
        if not tradeable_now:
            raise pocket_dom.AssetUntradeableError(
                asset, reason="became untradeable after selection (asset-inactive overlay active)",
            )

        try:
            risk_manager.check_minimum_payout(payout)
        except risk_manager.RiskViolation as violation:
            lifecycle_logger.warning(
                "trade_id=%s worker_id=%s rejected: %s", trade_id, worker.worker_id, violation.reason,
            )
            database.update_trade_status(
                trade_id, TradeStatus.ERROR,
                trade_amount=amount, payout=payout,
                result=f"rejected:{violation.rule}",
            )
            print(f"Status    : REJECTED ({violation.rule}: {violation.reason})")
            timeline.persist(database)
            return {
                "status": "rejected", "trade_id": trade_id,
                "rule": violation.rule, "reason": violation.reason,
            }

        _capture_screenshot_background(page, trade_id, "prepared")
        database.update_trade_status(
            trade_id, TradeStatus.TRADE_PREPARED,
            trade_amount=amount, payout=payout,
        )
        lifecycle_logger.info(
            "trade_id=%s worker_id=%s status=%s payout=%s",
            trade_id, worker.worker_id, TradeStatus.TRADE_PREPARED.value, payout,
        )

        print("\nAXIM TRADE PREPARED (verified: asset, expiry, amount, direction controls)")
        print(f"Worker    : {worker.worker_id}")
        print(f"Asset     : {asset}")
        print(f"Direction : {direction}")
        print(f"Expiry    : {expiry}")
        print(f"Amount    : ${amount}")
        print(f"Payout    : {payout}%" if payout is not None else "Payout    : unknown")
        print(f"Armed     : {ARMED}")

        if not ARMED:
            print("Status    : ARMED=false, trade NOT clicked")
            timeline.persist(database)
            return {
                "status": "prepared_not_armed",
                "trade_id": trade_id,
                "asset": asset,
                "direction": direction,
                "expiry": expiry,
                "amount": amount,
            }

        await pocket_dom.click_direction(page, direction)

        opened_at = datetime.now().isoformat()
        _capture_screenshot_background(page, trade_id, "clicked")
        database.update_trade_status(trade_id, TradeStatus.TRADE_CLICKED, opened_at=opened_at)
        lifecycle_logger.info("trade_id=%s worker_id=%s status=%s", trade_id, worker.worker_id, TradeStatus.TRADE_CLICKED.value)

        database.update_trade_status(trade_id, TradeStatus.TRADE_OPENED, opened_at=opened_at)
        lifecycle_logger.info("trade_id=%s worker_id=%s status=%s", trade_id, worker.worker_id, TradeStatus.TRADE_OPENED.value)

        print("Status    : TRADE BUTTON CLICKED (confirmed via Opened trades list)")
        timeline.persist(database)

        expiry_seconds = pocket_dom.expiry_to_seconds(expiry)
        # The worker is released right below (finally) instead of being
        # held by track_outcome for the trade's whole expiry - see
        # track_outcome's docstring for why that was the real bottleneck
        # limiting concurrent open trades to MAX_CONCURRENT_WORKERS, and
        # why outcome-watching uses warmup_service's dedicated page rather
        # than this same placement pool.
        asyncio.create_task(track_outcome(warmup_service, trade_id, expiry_seconds, asset=asset, direction=direction))

        return {
            "status": "clicked",
            "trade_id": trade_id,
            "asset": asset,
            "direction": direction,
            "expiry": expiry,
            "amount": amount,
        }
    except pocket_dom.AssetUntradeableError as e:
        lifecycle_logger.warning("trade_id=%s worker_id=%s asset untradeable: %s", trade_id, worker.worker_id, e)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="rejected:asset_untradeable")
        timeline.persist(database)
        return {"status": "rejected", "trade_id": trade_id, "rule": "asset_untradeable", "reason": str(e)}
    except Exception as e:
        lifecycle_logger.error("trade_id=%s worker_id=%s status=%s error=%s", trade_id, worker.worker_id, TradeStatus.ERROR.value, e)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:{e}")
        timeline.persist(database)
        raise
    finally:
        pool.release_worker(worker)


async def track_outcome(warmup_service, trade_id, expiry_seconds, asset=None, direction=None):
    """Waits for a trade to close and records the result. Used both for the
    normal post-click flow (prepare_trade, above) and by core/recovery.py
    to re-attach tracking to a trade left open across a restart.

    Does NOT hold - or even borrow - a placement worker for the wait or the
    read. The original design held the worker that placed the trade for its
    entire expiry (a 10-minute trade tied up one of only
    MAX_CONCURRENT_WORKERS browser tabs for 10 minutes), which meant the
    real limit on "how many trades can be open at once" was the worker
    count, not anything about risk or the source's actual signal rate -
    real bursts of fast-expiry signals arriving while long-expiry trades
    were still open got dropped as "all workers busy" even though nothing
    was actually wrong. A first fix had wait_for_trade_result briefly
    acquire a placement worker just for the final tab-switch+read step -
    better, but that still meant every trade's outcome check contended with
    NEW trades for a worker slot, which under heavy concurrency (10
    workers, real signal bursts) measurably degraded placement reliability
    itself (DOM timeouts on select_asset/select_expiry rose noticeably).
    Final fix: outcome detection only ever needed the Closed-trades list,
    which pocket_dom.wait_for_trade_result confirmed is shared live across
    every page in the browser context (clicking Closed on one tab instantly
    flips every other tab's rendered active tab too) - so it reads from
    `warmup_service`'s own dedicated bootstrap page (idle after startup)
    instead of the placement pool at all. Placement workers are now used
    for PLACEMENT ONLY, full stop - MAX_CONCURRENT_WORKERS bounds
    simultaneous placements, not simultaneous open positions, and outcome
    reads never compete with a new signal for a worker.

    `asset`/`direction` let wait_for_trade_result identify THIS specific
    trade's own closed item rather than relying on .no-deals, which was
    confirmed (P0 latency sprint follow-up) to reflect "zero open positions
    system-wide", not "this trade closed". Residual limitation, unchanged
    by this redesign: same-asset, same-direction trades closing within the
    same clock-minute (the site only renders HH:MM) can still be
    ambiguous - _closest_closed_item picks the nearest-time match, which
    reduces but does not eliminate that ambiguity, and higher concurrency
    makes it somewhat more likely to occur, not less.

    This coroutine is created via asyncio.create_task while prepare_trade's
    timeline is still active, so it normally inherits that SAME timeline
    object (contextvars propagate into child tasks by reference-copy at
    creation time - see core/timeline.py). The exception is a trade resumed
    by core/recovery.py after a process restart: no timeline was ever
    activated for this trade_id in THIS process, so a fresh one is created
    here, scoped to just the tail-end stages - persist() merges it with
    whatever the original process already saved, rather than clobbering it.
    """
    timeline = get_current_timeline()
    own_token = None
    if timeline is None:
        timeline = TradeTimeline(trade_id=trade_id)
        own_token = timeline.activate()

    # Measures detection/polling overhead on top of the trade's own
    # contractual expiry duration - not the expiry wait itself (a
    # deliberate, non-optimizable delay), but how much extra wall-clock
    # AXIM's settlement wait/retry takes beyond that to actually notice and
    # classify the close.
    wait_t0 = time.monotonic()
    try:
        outcome = await pocket_dom.wait_for_trade_result(
            warmup_service, expiry_seconds, asset=asset, direction=direction,
        )
        detection_overhead_ms = (time.monotonic() - wait_t0 - expiry_seconds) * 1000
        try:
            database.record_outcome_latency(trade_id, detection_overhead_ms)
        except Exception as e:
            lifecycle_logger.error("trade_id=%s failed to persist outcome_detection_ms: %s", trade_id, e)

        if outcome is None:
            database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:result_read_failed")
            lifecycle_logger.error(
                "trade_id=%s status=%s reason=result_read_failed",
                trade_id, TradeStatus.ERROR.value,
            )
            timeline.persist(database)
            return

        closed_at = datetime.now().isoformat()
        profit_loss = None
        if outcome["final_value"] is not None and outcome["stake"] is not None:
            profit_loss = outcome["final_value"] - outcome["stake"]

        database.update_trade_status(
            trade_id, TradeStatus.TRADE_CLOSED,
            closed_at=closed_at, result=outcome["result"], profit_loss=profit_loss,
        )
        lifecycle_logger.info("trade_id=%s status=%s", trade_id, TradeStatus.TRADE_CLOSED.value)

        status_map = {
            "win": TradeStatus.RESULT_WIN,
            "loss": TradeStatus.RESULT_LOSS,
            "draw": TradeStatus.RESULT_DRAW,
            "unknown": TradeStatus.ERROR,
        }
        result_status = status_map.get(outcome["result"], TradeStatus.ERROR)
        database.update_trade_status(
            trade_id, result_status,
            closed_at=closed_at, result=outcome["result"], profit_loss=profit_loss,
        )
        timeline.mark("outcome_recorded")
        timeline.persist(database)

        lifecycle_logger.info(
            "trade_id=%s status=%s result=%s stake=%s final_value=%s profit_loss=%s",
            trade_id, result_status.value, outcome["result"],
            outcome["stake"], outcome["final_value"], profit_loss,
        )

        try:
            from event_bus import get_event_bus
            await get_event_bus().publish("trade.closed", {
                "trade_id": trade_id, "result": outcome["result"], "profit_loss": profit_loss,
            })
        except Exception as e:
            lifecycle_logger.error("trade_id=%s failed to publish trade.closed event: %s", trade_id, e)
    except Exception as e:
        lifecycle_logger.error("trade_id=%s status=%s error=%s", trade_id, TradeStatus.ERROR.value, e)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:{e}")
        timeline.persist(database)
    finally:
        if own_token is not None:
            TradeTimeline.deactivate(own_token)


async def _take_screenshot(page, trade_id, label):
    """Deliberately NOT wrapped in time_category("browser") - this runs as
    fire-and-forget background work (see _capture_screenshot_background),
    concurrently with the rest of the trade's own sequential execution, not
    sequentially as part of it. Two operations that overlap in wall-clock
    time can't be summed additively against a wall-clock total (the same
    reason "CPU time" can exceed "wall time" on a multi-core system) - since
    this is explicitly decoupled from the critical path (the whole point of
    making it fire-and-forget), it's excluded from the category totals that
    active-time is computed as a residual against, rather than silently
    breaking that arithmetic."""
    trade_dir = SCREENSHOT_DIR / str(trade_id)
    trade_dir.mkdir(parents=True, exist_ok=True)
    path = trade_dir / f"{label}.png"
    await page.screenshot(path=str(path))
    return str(path)
