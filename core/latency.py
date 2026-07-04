import sys
import time
from pathlib import Path

from logger import get_logger

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))
import database

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class LatencyTracker:
    """Millisecond-resolution checkpoints for one trade's lifecycle,
    relative to when the signal was received."""

    CHECKPOINTS = [
        "telegram_received",
        "parsed",
        "risk_approved",
        "worker_acquired",
        "asset_selected",
        "expiry_set",
        "amount_set",
        "click_completed",
        "confirmation_detected",
    ]

    def __init__(self, trade_id=None, worker_id=None):
        self.trade_id = trade_id
        self.worker_id = worker_id
        self._start = time.monotonic()
        self._marks = {}
        self._logged = False

    def mark(self, checkpoint):
        elapsed_ms = (time.monotonic() - self._start) * 1000
        self._marks[checkpoint] = elapsed_ms
        return elapsed_ms

    def summary(self):
        return dict(self._marks)

    def log_summary(self):
        if self._logged:
            return
        self._logged = True
        parts = " ".join(
            f"{cp}={self._marks[cp]:.0f}ms" for cp in self.CHECKPOINTS if cp in self._marks
        )
        logger.info(
            "LATENCY trade_id=%s worker_id=%s %s",
            self.trade_id, self.worker_id, parts or "(no checkpoints recorded)",
        )
        if self.trade_id is not None and self._marks:
            try:
                database.record_latency_checkpoints(self.trade_id, self._marks)
            except Exception as e:
                logger.error("LatencyTracker: failed to persist checkpoints for trade_id=%s: %s", self.trade_id, e)
