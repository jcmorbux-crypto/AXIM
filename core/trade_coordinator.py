import logging
import sys
import time
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
EXECUTION_DIR = PROJECT_ROOT / "execution"
LOG_DIR = PROJECT_ROOT / "logs"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))
sys.path.insert(0, str(EXECUTION_DIR))

import database
import risk_manager
from trade_lifecycle import TradeStatus
from event_bus import get_event_bus
from settings import MAX_SIGNAL_AGE, PREVIEW_ONLY, AUTO_EXECUTE, TRADE_AMOUNT

import pocket_executor

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class TradeCoordinator:
    """
    Orchestration owner for the signal -> trade pipeline:
    Validation -> Risk Manager -> Duplicate Detection -> Trade Lifecycle ->
    Pocket Executor -> Outcome Tracking -> Statistics -> Dashboard.

    Does not perform any browser interaction itself - that stays entirely
    inside execution/pocket_executor.py and execution/pocket_dom.py, unchanged.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus or get_event_bus()

    def _log_stage(self, trade_id, stage, status, elapsed, reason=None):
        logger.info(
            "STAGE trade_id=%s stage=%s status=%s elapsed=%.3fs reason=%s",
            trade_id, stage, status, elapsed, reason,
        )

    async def handle_signal(self, signal, source=None, sender=None, message_id=None, sent_at=None):
        # Bookkeeping: record the signal immediately, before any gate runs,
        # so an ignored or rejected signal still has a row to log against -
        # otherwise "signals ignored"/"signals rejected" statistics would
        # have nothing to count.
        stage_t0 = time.monotonic()
        trade_id = database.record_signal_received(signal, source=source, sender=sender, message_id=message_id)
        self._log_stage(trade_id, TradeStatus.SIGNAL_RECEIVED.value, "recorded", time.monotonic() - stage_t0)
        await self.event_bus.publish("trade.signal_received", {"trade_id": trade_id, "signal": signal})

        asset, direction, expiry = signal["asset"], signal["direction"], signal["expiry"]
        amount = TRADE_AMOUNT

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
                    return {"status": "ignored", "trade_id": trade_id, "reason": "stale_signal", "age_seconds": age_seconds}
            self._log_stage(trade_id, "validation", "passed", time.monotonic() - stage_t0)

            # Stage: Risk Manager
            stage_t0 = time.monotonic()
            try:
                risk_manager.check_demo_only()
                risk_manager.check_max_trade_amount(amount)
                risk_manager.check_max_trades_per_hour()
                risk_manager.check_max_consecutive_losses()
                risk_manager.check_cooldown_after_loss()
            except risk_manager.RiskViolation as violation:
                return self._reject(trade_id, violation, time.monotonic() - stage_t0)
            self._log_stage(trade_id, "risk_manager", "passed", time.monotonic() - stage_t0)

            # Stage: Duplicate Detection
            stage_t0 = time.monotonic()
            try:
                risk_manager.check_duplicate_signal(asset, direction, expiry, exclude_id=trade_id)
            except risk_manager.RiskViolation as violation:
                return self._reject(trade_id, violation, time.monotonic() - stage_t0)
            self._log_stage(trade_id, "duplicate_detection", "passed", time.monotonic() - stage_t0)

            # Stage: Trade Lifecycle - cleared for execution
            self._log_stage(trade_id, "trade_lifecycle", "cleared_for_execution", 0.0)

            if PREVIEW_ONLY or not AUTO_EXECUTE:
                self._log_stage(trade_id, "pocket_executor", "preview_only", 0.0)
                return {"status": "preview", "trade_id": trade_id}

            # Stage: Pocket Executor (unchanged browser execution logic)
            stage_t0 = time.monotonic()
            result = await pocket_executor.prepare_trade(trade_id, asset, direction, expiry, amount)
            self._log_stage(trade_id, "pocket_executor", result.get("status"), time.monotonic() - stage_t0)
            await self.event_bus.publish("trade.prepared", {"trade_id": trade_id, "result": result})

            return result
        except Exception as e:
            logger.error("trade_coordinator: trade_id=%s unhandled error=%s", trade_id, e)
            database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"error:{e}")
            await self.event_bus.publish("trade.error", {"trade_id": trade_id, "error": str(e)})
            raise

    def _reject(self, trade_id, violation, elapsed):
        self._log_stage(trade_id, violation.rule, "rejected", elapsed, violation.reason)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result=f"rejected:{violation.rule}")
        return {"status": "rejected", "trade_id": trade_id, "rule": violation.rule, "reason": violation.reason}
