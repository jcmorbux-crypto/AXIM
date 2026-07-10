"""
MANUAL, ONE-OFF TEST - not part of the automated suite, never runs
automatically. Passively observes real WebSocket traffic during one real
$1 demo trade to identify readable (JSON event-name, not binary-payload)
signals for trade open/close - the investigation behind
docs/AXIM_COMPETITIVE_BENCHMARK.md item 4.

Findings from the last real run (see that doc for full detail): the
trading socket (wss://demo-api-eu.po.market/socket.io/, Socket.IO - a
public protocol, not proprietary to Pocket Option) fires a
`successopenOrder` event within ~100ms of the real trade-opened moment,
and `successcloseOrder` right at the trade's own expiry. Only the EVENT
NAME and TIMING are used/reported here - the actual payload is Socket.IO's
binary-attachment convention and decoding it would mean reverse-engineering
a Pocket-Option-specific format, which is out of scope (same boundary this
whole project has held throughout: "no reverse-engineering of Pocket
Option's proprietary client or backend").

Re-run this if/when a production WS-triggered-early-check integration is
being considered, to reconfirm the event names still hold before building
against them - trading platforms change their client code without notice.

ARMED is forced to "true" for THIS PROCESS ONLY via an environment
variable set before importing pocket_executor (which reads ARMED at
module import time) - the checked-in .env is never touched, and the real
listener process is unaffected.

Run only when explicitly instructed to re-investigate this.
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

SIGNAL = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "15 Seconds", "raw_message": "ws investigation trade"}

frame_log = []
t0 = None


def on_websocket(ws):
    def on_frame_received(payload):
        frame_log.append((time.monotonic() - t0, "recv", ws.url, payload))
    ws.on("framereceived", on_frame_received)


async def _wait_for_resolution(trade_id, max_wait=60):
    for _ in range(max_wait):
        conn = database.get_connection()
        row = conn.execute(
            "SELECT execution_status, result, profit_loss FROM signals WHERE id = ?", (trade_id,),
        ).fetchone()
        conn.close()
        if row["execution_status"] in ("result_win", "result_loss", "result_draw", "error"):
            print(f"Resolved: {row['execution_status']} result={row['result']} profit_loss={row['profit_loss']}")
            return True
        await asyncio.sleep(1)
    print("WARNING: did not resolve within max_wait")
    return False


async def main():
    global t0
    t0 = time.monotonic()
    print("WEBSOCKET OBSERVATION DURING A REAL DEMO TRADE - $1, EUR/USD OTC, 15s expiry")
    print("ARMED=true is set for THIS PROCESS ONLY, .env is untouched")
    print("=" * 70)

    warmup = BrowserWarmupService()
    await warmup.start()

    # Attach at the CONTEXT level, not just the warmup page - the actual
    # trade gets placed on a BrowserWorkerPool worker's own page (a
    # separate tab), which has its own independent WebSocket
    # connections. This catches every page's WS traffic, existing and
    # future, not just the warmup page's.
    context = warmup.get_context()
    for existing_page in context.pages:
        existing_page.on("websocket", on_websocket)
    context.on("page", lambda new_page: new_page.on("websocket", on_websocket))

    pool = BrowserWorkerPool(warmup, num_workers=1)
    await pool.start()
    coordinator = TradeCoordinator(pool, warmup)

    print(f"[{time.monotonic()-t0:.1f}s] Placing trade...")
    result = await coordinator.handle_signal(SIGNAL, source="ws-investigation", sender="tester", message_id=90099)
    print(f"[{time.monotonic()-t0:.1f}s] prepare_trade result: {result}")

    if result.get("status") == "clicked":
        await _wait_for_resolution(result["trade_id"], max_wait=45)
    print(f"[{time.monotonic()-t0:.1f}s] Waiting a few more seconds to catch any post-close frames...")
    await asyncio.sleep(5)

    await pool.stop()
    await warmup.stop()

    print("=" * 70)
    print(f"Total frames captured: {len(frame_log)}")
    print("\n--- Every TEXT (non-binary) frame, with timestamp ---")
    for t, direction, url, payload in frame_log:
        if isinstance(payload, str):
            # Skip the continuous updateStream placeholder noise (real-time
            # price ticks, unrelated to trade open/close) - only show
            # anything ELSE readable, which is what we're looking for.
            if "updateStream" in payload:
                continue
            print(f"[{t:6.2f}s][{direction}] {url[:50]} :: {payload[:300]!r}")

    print(f"\n--- Binary frame count (not decoded, out of scope) ---")
    binary_count = sum(1 for _, _, _, p in frame_log if not isinstance(p, str))
    print(f"{binary_count} binary frames received during the trade window")


if __name__ == "__main__":
    asyncio.run(main())
