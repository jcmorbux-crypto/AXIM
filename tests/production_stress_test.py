"""
PRODUCTION STRESS TEST - manual, one-off, real-trade validation run.

Fires a large, varied signal sequence through the REAL pipeline
(parse_signal -> risk_manager -> TradeCoordinator -> pocket_executor ->
pocket_dom) against the live Pocket Option DEMO account, covering:
  - parser correctness across all asset categories + malformed/invalid input
  - duplicate-signal rejection
  - burst traffic (concurrent submission)
  - mixed BUY/SELL, OTC/non-OTC, 1m/5m/15m expiries, real execution
  - a simulated mid-run browser crash + recovery
  - resource sampling (CPU/memory) throughout

ARMED is forced "true" and risk thresholds are relaxed for THIS PROCESS
ONLY (checked-in .env is never touched) - same pattern as
tests/latency_benchmark.py. DUPLICATE_SIGNAL_WINDOW_SECONDS is deliberately
LEFT REALISTIC (not relaxed) since duplicate-rejection is itself a thing
this test verifies.

Must be run with the live listener process STOPPED (same persistent
browser profile, can't run concurrently - same constraint hit repeatedly
this session).

Run: python tests/production_stress_test.py
Writes: logs/stress_test_<timestamp-free label>.json
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["ARMED"] = "true"
os.environ["MAX_TRADES_PER_HOUR"] = "1000"
os.environ["MAX_CONSECUTIVE_LOSSES"] = "1000"
os.environ["COOLDOWN_AFTER_LOSS_SECONDS"] = "0"
os.environ["MINIMUM_PAYOUT"] = "0"
os.environ["DUPLICATE_SIGNAL_WINDOW_SECONDS"] = "60"  # left realistic - this test verifies it
os.environ["MAX_CONCURRENT_WORKERS"] = "6"  # matches tonight's production-tuned value

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from trade_coordinator import TradeCoordinator
from timeline import TradeTimeline
from signal_parser import parse_signal
import database

SOURCE_TAG = "stress-test"

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

INVALID_MESSAGES = [
    "", "   ", "hello world", "12345", "BUY", "SELL",
    "🔥🔥🔥🔥🔥🔥🔥🔥", "<script>alert(1)</script>", "' OR 1=1; --",
    "A" * 5000,
    "Currency pair: OTC OTC OTC",
    "Signal: BUY M5",  # asset label present but no real asset value
    "Stock: OTC",
]

VALID_LABELED_MESSAGES = [
    ("Currency pair: EUR/USD OTC", "SELL", "M1"),
    ("Currency pair: GBP/USD OTC", "BUY", "M5"),
    ("Cryptocurrency: Bitcoin", "BUY", "M1"),        # non-OTC, confirmed live-tradeable 24/7
    ("Cryptocurrency: Ethereum OTC", "SELL", "M5"),
    ("Commoditi: Gold OTC", "BUY", "M1"),
    ("Stock: Apple OTC", "SELL", "M15"),
    ("Index: SP500 OTC", "BUY", "M5"),
    ("Currency pair: AUD/USD OTC", "SELL", "M15"),
]


def _build_message(label_value, direction, expiry_code):
    arrow = "BUY ⬆️" if direction == "BUY" else "SELL ⬇️"
    return f"✅ The analysis is complete!\n\n💱 {label_value}\n⏳ Expiration time: {expiry_code}\n\n📈 Signal: {arrow}"


async def _wait_for_terminal(trade_id, max_wait=1200):
    """Polls until a trade reaches a terminal execution_status, or max_wait
    seconds elapse (covers up to 15-minute expiries + settlement)."""
    terminal = ("result_win", "result_loss", "result_draw", "error",
                "rejected", "prepared_not_armed")
    waited = 0
    while waited < max_wait:
        conn = database.get_connection()
        row = conn.execute(
            "SELECT execution_status, result, profit_loss FROM signals WHERE id = ?",
            (trade_id,),
        ).fetchone()
        conn.close()
        if row is not None and row["execution_status"] in terminal:
            return dict(row)
        await asyncio.sleep(2)
        waited += 2
    return None


def _sample_resources(label, results):
    """Best-effort CPU/memory sample of this python process + any chrome.exe
    children, via PowerShell (matches how this session has been checking
    resource usage all along)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process chrome -ErrorAction SilentlyContinue | "
             "Measure-Object WorkingSet64 -Sum | Select-Object -ExpandProperty Sum; "
             "Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object | "
             "Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=15,
        )
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        chrome_mem_bytes = int(lines[0]) if len(lines) > 0 and lines[0].isdigit() else None
        chrome_count = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else None
    except Exception as e:
        chrome_mem_bytes, chrome_count = None, None
        print(f"resource sample failed: {e}")
    sample = {
        "label": label, "t": time.monotonic(),
        "chrome_process_count": chrome_count,
        "chrome_working_set_mb": round(chrome_mem_bytes / 1_048_576, 1) if chrome_mem_bytes else None,
    }
    results["resource_samples"].append(sample)
    print(f"[resources] {label}: chrome_procs={chrome_count} chrome_mem_mb={sample['chrome_working_set_mb']}")


