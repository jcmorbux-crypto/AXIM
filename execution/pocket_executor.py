import logging
import os
import sys
import asyncio
from datetime import datetime
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

import pocket_dom
import database
from trade_lifecycle import TradeStatus
from latency import LatencyTracker

ARMED = os.getenv("ARMED", "false").lower() == "true"

SCREENSHOT_DIR = PROJECT_ROOT / "logs" / "trades"
LOG_DIR = PROJECT_ROOT / "logs"

lifecycle_logger = logging.getLogger("axim.lifecycle")
if not lifecycle_logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lifecycle_logger.addHandler(_handler)
    lifecycle_logger.addHandler(logging.StreamHandler())
    lifecycle_logger.setLevel(logging.INFO)


async def prepare_trade(trade_id, asset, direction, expiry, amount, page, lock, latency=None):
    """
    Runs the verified browser interaction sequence (unchanged internals -
    select_asset/select_expiry/set_amount/verify_direction_controls_ready/
    click_direction all live in pocket_dom.py exactly as before) against
    the persistent, already-open `page` supplied by BrowserWarmupService.

    `lock` guards the one warm page - held for the whole synchronous part
    of this call, and transferred to the background outcome tracker (not
    released here) if a real click happens, since that task keeps using
    the same page until the trade closes.
    """
    latency = latency or LatencyTracker(trade_id)
    latency.trade_id = trade_id

    await lock.acquire()
    lock_transferred = False
    try:
        await pocket_dom.select_asset(page, asset)
        latency.mark("asset_selected")

        await pocket_dom.select_expiry(page, expiry)
        latency.mark("expiry_set")

        await pocket_dom.set_amount(page, amount)
        latency.mark("amount_set")

        await pocket_dom.verify_direction_controls_ready(page)

        payout = await pocket_dom.read_payout_percent(page)
        screenshot_path = await _take_screenshot(page, trade_id, "prepared")
        database.append_screenshot_path(trade_id, screenshot_path)
        database.update_trade_status(
            trade_id, TradeStatus.TRADE_PREPARED,
            trade_amount=amount, payout=payout,
        )
        lifecycle_logger.info("trade_id=%s status=%s payout=%s", trade_id, TradeStatus.TRADE_PREPARED.value, payout)

        print("\nAXIM TRADE PREPARED (verified: asset, expiry, amount, direction controls)")
        print(f"Asset     : {asset}")
        print(f"Direction : {direction}")
        print(f"Expiry    : {expiry}")
        print(f"Amount    : ${amount}")
        print(f"Payout    : {payout}%" if payout is not None else "Payout    : unknown")
        print(f"Armed     : {ARMED}")

        if not ARMED:
            print("Status    : ARMED=false, trade NOT clicked")
            latency.log_summary()
            return {
                "status": "prepared_not_armed",
                "trade_id": trade_id,
                "asset": asset,
                "direction": direction,
                "expiry": expiry,
                "amount": amount,
            }

        await pocket_dom.click_direction(page, direction)
        latency.mark("click_completed")
        # click_direction's own internal wait already confirms the trade
        # opened (Opened list updated) before returning without raising -
        # its successful return IS the confirmation.
        latency.mark("confirmation_detected")

        opened_at = datetime.now().isoformat()
        screenshot_path = await _take_screenshot(page, trade_id, "clicked")
        database.append_screenshot_path(trade_id, screenshot_path)
        database.update_trade_status(trade_id, TradeStatus.TRADE_CLICKED, opened_at=opened_at)
        lifecycle_logger.info("trade_id=%s status=%s", trade_id, TradeStatus.TRADE_CLICKED.value)

        database.update_trade_status(trade_id, TradeStatus.TRADE_OPENED, opened_at=opened_at)
        lifecycle_logger.info("trade_id=%s status=%s", trade_id, TradeStatus.TRADE_OPENED.value)

        print("Status    : TRADE BUTTON CLICKED (confirmed via Opened trades list)")
        latency.log_summary()

        expiry_seconds = pocket_dom.expiry_to_seconds(expiry)
        lock_transferred = True
        asyncio.create_task(track_outcome(page, trade_id, expiry_seconds, lock))

        return {
            "status": "clicked",
            "trade_id": trade_id,
            "asset": asset,
            "direction": direction,
            "expiry": expiry,
            "amount": amount,
        }
    except pocket_dom.AssetUntradeableError as e:
        lifecycle_logger.warning("trade_id=%s asset untradeable: %s", trade_id, e)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="rejected:asset_untradeable")
        latency.log_summary()
        return {"status": "rejected", "trade_id": trade_id, "rule": "asset_untradeable", "reason": str(e)}
    except Exception as e:
        lifecycle_logger.error("trade_id=%s status=%s error=%s", trade_id, TradeStatus.ERROR.value, e)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:{e}")
        latency.log_summary()
        raise
    finally:
        if not lock_transferred:
            lock.release()


async def track_outcome(page, trade_id, expiry_seconds, lock):
    """Waits for a trade to close and records the result. Used both for the
    normal post-click flow (prepare_trade, above) and by core/recovery.py
    to re-attach tracking to a trade left open across a restart. Releases
    `lock` when done, since it holds the one warm page until the trade
    resolves - the next queued trade can't proceed until this returns."""
    try:
        outcome = await pocket_dom.wait_for_trade_result(page, expiry_seconds)
        if outcome is None:
            database.update_trade_status(trade_id, TradeStatus.ERROR, result="error:result_read_failed")
            lifecycle_logger.error(
                "trade_id=%s status=%s reason=result_read_failed",
                trade_id, TradeStatus.ERROR.value,
            )
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
    finally:
        lock.release()


async def _take_screenshot(page, trade_id, label):
    trade_dir = SCREENSHOT_DIR / str(trade_id)
    trade_dir.mkdir(parents=True, exist_ok=True)
    path = trade_dir / f"{label}.png"
    await page.screenshot(path=str(path))
    return str(path)
