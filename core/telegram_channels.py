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
from datetime import datetime, timedelta, timezone
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl import functions

import database
from logger import get_logger
from signal_parser import parse_signal

load_dotenv()

logger = get_logger("axim.ui", filename="ui.log")

UI_SESSION_NAME = os.getenv("UI_SESSION_NAME", "axim_ui_session")

# Default source universe (V1 product directive): scope channel sync to
# this Telegram folder, rather than every dialog in the account
# (personal chats, unrelated groups, etc. - a real, confirmed source of
# noise this project already hit once, see the empty-title-channel
# lookup-collision fix in core/database.py). A configurable override via
# .env, not hardcoded, since a fresh install may name its folder
# differently.
DEFAULT_SYNC_FOLDER = os.getenv("TELEGRAM_SYNC_FOLDER", "OPT SIGNALS")


async def _find_folder(client, folder_name):
    """Returns the full Telegram DialogFilter object whose title matches
    folder_name (case-insensitive, whitespace-trimmed), or None if the
    account has no such folder. A DialogFilter's title is a
    TextWithEntities object in current Telethon/MTProto, not a plain
    string - .text is the actual folder name. Some filter types
    (DialogFilterDefault, the "All Chats" pseudo-folder) have no real
    title at all and are safely skipped rather than raising.

    Returns the whole filter, not just its id: confirmed live against a
    real account that TelegramClient.iter_dialogs(folder=...) does NOT
    accept a DialogFilter id (it raises FolderIdInvalidError - that
    parameter only understands the legacy Archived pseudo-folder).
    A folder's real membership is its pinned_peers/include_peers/
    exclude_peers plus category flags, evaluated by _dialog_in_folder."""
    try:
        response = await client(functions.messages.GetDialogFiltersRequest())
    except Exception as e:
        logger.warning("telegram_channels: could not list Telegram folders: %s", e)
        return None
    for f in getattr(response, "filters", []):
        title_obj = getattr(f, "title", None)
        title_text = getattr(title_obj, "text", None) if title_obj is not None else None
        if title_text and title_text.strip().lower() == folder_name.strip().lower():
            return f
    return None


def _peer_id(peer):
    """InputPeerChannel/InputPeerChat/InputPeerUser each carry the bare
    entity id under a different attribute name - this is the same id
    Telethon exposes as dialog.entity.id (unlike dialog.id, which is the
    "marked" id with a -100 prefix for channels)."""
    return getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)


def _dialog_in_folder(dialog, dialog_filter):
    """Pure - mirrors Telegram's own DialogFilter membership rules:
    explicit pinned_peers/include_peers always count (minus
    exclude_peers), plus whichever category flags the folder has turned
    on (groups/broadcasts/bots/contacts/non_contacts). The real "OPT
    SIGNALS" folder found live uses only explicit peers with every
    category flag off, but a fresh/differently-configured install's
    folder might rely on the category flags instead, so both are honored
    rather than only the common case."""
    entity_id = getattr(dialog.entity, "id", None)
    exclude_ids = {_peer_id(p) for p in (dialog_filter.exclude_peers or [])}
    if entity_id in exclude_ids:
        return False
    include_ids = {_peer_id(p) for p in list(dialog_filter.pinned_peers or []) + list(dialog_filter.include_peers or [])}
    if entity_id in include_ids:
        return True
    if dialog_filter.groups and dialog.is_group:
        return True
    if dialog_filter.broadcasts and dialog.is_channel and not dialog.is_group:
        return True
    is_bot = bool(getattr(dialog.entity, "bot", False))
    if dialog_filter.bots and is_bot:
        return True
    if dialog.is_user and not is_bot:
        is_contact = bool(getattr(dialog.entity, "contact", False))
        if dialog_filter.contacts and is_contact:
            return True
        if dialog_filter.non_contacts and not is_contact:
            return True
    return False


def _telegram_credentials():
    """Read fresh at call time, not at import. Found live (2026-07-11):
    api/main.py imports this module eagerly at server startup, and a
    brand-new install's .env legitimately has no real TELEGRAM_API_ID yet
    (.env.example's placeholder isn't numeric) - Telegram linking is an
    in-app step done AFTER the server is already running (see
    docs/AXIM_SETUP_GUIDE.md), so a missing/placeholder credential here
    must not crash server startup itself. Previously `int(os.getenv(...))`
    ran at module level and did exactly that."""
    raw_id = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID")
    if not raw_id or not raw_id.isdigit():
        raise RuntimeError(
            "Telegram is not linked yet - connect it from the Signal Sources page first"
        )
    api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
    phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")
    return int(raw_id), api_hash, phone


