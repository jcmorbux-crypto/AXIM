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
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

from dotenv import load_dotenv
from telethon import TelegramClient

import database
from logger import get_logger
from signal_parser import parse_signal

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


# Caps how many historical messages a single import can scan - large
# enough for a genuinely useful signal-history pull, small enough that
# one request can't run for minutes or exhaust real Telegram API quota
# on an accidental huge limit. A documented, enforced limit rather than
# a silent one, same discipline as backtest_routes.py's synchronous-
# simulation size limit.
MAX_HISTORY_SCAN = 2000


def _message_to_signal_row(text, message_date, message_id, source_label):
    """Pure - no Telethon/network dependency, so this is unit-testable
    without mocking TelegramClient. Returns a row shaped exactly like
    core/backtest_engine.py's CSV/Excel import rows, or None if the
    message text doesn't parse as a signal at all (chatter, images,
    "good morning" posts - not a failure, just not a signal)."""
    signal = parse_signal(text or "")
    if signal is None:
        return None
    received_at = (message_date or datetime.now()).isoformat()
    return {
        "source_label": source_label, "asset": signal["asset"], "direction": signal["direction"],
        "expiry": signal.get("expiry"), "received_at": received_at,
        "result": None, "payout_percent": None,
        "notes": f"Imported from Telegram history (message id {message_id})",
    }


async def fetch_channel_history(chat_id, limit=200, source_label=None):
    """Scans up to `limit` of a channel's most recent real messages
    (Telethon's default iter_messages order: newest first) via the
    dedicated UI session, parsing each with the SAME
    parsers.signal_parser.parse_signal() the live listener uses for
    real-time signals (via _message_to_signal_row) - so a message this
    recognizes is exactly what AXIM would have recognized had it
    arrived live. Returns (rows, messages_scanned) - result and
    payout_percent are always None, since a signal message alone never
    carries its own outcome, matching api/backtest_routes.py's existing
    import-csv/import-excel row shape so the caller can reuse the same
    database.create_imported_signal loop."""
    limit = min(limit, MAX_HISTORY_SCAN)
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.start(phone=phone)
    rows = []
    scanned = 0
    try:
        entity = await client.get_entity(chat_id)
        label = source_label or getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_id)
        async for message in client.iter_messages(chat_id, limit=limit):
            scanned += 1
            row = _message_to_signal_row(message.raw_text, message.date, message.id, label)
            if row is not None:
                rows.append(row)
    finally:
        await client.disconnect()
    logger.info(
        "telegram_channels: fetch_channel_history chat_id=%s scanned=%d signals_found=%d",
        chat_id, scanned, len(rows),
    )
    return rows, scanned


if __name__ == "__main__":
    import asyncio
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    database.initialize_database()
    n = asyncio.run(sync_dialogs())
    print(f"Synced {n} dialog(s) into ui_channels.")
