"""
MANUAL, ONE-OFF TEST - not part of the automated suite, never runs
automatically. Performs a single real click on Pocket Option's DEMO
account (virtual balance only) to validate click_direction() and its
post-click confirmation check, which cannot be proven without an actual
click.

This bypasses pocket_executor.py / ARMED entirely by calling pocket_dom
functions directly - it does not read, use, or modify ARMED in any way.

Run only when explicitly instructed to validate the click step.
"""
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

from browser_session import PocketBrowserSession, get_trading_page, DEMO_URL
import pocket_dom

TEST_ASSET = "EUR/USD OTC"
TEST_DIRECTION = "BUY"
TEST_EXPIRY = "15 Seconds"
TEST_AMOUNT = 1


async def main():
    print("MANUAL CLICK TEST - DEMO ACCOUNT ONLY")
    print(f"Asset: {TEST_ASSET}  Direction: {TEST_DIRECTION}  Expiry: {TEST_EXPIRY}  Amount: ${TEST_AMOUNT}")
    print("=" * 60)

    async with PocketBrowserSession() as context:
        page = await get_trading_page(context, DEMO_URL)

        await pocket_dom.dismiss_blocking_modals(page)
        await pocket_dom.select_asset(page, TEST_ASSET)
        await pocket_dom.select_expiry(page, TEST_EXPIRY)
        await pocket_dom.set_amount(page, TEST_AMOUNT)
        await pocket_dom.verify_direction_controls_ready(page)

        print("All pre-click verification passed. Clicking", TEST_DIRECTION, "now.")

        await pocket_dom.click_direction(page, TEST_DIRECTION)

        print("click_direction() returned without raising - confirmation check passed")
        print("(Opened trades list is no longer showing 'No opened trades')")

        evidence_dir = PROJECT_ROOT / "logs" / "manual_click_test"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(evidence_dir / "post_click.png"))
        html = await page.content()
        (evidence_dir / "post_click.html").write_text(html, encoding="utf-8")
        print(f"Evidence saved to {evidence_dir}")

        # Give the trade a moment to appear/settle on screen before closing,
        # for visual confirmation only (not a wait the pipeline depends on).
        await page.wait_for_timeout(5000)


if __name__ == "__main__":
    asyncio.run(main())
