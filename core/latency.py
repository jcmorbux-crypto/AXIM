import logging
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class LatencyTracker:
    """Millisecond-resolution checkpoints for one trade's lifecycle,
    relative to when the signal was received."""

    CHECKPOINTS = [
        "telegram_received",
        "parsed",
        "risk_approved",
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
