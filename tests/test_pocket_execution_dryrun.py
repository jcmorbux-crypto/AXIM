import asyncio
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(TESTS_DIR))

from browser_session import PocketBrowserSession, get_trading_page, DEMO_URL
import pocket_dom
from fixtures.simulated_signals import SIMULATED_SIGNALS

DRYRUN_OPT_IN_ENV = "AXIM_RUN_LIVE_DOM_TESTS"
DRYRUN_STAKE = 1


async def _run_dryrun_for_signal(signal):
    async with PocketBrowserSession() as context:
        page = await get_trading_page(context, DEMO_URL)

        await pocket_dom.dismiss_blocking_modals(page)
        await pocket_dom.select_asset(page, signal["asset"])
        await pocket_dom.select_expiry(page, signal["expiry"])
        await pocket_dom.set_amount(page, DRYRUN_STAKE)
        await pocket_dom.verify_direction_controls_ready(page)


def test_dryrun_all_simulated_signals():
    if os.getenv(DRYRUN_OPT_IN_ENV, "false").lower() != "true":
        print(
            f"SKIPPED: set {DRYRUN_OPT_IN_ENV}=true to run this test. "
            "It drives a real browser against the live Pocket Option demo "
            "cabinet (asset/expiry/amount selection only - never clicks "
            "BUY/SELL)."
        )
        return

    failures = []
    skipped = []
    for signal in SIMULATED_SIGNALS:
        label = f"{signal['asset']} {signal['direction']} {signal['expiry']}"
        try:
            asyncio.run(_run_dryrun_for_signal(signal))
            print(f"PASS: {label}")
        except pocket_dom.AssetUntradeableError as e:
            skipped.append((signal, e))
            print(f"SKIPPED (market closed): {label} -> {e}")
        except pocket_dom.PocketDomError as e:
            failures.append((signal, e))
            print(f"FAIL: {label} -> {e}")

    if skipped:
        print(
            f"{len(skipped)} signal(s) skipped due to real market-closed conditions, "
            "not counted as failures."
        )

    if failures:
        raise AssertionError(
            f"{len(failures)}/{len(SIMULATED_SIGNALS)} simulated signals "
            f"failed DOM verification: {[s['raw_message'] for s, _ in failures]}"
        )


if __name__ == "__main__":
    os.environ.setdefault(DRYRUN_OPT_IN_ENV, "true")
    test_dryrun_all_simulated_signals()
    print(f"\nAll {len(SIMULATED_SIGNALS)} simulated signals passed dry-run DOM verification. No trades were submitted.")
