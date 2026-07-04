"""
MANUAL, ONE-OFF INVESTIGATION - not part of the automated suite. Tests
whether asset selection (.current-symbol) is cross-tab-shared the same way
the Opened/Closed trades-panel tab was already confirmed to be (Phase 5).

Real production history shows one occurrence of select_asset's own
post-click assertion failing with a wrong-asset readback ("Locator expected
to have text 'GBP/JPY'. Actual value: EUR/USD OT..."). This test tries to
reproduce that directly: two workers select DIFFERENT assets as close to
simultaneously as asyncio.gather allows, repeated N times, then each page's
actual .current-symbol is read back and compared against what THAT worker
requested - independent of select_asset's own internal assertion, in case
that assertion itself is part of what's racy.

Read-only with respect to trading (uses select_asset only, never clicks
BUY/SELL) - does not require ARMED=true.

Run only when explicitly instructed to investigate this.
"""
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
import pocket_dom

ASSET_A = "EUR/USD OTC"
ASSET_B = "GBP/USD OTC"
ITERATIONS = 8


async def _select_and_read_back(page, asset):
    await pocket_dom.select_asset(page, asset)
    actual = await pocket_dom._read_current_asset(page)
    return actual


async def main():
    print("CROSS-TAB ASSET-SELECTION RACE TEST - DEMO ACCOUNT ONLY, no clicks")
    warmup = BrowserWarmupService()
    await warmup.start()
    pool = BrowserWorkerPool(warmup, num_workers=2)
    await pool.start()

    worker_a = await pool.acquire_worker(timeout=5)
    worker_b = await pool.acquire_worker(timeout=5)
    print(f"worker_a={worker_a.worker_id} worker_b={worker_b.worker_id}")

    mismatches = []
    for i in range(ITERATIONS):
        # Alternate which worker asks for which asset each round, so a
        # one-directional bug (e.g. "last writer wins") isn't masked by
        # always racing the same pairing.
        if i % 2 == 0:
            want_a, want_b = ASSET_A, ASSET_B
        else:
            want_a, want_b = ASSET_B, ASSET_A

        got_a, got_b = await asyncio.gather(
            _select_and_read_back(worker_a.page, want_a),
            _select_and_read_back(worker_b.page, want_b),
        )

        ok_a = (got_a == want_a)
        ok_b = (got_b == want_b)
        print(f"[{i}] worker_a wanted={want_a!r} got={got_a!r} ok={ok_a} | "
              f"worker_b wanted={want_b!r} got={got_b!r} ok={ok_b}")

        if not ok_a:
            mismatches.append(("worker_a", i, want_a, got_a))
        if not ok_b:
            mismatches.append(("worker_b", i, want_b, got_b))

    pool.release_worker(worker_a)
    pool.release_worker(worker_b)
    await pool.stop()
    await warmup.stop()

    print("\n" + "=" * 70)
    if mismatches:
        print(f"FINDING: {len(mismatches)} mismatch(es) out of {ITERATIONS * 2} selections.")
        print("Asset selection DOES appear to be cross-tab-shared/racy:")
        for m in mismatches:
            print(f"  {m}")
    else:
        print(f"FINDING: 0 mismatches out of {ITERATIONS * 2} concurrent selections.")
        print("No evidence in this run that .current-symbol is cross-tab-shared.")


if __name__ == "__main__":
    asyncio.run(main())
