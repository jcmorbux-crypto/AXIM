"""Broker Account Manager connect flow (docs/AXIM_APP_PLAN.md) - opens a
real, visible browser window against ONE broker account's own persistent
profile directory so an operator can manually log in to that specific
Pocket Option account. Pocket Option has no documented safe programmatic
login in this codebase (PO_EMAIL/PO_PASSWORD have always been unused
placeholders) - this formalizes the same manual-login-once, cookies-
persist-after pattern every broker account before this feature used
implicitly, as an explicit, repeatable, per-account action instead of a
single shared profile.

Run as a standalone subprocess (spawned by api/broker_accounts_routes.py's
POST /connect, same subprocess-based pattern api/process_control.py already
uses for the listener) rather than inside the FastAPI process itself - a
manual login can take an arbitrary, unbounded amount of real time, which
doesn't fit inside one HTTP request/response cycle. This script owns the
browser and writes the outcome directly to core/database.py (a one-off
setup tool, not part of the live trading pipeline, same as
scripts/backup_axim_state.ps1 touching state directly).

Usage: python scripts/connect_broker_account.py <broker_account_id>
"""
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXECUTION_DIR = PROJECT_ROOT / "execution"
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

from browser_session import PocketBrowserSession, get_trading_page, DEMO_URL
import pocket_dom
import database
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

LOGIN_URL = "https://pocketoption.com/en/login/"
# How long to keep polling for a successful login before giving up and
# marking the account 'error' - generous, since this is a human typing a
# password/passing 2FA, not a machine operation.
CONNECT_TIMEOUT_SECONDS = 15 * 60
POLL_INTERVAL_SECONDS = 5


async def _verify_logged_in(context):
    """Non-intrusive check run periodically WHILE the operator's own tab
    stays untouched: opens a throwaway tab at the real trading URL and
    checks for the same asset-selector browser_session.get_trading_page
    already treats as "the trading page genuinely loaded" - a login
    redirect would never reach that element. Closes the throwaway tab
    either way so it doesn't clutter the window the operator is using."""
    page = await context.new_page()
    try:
        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=10_000)
        await page.locator(pocket_dom.SEL_ASSET_TRIGGER).first.wait_for(state="visible", timeout=5_000)
        return True
    except Exception:
        return False
    finally:
        await page.close()


async def connect(account_id):
    account = database.get_broker_account(account_id)
    if account is None:
        print(f"No broker account with id {account_id}")
        return

    database.update_broker_account(account_id, connection_status="connecting")
    print(f"Opening a browser window for broker account {account_id!r} ({account['name']}).")
    print("Log in to Pocket Option in that window. This script detects success automatically.")

    session = PocketBrowserSession(user_data_dir=PROJECT_ROOT / account["user_data_dir"])
    context = await session.__aenter__()
    connected = False
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if await _verify_logged_in(context):
                connected = True
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        if connected:
            database.update_broker_account(
                account_id, connection_status="connected",
                last_connected_at=datetime.now().isoformat(),
            )
            print(f"Broker account {account_id!r} connected successfully.")
            logger.info("connect_broker_account: account_id=%s connected", account_id)
        else:
            database.update_broker_account(account_id, connection_status="error")
            print(f"Broker account {account_id!r} did not reach a logged-in state within "
                  f"{CONNECT_TIMEOUT_SECONDS}s - marked as error. The window is left open; "
                  f"you can keep trying and reconnect from the UI once logged in.")
            logger.warning("connect_broker_account: account_id=%s timed out", account_id)
            # Leave the window open on timeout - don't yank control from an
            # operator who may still be mid-login (slow 2FA, etc).
            return
    finally:
        if connected:
            await session.__aexit__(None, None, None)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/connect_broker_account.py <broker_account_id>")
        sys.exit(1)
    asyncio.run(connect(int(sys.argv[1])))
