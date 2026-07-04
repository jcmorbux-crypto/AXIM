"""
MANUAL, ONE-OFF TEST - not part of the automated suite, never runs
automatically. Validates the multi-worker concurrency architecture's real
click path end-to-end: BrowserWarmupService (shared context) ->
BrowserWorkerPool (N warm pages, each its own lock) -> TradeCoordinator ->
pocket_executor.prepare_trade (worker's lock held through the click) ->
lock transferred to the background track_outcome task -> trade resolves ->
worker released back to the pool.

Fires TWO overlapping signals (different assets) concurrently to confirm
they land on different workers and neither blocks the other - the actual
point of this architecture, not just that a single click still works.

ARMED is forced to "true" for THIS PROCESS ONLY via an environment
variable set before importing pocket_executor (which reads ARMED at
module import time) - the checked-in .env is never touched, and the real
listener process is unaffected.

Run only when explicitly instructed to validate the click step.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ARMED"] = "true"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from trade_coordinator import TradeCoordinator
import database

SIGNAL_A = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "15 Seconds", "raw_message": "concurrency test A"}
SIGNAL_B = {"asset": "GBP/USD OTC", "direction": "SELL", "expiry": "15 Seconds", "raw_message": "concurrency test B"}


async def _wait_for_resolution(trade_id, label, max_wait=60):
    for _ in range(max_wait):
        conn = database.get_connection()
        row = conn.execute(
            "SELECT execution_status, result, profit_loss FROM signals WHERE id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()
        if row["execution_status"] in ("result_win", "result_loss", "result_draw", "error"):
            print(f"[{label}] Resolved: {row['execution_status']} result={row['result']} profit_loss={row['profit_loss']}")
            return True
        await asyncio.sleep(1)
    print(f"[{label}] WARNING: did not resolve within {max_wait}s")
    return False


async def main():
    print("MULTI-WORKER CONCURRENCY TEST - DEMO ACCOUNT ONLY")
    print("ARMED=true is set for this process only, .env is untouched")
    print(f"Signal A: {SIGNAL_A['asset']} {SIGNAL_A['direction']}")
    print(f"Signal B: {SIGNAL_B['asset']} {SIGNAL_B['direction']}")
    print("=" * 70)

    warmup = BrowserWarmupService()
    await warmup.start()

    pool = BrowserWorkerPool(warmup, num_workers=2)
    await pool.start()
    print(f"Pool started with {pool.num_workers} worker(s)")

    coordinator = TradeCoordinator(pool)

    t0 = time.monotonic()
    result_a, result_b = await asyncio.gather(
        coordinator.handle_signal(SIGNAL_A, source="concurrency-test", sender="tester", message_id=90001),
        coordinator.handle_signal(SIGNAL_B, source="concurrency-test", sender="tester", message_id=90002),
    )
    elapsed = time.monotonic() - t0

    print(f"\nBoth signals completed in {elapsed:.2f}s (wall clock, run concurrently)")
    print("Result A:", result_a)
    print("Result B:", result_b)

    if result_a["status"] != "clicked" or result_b["status"] != "clicked":
        print("UNEXPECTED: expected both to click. Not proceeding to outcome wait.")
        await pool.stop()
        await warmup.stop()
        return

    print(
        "\nBoth workers now busy tracking outcomes - pool should report 0 "
        f"immediately available: available_qsize={pool._available.qsize()}"
    )

    await asyncio.gather(
        _wait_for_resolution(result_a["trade_id"], "A"),
        _wait_for_resolution(result_b["trade_id"], "B"),
    )

    print(f"\nAfter resolution, available_qsize={pool._available.qsize()} (expected {pool.num_workers})")

    await pool.stop()
    await warmup.stop()


if __name__ == "__main__":
    asyncio.run(main())
