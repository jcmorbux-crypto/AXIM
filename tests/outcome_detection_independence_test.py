"""
MANUAL, ONE-OFF REGRESSION TEST - not part of the automated suite. Guards
against the outcome-detection concurrency bug found in the P0 latency
sprint follow-up: .no-deals reflects "zero open positions system-wide", not
"this worker's trade closed" - confirmed live (two concurrent trades, 15s
and 60s expiry): the 15s trade's own .no-deals stayed False for the full
~60 seconds until the 60s trade ALSO closed. Fixed in pocket_dom.
wait_for_trade_result by sleeping for the trade's own known expiry instead
of polling that aggregate signal, then matching the specific closed item by
asset + direction + closest closing-time.

This test fires one fast (15s) and one slow (60s) trade concurrently and
asserts the fast one resolves well before the slow one - if this
regresses, the fast trade will instead resolve at roughly the same time as
the slow one (the original bug's signature).

ARMED forced true for this process only; risk thresholds relaxed the same
way as tests/latency_benchmark.py - .env is never touched, this measures
pipeline behavior, not risk-rule behavior.

Run only when explicitly instructed to re-verify this fix.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ARMED"] = "true"
os.environ["DUPLICATE_SIGNAL_WINDOW_SECONDS"] = "1"
os.environ["MAX_TRADES_PER_HOUR"] = "1000"
os.environ["MAX_CONSECUTIVE_LOSSES"] = "1000"
os.environ["COOLDOWN_AFTER_LOSS_SECONDS"] = "0"
os.environ["MINIMUM_PAYOUT"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from trade_coordinator import TradeCoordinator
import database

FAST = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "15 Seconds", "raw_message": "outcome-independence fast"}
SLOW = {"asset": "GBP/USD OTC", "direction": "SELL", "expiry": "1 Minute", "raw_message": "outcome-independence slow"}


async def _wait_resolved(trade_id, label):
    t0 = time.monotonic()
    while True:
        conn = database.get_connection()
        row = conn.execute(
            "SELECT execution_status, outcome_detection_ms FROM signals WHERE id = ?", (trade_id,)
        ).fetchone()
        conn.close()
        if row["execution_status"] in ("result_win", "result_loss", "result_draw", "error"):
            elapsed = time.monotonic() - t0
            print(f"[{label}] resolved after {elapsed:.1f}s wall (from fire), "
                  f"outcome_detection_ms={row['outcome_detection_ms']}")
            return elapsed
        await asyncio.sleep(1)


async def main():
    print("OUTCOME-DETECTION INDEPENDENCE TEST - DEMO ACCOUNT ONLY")
    print("ARMED=true is set for this process only, .env is untouched")

    warmup = BrowserWarmupService()
    await warmup.start()
    pool = BrowserWorkerPool(warmup, num_workers=2)
    await pool.start()
    coordinator = TradeCoordinator(pool, warmup)

    print("Firing FAST (15s) and SLOW (60s) trades concurrently...")
    result_fast, result_slow = await asyncio.gather(
        coordinator.handle_signal(FAST, source="outcome-independence-test", sender="tester", message_id=99001),
        coordinator.handle_signal(SLOW, source="outcome-independence-test", sender="tester", message_id=99002),
    )
    print("fast:", result_fast)
    print("slow:", result_slow)

    if result_fast["status"] != "clicked" or result_slow["status"] != "clicked":
        print("UNEXPECTED: expected both to click. Not proceeding to outcome wait.")
        await pool.stop()
        await warmup.stop()
        return

    fast_elapsed, slow_elapsed = await asyncio.gather(
        _wait_resolved(result_fast["trade_id"], "FAST(15s)"),
        _wait_resolved(result_slow["trade_id"], "SLOW(60s)"),
    )

    print(f"\nFAST resolved at {fast_elapsed:.1f}s, SLOW resolved at {slow_elapsed:.1f}s (from fire, shared t0)")
    if fast_elapsed < slow_elapsed - 10:
        print("PASS: fast trade resolved well before the slow one - not coupled to it.")
    else:
        print("FAIL: fast trade did not resolve meaningfully earlier than the slow one - "
              "the outcome-detection independence fix may have regressed.")

    await pool.stop()
    await warmup.stop()


if __name__ == "__main__":
    asyncio.run(main())
