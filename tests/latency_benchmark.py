"""
MANUAL, ONE-OFF BENCHMARK - not part of the automated suite, never runs
automatically. Fires a controlled sequential series of real demo trades
through the full TradeCoordinator -> pocket_executor -> pocket_dom pipeline
and records real per-stage latency (LatencyTracker.summary()) plus outcome
data, for a before/after comparison across the P0 latency/reliability sprint.

Alternates asset (forcing the "asset must change" path every other trade)
and direction (BUY/SELL, so consecutive same-asset trades aren't flagged as
duplicates) across N trades on the SAME two known-tradeable OTC pairs used
throughout this project's prior testing.

Risk-rule thresholds that would otherwise contaminate a pure latency
measurement (duplicate window, trades/hour cap, consecutive-loss cooldown,
minimum payout) are relaxed via os.environ for THIS PROCESS ONLY, set before
importing settings/risk_manager - the checked-in .env is never touched, and
this script measures execution latency, not risk-rule behavior.

ARMED is forced to "true" for this process only, same as
manual_click_test_warm.py.

Run only when explicitly instructed to benchmark the execution pipeline.
Usage: python tests/latency_benchmark.py <label>
  <label> becomes the output file logs/benchmark_<label>.json
"""
import asyncio
import json
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
LOG_DIR = PROJECT_ROOT / "logs"
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from trade_coordinator import TradeCoordinator
from latency import LatencyTracker
import database

ASSETS = ["EUR/USD OTC", "GBP/USD OTC"]
EXPIRY = "15 Seconds"
NUM_TRADES = 10


def _build_signal(i):
    asset = ASSETS[(i // 2) % 2]
    direction = "BUY" if i % 2 == 0 else "SELL"
    return {
        "asset": asset, "direction": direction, "expiry": EXPIRY,
        "raw_message": f"latency benchmark trade {i}",
    }


async def _wait_for_resolution(trade_id, max_wait=60):
    for _ in range(max_wait):
        conn = database.get_connection()
        row = conn.execute(
            "SELECT execution_status, result, profit_loss FROM signals WHERE id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()
        if row["execution_status"] in ("result_win", "result_loss", "result_draw", "error"):
            return dict(row)
        await asyncio.sleep(1)
    return None


async def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    print(f"LATENCY BENCHMARK ({label}) - DEMO ACCOUNT ONLY, ARMED=true for this process only")
    print(f"Risk thresholds relaxed for this process only (.env untouched) - measuring latency, not risk rules")
    print("=" * 70)

    # One worker per trade fired: each trade gets an immediately-free
    # worker, so the "browser latency" stage measures true acquire/health-
    # check overhead, not queueing behind a still-busy worker whose prior
    # trade hasn't resolved yet (track_outcome holds a worker for the full
    # expiry+settlement window in the background).
    warmup = BrowserWarmupService()
    await warmup.start()
    pool = BrowserWorkerPool(warmup, num_workers=NUM_TRADES)
    await pool.start()
    coordinator = TradeCoordinator(pool)

    samples = []
    for i in range(NUM_TRADES):
        signal = _build_signal(i)
        expected_nochange = (i % 2 == 1)  # odd trades repeat the prior asset
        latency = LatencyTracker()
        t0 = time.monotonic()
        result = await coordinator.handle_signal(
            signal, source="latency-benchmark", sender="benchmark", message_id=95000 + i,
            latency=latency,
        )
        wall_elapsed = time.monotonic() - t0
        checkpoints = latency.summary()
        print(f"[{i}] {signal['asset']} {signal['direction']} -> {result.get('status')} "
              f"wall={wall_elapsed:.3f}s checkpoints={checkpoints}")
        samples.append({
            "index": i, "asset": signal["asset"], "direction": signal["direction"],
            "expected_nochange": expected_nochange, "status": result.get("status"),
            "trade_id": result.get("trade_id"), "wall_seconds": wall_elapsed,
            "checkpoints_ms": checkpoints,
        })

    # Wait for outcome resolution on the trades that actually clicked, to
    # also capture real outcome-detection timing.
    clicked = [s for s in samples if s["status"] == "clicked"]
    print(f"\nWaiting for {len(clicked)} clicked trade(s) to resolve...")
    for s in clicked:
        t0 = time.monotonic()
        outcome = await _wait_for_resolution(s["trade_id"])
        s["outcome_wait_seconds"] = time.monotonic() - t0
        s["outcome"] = outcome

    await pool.stop()
    await warmup.stop()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / f"benchmark_{label}.json"
    out_path.write_text(json.dumps(samples, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
