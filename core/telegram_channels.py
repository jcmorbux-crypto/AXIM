"""
AXIM UI - Telegram channel manager backend.

Lists the real dialogs (channels/groups/bots) the account can see and
syncs them into the `ui_channels` DB table, which is what
core/telegram_listener.py's allow-list reads from once the UI is in use.

Uses its OWN, separate Telethon session (`axim_ui_session`) rather than
the live listener's `axim_session` - a Telethon session file is a SQLite
db and can't be opened by two processes at once (same reason
core/source_observer.py already uses its own session), so this can safely
run while the listener is live.

Read-only with respect to trading: never imports trade_coordinator,
pocket_executor, or risk_manager.
"""
import os
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from dotenv import load_dotenv
from telethon import TelegramClient

import database
from logger import get_logger

load_dotenv()

logger = get_logger("axim.ui", filename="ui.log")

UI_SESSION_NAME = os.getenv("UI_SESSION_NAME", "axim_ui_session")

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")


def _dialog_kind(dialog):
    if dialog.is_user:
        return "user"
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "unknown"


async def sync_dialogs():
    """Connects with the dedicated UI session, lists every real dialog,
    and upserts identity (chat_id/username/title/kind) into ui_channels -
    never touches the `enabled` flag, so this is safe to call as often as
    the UI wants (e.g. a "Refresh" button) without undoing the operator's
    own choices. Returns the count synced."""
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.start(phone=phone)
    count = 0
    try:
        async for dialog in client.iter_dialogs():
            username = getattr(dialog.entity, "username", None)
            title = dialog.name or ""
            database.upsert_channel(
                chat_id=dialog.id,
                username=username,
                title=title,
                kind=_dialog_kind(dialog),
            )
            count += 1
    finally:
        await client.disconnect()
    logger.info("telegram_channels: synced %d dialog(s) via %s", count, UI_SESSION_NAME)
    return count


if __name__ == "__main__":
    import asyncio
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    database.initialize_database()
    n = asyncio.run(sync_dialogs())
    print(f"Synced {n} dialog(s) into ui_channels.")
