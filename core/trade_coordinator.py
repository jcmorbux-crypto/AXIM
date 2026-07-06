import sys
import time
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
EXECUTION_DIR = PROJECT_ROOT / "execution"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))
sys.path.insert(0, str(EXECUTION_DIR))

import database
import risk_manager
from trade_lifecycle import TradeStatus
from event_bus import get_event_bus
from timeline import TradeTimeline
from logger import get_logger
from settings import MAX_SIGNAL_AGE, PREVIEW_ONLY, AUTO_EXECUTE, TRADE_AMOUNT, WORKER_ACQUIRE_TIMEOUT_SECONDS

import pocket_executor
import asset_cache

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class TradeCoordinator:
    """
    Orchestration owner for the signal -> trade pipeline:
    Validation -> Risk Manager -> Duplicate Detection -> Trade Lifecycle ->
    Pocket Executor -> Outcome Tracking -> Statistics -> Dashboard.

    Does not perform any browser interaction itself - that stays entirely
    inside execution/pocket_executor.py and execution/pocket_dom.py.

    Uses a BrowserWorkerPool (N warm pages, each with its own lock) instead
    of a single page/lock - trades on different workers run fully in
    parallel. If all workers are busy, acquire_worker() queues (FIFO) up to
    WORKER_ACQUIRE_TIMEOUT_SECONDS before this coordinator rejects the
    signal cleanly, rather than stalling "near-instant" execution
    indefinitely.
    """

    def __init__(self, worker_pool, warmup_service, event_bus=None):
        self.worker_pool = worker_pool
        # Passed through to pocket_executor.prepare_trade so its background
        # track_outcome task reads outcomes from this service's own
        # dedicated (otherwise-idle) page instead of borrowing a placement
        # worker - see pocket_executor.track_outcome's docstring.
        self.warmup_service = warmup_service
        self.event_bus = event_bus or get_event_bus()

    def _log_stage(self, trade_id, stage, status, elapsed, reason=None):
        logger.info(
            "STAGE trade_id=%s stage=%s status=%s elapsed=%.3fs reason=%s",
            trade_id, stage, status, elapsed, reason,
        )

    async def handle_signal(self, signal, source=None, sender=None, message_id=None,
                             sent_at=None, timeline=None):
        timeline = timeline or TradeTimeline()
        token = timeline.activate()
        try:
            if "signal_received" not in timeline.stage_timestamps:
                timeline.mark("signal_received")
            if "signal_parsed" not in timeline.stage_timestamps:
                timeline.mark("signal_parsed")

            # Bookkeeping: record the signal immediately, before any gate runs,
            # so an ignored or rejected signal still has a row to log against -
            # otherwise "signals ignored"/"signals rejected" statistics would
            # have nothing to count.
            stage_t0 = time.monotonic()
            trade_id = database.record_signal_received(signal, source=source, sender=sender, message_id=message_id)
            timeline.trade_id = trade_id
            self._log_stage(trade_id, TradeStatus.SIGNAL_RECEIVED.value, "recorded", time.monotonic() - stage_t0)
            await self.event_bus.publish("trade.signal_received", {"trade_id": trade_id, "signal": signal})

            asset, direction, expiry = signal["asset"], signal["direction"], signal["expiry"]
            # Fixed TRADE_AMOUNT by default, or a percentage of current
            # bankroll if the operator configured that via the UI - see
            # risk_manager.compute_trade_amount's own docstring.
            amount = risk_manager.compute_trade_amount(TRADE_AMOUNT)

            try:
                # Stage: Validation (freshness)
                stage_t0 = time.monotonic()
                if sent_at is not None:
                    now = datetime.now(sent_at.tzinfo) if sent_at.tzinfo else datetime.now()
                    age_seconds = (now - sent_at).total_seconds()
                    if age_seconds > MAX_SIGNAL_AGE:
                        reason = f"signal age {age_seconds:.1f}s exceeds MAX_SIGNAL_AGE {MAX_SIGNAL_AGE}s"
                        self._log_stage(trade_id, "validation", "ignored", time.monotonic() - stage_t0, reason)
                        database.update_trade_status(trade_id, TradeStatus.ERROR, result="ignored:stale_signal")
                        await self.event_bus.publish("signal.ignored", {"trade_id": trade_id, "reason": "stale_signal"})
                        timeline.persist(database)
                        return {"status": "ignored", "trade_id": trade_id, "reason": "stale_signal", "age_seconds": age_seconds}
                self._log_stage(trade_id, "validation", "passed", time.monotonic() - stage_t0)

                # Stage: Risk Manager
                stage_t0 = time.monotonic()
                try:
                    risk_manager.check_demo_only()
                    risk_manager.check_max_trade_amount(amount)
                    risk_manager.check_max_trades_per_hour()
                    risk_manager.check_max_trades_per_day()
                    risk_manager.check_max_consecutive_losses()
                    risk_manager.check_cooldown_after_loss()
                    risk_manager.check_max_daily_loss()
                    risk_manager.check_daily_profit_target()
                except risk_manager.RiskViolation as violation:
                    timeline.persist(database)
                    return self._reject(trade_id, violation, time.monotonic() - stage_t0)
                self._log_stage(trade_id, "risk_manager", "passed", time.monotonic() - stage_t0)

                # Stage: Duplicate Detection
                stage_t0 = time.monotonic()
                try:
                    risk_manager.check_duplicate_signal(asset, direction, expiry, exclude_id=trade_id)
                except risk_manager.RiskViolation as violation:
                    timeline.persist(database)
                    return self._reject(trade_id, violation, time.monotonic() - stage_t0)
                self._log_stage(trade_id, "duplicate_detection", "passed", time.monotonic() - stage_t0)
                timeline.mark("risk_evaluated")

                # Correct a case-only mismatch against the real scanned asset
                # list before anything else - execution/pocket_dom.py's
                # select_asset() does an exact-text DOM match, so a parsed
                # name that's right except for casing would otherwise reach
                # the browser and fail as "not found" for no real reason.
                asset = asset_cache.resolve_exact_name(asset)

                # Fast-path rejection using the startup asset cache: if we
                # already know (from the last scan) that this asset is
                # untradeable, reject now without touching the browser/lock at
                # all. The cache can go stale, so "unknown" (None) falls through
                # to the normal flow, where pocket_dom does the live DOM check.
                cached_tradeable = asset_cache.is_known_tradeable(asset)
                if cached_tradeable is False:
                    reason = f"{asset!r} was untradeable at last asset-cache scan"
                    self._log_stage(trade_id, "asset_cache", "rejected", 0.0, reason)
                    database.update_trade_status(trade_id, TradeStatus.ERROR, result="rejected:asset_untradeable_cached")
                    timeline.persist(database)
                    return {"status": "rejected", "trade_id": trade_id, "rule": "asset_untradeable_cached", "reason": reason}

                # Stage: Trade Lifecycle - cleared for execution
                self._log_stage(trade_id, "trade_lifecycle", "cleared_for_execution", 0.0)

                # database.get_control_state()["test_mode"] is a UI-flippable
                # runtime override - it can only ADD a reason to skip real
                # execution, never remove the static PREVIEW_ONLY/AUTO_EXECUTE
                # .env gate above. Same everything-up-to-execution behavior as
                # PREVIEW_ONLY, distinctly labeled so it's not confused with
                # the .env-level setting in logs/dashboard.
                if PREVIEW_ONLY or not AUTO_EXECUTE:
                    self._log_stage(trade_id, "pocket_executor", "preview_only", 0.0)
                    timeline.persist(database)
                    return {"status": "preview", "trade_id": trade_id}

                if database.get_control_state().get("test_mode"):
                    self._log_stage(trade_id, "pocket_executor", "test_mode_skipped", 0.0)
                    timeline.persist(database)
                    return {"status": "test_mode", "trade_id": trade_id}

                # Stage: Worker Pool - acquire one of N warm pages. Queues
                # (FIFO) up to WORKER_ACQUIRE_TIMEOUT_SECONDS if all are busy;
                # rejects cleanly rather than hanging if none free up in time.
                stage_t0 = time.monotonic()
                worker = await self.worker_pool.acquire_worker(timeout=WORKER_ACQUIRE_TIMEOUT_SECONDS)
                if worker is None:
                    reason = (
                        f"all {self.worker_pool.num_workers} worker(s) busy for "
                        f"longer than WORKER_ACQUIRE_TIMEOUT_SECONDS={WORKER_ACQUIRE_TIMEOUT_SECONDS}s"
                    )
                    self._log_stage(trade_id, "worker_pool", "rejected", time.monotonic() - stage_t0, reason)
                    database.update_trade_status(trade_id, TradeStatus.ERROR, result="rejected:all_workers_busy")
                    timeline.persist(database)
                    return {"status": "rejected", "trade_id": trade_id, "rule": "all_workers_busy", "reason": reason}
                self._log_stage(trade_id, "worker_pool", f"acquired worker_id={worker.worker_id}", time.monotonic() - stage_t0)

                # Stage: Pocket Executor (unchanged browser execution logic,
                # against this worker's own warm page)
                stage_t0 = time.monotonic()
                result = await pocket_executor.prepare_trade(
                    trade_id, asset, direction, expiry, amount,
                    worker, self.worker_pool, self.warmup_service, timeline=timeline,
                )
                self._log_stage(trade_id, "pocket_executor", result.get("status"), time.monotonic() - stage_t0)
                await self.event_bus.publish("trade.prepared", {"trade_id": trade_id, "result": result})

                return result
            except Exception as e:
                logger.error("trade_coordinator: trade_id=%s unhandled error=%s", trade_id, e)
                database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:{e}")
                await self.event_bus.publish("trade.error", {"trade_id": trade_id, "error": str(e)})
                timeline.persist(database)
                raise
        finally:
            # Safe even though a background track_outcome task may still be
            # using this SAME timeline object - asyncio.create_task() copies
            # the active-context reference at task-creation time, so
            # deactivating here (in THIS context) does not affect the
            # already-scheduled child task's own copy.
            TradeTimeline.deactivate(token)

    def _reject(self, trade_id, violation, elapsed):
        self._log_stage(trade_id, violation.rule, "rejected", elapsed, violation.reason)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"rejected:{violation.rule}")
        return {"status": "rejected", "trade_id": trade_id, "rule": violation.rule, "reason": violation.reason}