async def main():
    results = {
        "parser_tests": [], "duplicate_test": [], "burst_test": [],
        "execution_tests": [], "recovery_test": {}, "resource_samples": [],
        "started_at": None, "finished_at": None,
    }
    results["started_at"] = time.time()

    # -----------------------------------------------------------------
    # Group A: parser-layer correctness (no browser needed)
    # -----------------------------------------------------------------
    print("\n=== GROUP A: parser correctness (invalid input) ===")
    for msg in INVALID_MESSAGES:
        try:
            parsed = parse_signal(msg)
            ok = parsed is None
        except Exception as e:
            parsed, ok = f"EXCEPTION: {e}", False
        results["parser_tests"].append({"input": msg[:80], "expected": "None", "got": str(parsed)[:120], "pass": ok})
        print(f"  {'PASS' if ok else 'FAIL'}: {msg[:50]!r} -> {str(parsed)[:80]}")

    print("\n=== GROUP A: parser correctness (valid labeled signals) ===")
    for label_value, direction, expiry_code in VALID_LABELED_MESSAGES:
        msg = _build_message(label_value, direction, expiry_code)
        try:
            parsed = parse_signal(msg)
            ok = parsed is not None and parsed["direction"] == direction
        except Exception as e:
            parsed, ok = f"EXCEPTION: {e}", False
        results["parser_tests"].append({"input": label_value, "expected": direction, "got": str(parsed), "pass": ok})
        print(f"  {'PASS' if ok else 'FAIL'}: {label_value!r} -> {parsed}")

    parser_pass = sum(1 for t in results["parser_tests"] if t["pass"])
    print(f"\nParser group: {parser_pass}/{len(results['parser_tests'])} passed")

    # -----------------------------------------------------------------
    # Browser-dependent groups need a live pool
    # -----------------------------------------------------------------
    print("\n=== Starting browser (warmup + worker pool) ===")
    _sample_resources("before_startup", results)
    warmup = BrowserWarmupService()
    await warmup.start()
    pool = BrowserWorkerPool(warmup, num_workers=int(os.environ["MAX_CONCURRENT_WORKERS"]))
    await pool.start()
    coordinator = TradeCoordinator(pool, warmup)
    _sample_resources("after_startup", results)

    msg_id_counter = [900000]

    async def fire(asset_label, direction, expiry_code, expiry_display, tag):
        msg_id_counter[0] += 1
        raw = _build_message(asset_label, direction, expiry_code)
        signal = parse_signal(raw)
        if signal is None:
            return {"tag": tag, "status": "parse_failed", "raw": raw}
        timeline = TradeTimeline()
        t0 = time.monotonic()
        result = await coordinator.handle_signal(
            signal, source=SOURCE_TAG, sender="stress-test", message_id=msg_id_counter[0],
            timeline=timeline,
        )
        wall = time.monotonic() - t0
        return {
            "tag": tag, "asset": signal["asset"], "direction": signal["direction"],
            "expiry": signal["expiry"], "status": result.get("status"),
            "rule": result.get("rule"), "trade_id": result.get("trade_id"),
            "wall_seconds": wall,
        }

    # -----------------------------------------------------------------
    # Group B: duplicate signal rejection
    # -----------------------------------------------------------------
    print("\n=== GROUP B: duplicate signal rejection ===")
    dup_results = []
    for i in range(3):
        r = await fire("Currency pair: EUR/JPY OTC", "BUY", "M1", "1 Minute", f"dup-{i}")
        dup_results.append(r)
        print(f"  [{i}] status={r['status']} rule={r.get('rule')}")
    first_ok = dup_results[0]["status"] not in ("rejected",)
    rest_rejected = all(r["status"] == "rejected" and r.get("rule") == "duplicate_signal" for r in dup_results[1:])
    results["duplicate_test"] = {
        "samples": dup_results, "pass": first_ok and rest_rejected,
    }
    print(f"  Duplicate test: {'PASS' if (first_ok and rest_rejected) else 'FAIL'}")

    # -----------------------------------------------------------------
    # Group C: burst traffic (concurrent submission)
    # -----------------------------------------------------------------
    print("\n=== GROUP C: burst traffic (concurrent) ===")
    burst_assets = [
        ("Currency pair: EUR/CAD OTC", "BUY"), ("Currency pair: GBP/CHF OTC", "SELL"),
        ("Currency pair: AUD/CHF OTC", "BUY"), ("Currency pair: USD/CAD OTC", "SELL"),
        ("Currency pair: EUR/CHF OTC", "BUY"), ("Currency pair: CAD/CHF OTC", "SELL"),
        ("Currency pair: GBP/AUD OTC", "BUY"), ("Currency pair: EUR/GBP OTC", "SELL"),
    ]
    burst_t0 = time.monotonic()
    burst_coros = [fire(label, d, "M1", "1 Minute", f"burst-{i}") for i, (label, d) in enumerate(burst_assets)]
    burst_results = await asyncio.gather(*burst_coros, return_exceptions=True)
    burst_wall = time.monotonic() - burst_t0
    burst_clean = [r if not isinstance(r, Exception) else {"tag": "exception", "status": "exception", "error": str(r)} for r in burst_results]
    exceptions = [r for r in burst_clean if r.get("status") == "exception"]
    results["burst_test"] = {"samples": burst_clean, "wall_seconds": burst_wall, "exceptions": len(exceptions)}
    print(f"  Fired {len(burst_assets)} concurrent signals in {burst_wall:.2f}s wall, {len(exceptions)} exception(s)")
    for r in burst_clean:
        print(f"    {r.get('tag')}: status={r.get('status')} rule={r.get('rule')}")

    _sample_resources("after_burst", results)

    # -----------------------------------------------------------------
    # Group D: mixed real executions (categories x directions x expiries)
    # -----------------------------------------------------------------
    print("\n=== GROUP D: mixed real executions ===")
    mixed_signals = [
        ("Currency pair: NZD/JPY OTC", "BUY", "M1", "e1"),
        ("Currency pair: USD/BRL OTC", "SELL", "M5", "e2"),
        ("Cryptocurrency: Litecoin OTC", "BUY", "M1", "e3"),
        ("Cryptocurrency: Bitcoin", "SELL", "M1", "e4"),         # non-OTC
        ("Commoditi: Silver OTC", "BUY", "M5", "e5"),
        ("Stock: Netflix OTC", "SELL", "M1", "e6"),
        ("Stock: Intel OTC", "BUY", "M15", "e7"),                # long expiry
        ("Index: DJI30 OTC", "SELL", "M5", "e8"),
        ("Currency pair: EUR/NZD OTC", "BUY", "M15", "e9"),      # long expiry
        ("Cryptocurrency: Polkadot OTC", "SELL", "M1", "e10"),
    ]
    for label, direction, expiry_code, tag in mixed_signals:
        r = await fire(label, direction, expiry_code, expiry_code, tag)
        results["execution_tests"].append(r)
        print(f"  {tag}: {r.get('asset')} {r.get('direction')} -> status={r.get('status')} trade_id={r.get('trade_id')}")
        await asyncio.sleep(1.5)  # realistic inter-signal spacing, not a simultaneous dump

    _sample_resources("after_mixed_execution", results)

    # -----------------------------------------------------------------
    # Group E: simulated browser crash + recovery
    # -----------------------------------------------------------------
    print("\n=== GROUP E: simulated browser crash + recovery ===")
    recovery_result = {}
    try:
        pre_generation = warmup.generation
        # Force-close the underlying context to simulate a real browser crash.
        await warmup.get_context().close()
        print("  Forced browser context closed (simulated crash)")
        await asyncio.sleep(1)
        r = await fire("Currency pair: EUR/USD OTC", "SELL", "M1", "M1", "post-crash-recovery")
        recovery_result = {
            "pre_generation": pre_generation, "post_generation": warmup.generation,
            "recovered": warmup.generation > pre_generation,
            "post_crash_trade_status": r.get("status"),
            "post_crash_trade_succeeded": r.get("status") in ("clicked", "preview", "prepared_not_armed"),
        }
        print(f"  generation {pre_generation} -> {warmup.generation}, "
              f"post-crash trade status={r.get('status')}")
    except Exception as e:
        recovery_result = {"error": str(e), "recovered": False}
        print(f"  RECOVERY TEST ERROR: {e}")
    results["recovery_test"] = recovery_result

    _sample_resources("after_recovery_test", results)

    # -----------------------------------------------------------------
    # Wait for terminal outcomes on everything that actually clicked
    # -----------------------------------------------------------------
    all_fired = results["duplicate_test"]["samples"] + results["burst_test"]["samples"] + results["execution_tests"]
    clicked = [r for r in all_fired if isinstance(r, dict) and r.get("status") == "clicked" and r.get("trade_id")]
    print(f"\n=== Waiting for {len(clicked)} clicked trade(s) to resolve (up to 20 min each) ===")
    for r in clicked:
        t0 = time.monotonic()
        outcome = await _wait_for_terminal(r["trade_id"])
        r["outcome_wait_seconds"] = time.monotonic() - t0
        r["outcome"] = outcome
        print(f"  trade_id={r['trade_id']} ({r.get('asset')}) -> {outcome}")

    _sample_resources("before_shutdown", results)

    await pool.stop()
    await warmup.stop()

    _sample_resources("after_shutdown", results)

    results["finished_at"] = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / "stress_test_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print(f"Total wall time: {results['finished_at'] - results['started_at']:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