def _dialog_kind(dialog):
    if dialog.is_user:
        return "user"
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "unknown"


async def sync_dialogs(folder_name=DEFAULT_SYNC_FOLDER):
    """Connects with the dedicated UI session and upserts identity
    (chat_id/username/title/kind) into ui_channels for dialogs in the
    given Telegram folder - never touches the `enabled` flag, so this is
    safe to call as often as the UI wants (e.g. a "Refresh" button)
    without undoing the operator's own choices. Returns the count synced.

    folder_name defaults to DEFAULT_SYNC_FOLDER ("OPT SIGNALS" unless
    overridden via .env) - the V1 product directive's required default
    source universe, so a fresh account isn't flooded with personal
    chats/unrelated groups. Pass folder_name=None to sync every dialog
    (the old, pre-folder-scoping behavior) - also the automatic fallback
    if the named folder doesn't exist in this Telegram account, since
    silently syncing zero channels would be a worse regression than
    syncing everything."""
    api_id, api_hash, phone = _telegram_credentials()
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.start(phone=phone)
    count = 0
    try:
        dialog_filter = None
        if folder_name:
            dialog_filter = await _find_folder(client, folder_name)
            if dialog_filter is None:
                logger.warning(
                    "telegram_channels: folder '%s' not found - syncing all dialogs instead",
                    folder_name,
                )
        async for dialog in client.iter_dialogs():
            if dialog_filter is not None and not _dialog_in_folder(dialog, dialog_filter):
                continue
            username = getattr(dialog.entity, "username", None)
            title = dialog.name or ""
            database.upsert_channel(
                chat_id=dialog.id,
                username=username,
                title=title,
                kind=_dialog_kind(dialog),
                in_default_folder=True if dialog_filter is not None else None,
            )
            count += 1
    finally:
        await client.disconnect()
    logger.info("telegram_channels: synced %d dialog(s) via %s (folder=%s)", count, UI_SESSION_NAME, folder_name)
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
    api_id, api_hash, phone = _telegram_credentials()
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


async def fetch_channel_raw_history(chat_id, limit=1000, source_label=None, days=None):
    """Same connection/credential handling as fetch_channel_history, but
    returns EVERY message's raw text unfiltered (not run through
    parsers.signal_parser.parse_signal first) - core/
    provider_language_learner.py needs the full, real batch (including
    promotional chatter, results, session boilerplate) to detect which
    structural pattern this provider actually uses, not just the subset
    the live parser already recognizes. Returns (messages, source_title)
    where messages is [{"message_id", "text", "date_utc"}, ...] in
    chronological order (oldest first - the shape the learner and
    backtest engine both expect, opposite of Telethon's own newest-first
    iteration).

    days, if given (the Provider Onboarding Wizard's history-window
    selector - 7/14/30/60/90, default 30), stops scanning once a message
    is older than that many days back - Telethon iterates newest-first,
    so this is a simple early break, not a second pass. Still capped by
    limit/MAX_HISTORY_SCAN either way, whichever is hit first - a very
    high-volume channel over 90 days shouldn't scan unboundedly."""
    limit = min(limit, MAX_HISTORY_SCAN)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    api_id, api_hash, phone = _telegram_credentials()
    client = TelegramClient(UI_SESSION_NAME, api_id, api_hash)
    await client.start(phone=phone)
    messages = []
    try:
        entity = await client.get_entity(chat_id)
        title = source_label or getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_id)
        async for message in client.iter_messages(chat_id, limit=limit):
            message_date = message.date or datetime.now(timezone.utc)
            if cutoff is not None and message_date < cutoff:
                break
            messages.append({
                "message_id": message.id, "text": message.raw_text or "",
                "date_utc": message_date.isoformat(),
            })
    finally:
        await client.disconnect()
    messages.reverse()  # Telethon iterates newest-first; the learner/backtest engine expect oldest-first
    logger.info(
        "telegram_channels: fetch_channel_raw_history chat_id=%s messages=%d days=%s", chat_id, len(messages), days,
    )
    return messages, title


if __name__ == "__main__":
    import asyncio
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    database.initialize_database()
    n = asyncio.run(sync_dialogs())
    print(f"Synced {n} dialog(s) into ui_channels.")
