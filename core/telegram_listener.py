from telethon import TelegramClient, events
import os
import sys
import time
from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't encode most emoji/non-
# Latin characters that arrive in real Telegram messages - forcing UTF-8
# here means print() never crashes the process over a character the
# terminal can't represent (previously fixed ad hoc per-script; this is
# the one place that matters since this is the live entry point).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "parsers")
sys.path.insert(0, "execution")
sys.path.insert(0, "core")
sys.path.insert(0, "config")

from signal_parser import parse_signal
from trade_coordinator import TradeCoordinator
from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from timeline import TradeTimeline
from settings import WATCH_CHANNELS, MAX_CONCURRENT_WORKERS
from logger import get_logger
import recovery
import database

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

# Originated as a one-off WATCH_CHANNELS/filter debugging aid; kept as a
# permanent, opt-in observability feature - logs every incoming message's
# routing decision BEFORE any filtering, execution-unrelated. Does not touch
# trade_coordinator, pocket_executor, risk_manager, or pocket_dom. Off by
# default (set TELEGRAM_DEBUG_LOG=true to enable).
TELEGRAM_DEBUG_LOG = os.getenv("TELEGRAM_DEBUG_LOG", "false").strip().lower() == "true"

client = TelegramClient("axim_session", api_id, api_hash)

# Rebuilt fresh on every (re)start by _startup() - not constructed once at
# module load, since a process-level restart needs a clean browser/worker
# stack rather than reusing objects that may be in a broken state.
warmup_service = None
worker_pool = None
coordinator = None

print("AXIM Telegram Listener Starting...")

database.initialize_database()
# One-time migration: populate ui_channels from the static .env
# WATCH_CHANNELS the very first time this runs against a DB that's never
# seen the UI channel manager - a no-op on every subsequent start (the
# function itself checks the table isn't empty first). From here on,
# ui_channels (editable via the API/web UI without touching .env or
# restarting) is the real source of truth; WATCH_CHANNELS still works too
# (see channel_allowed below) so nothing regresses for anyone not using
# the UI yet.
database.seed_channels_from_env(WATCH_CHANNELS)

if not WATCH_CHANNELS and not database.get_enabled_channels():
    print(
        "\n*** WARNING: no channel is allow-listed (.env WATCH_CHANNELS is\n"
        "empty and no channel is enabled in the UI channel manager). ***\n"
        "AXIM will NOT process signals from ANY chat until you enable at\n"
        "least one. This is intentional: previously this listener processed\n"
        "every message in every chat the account could see - fail-closed by\n"
        "default is safer than that.\n"
    )
else:
    print(f"Watching for chat titles containing: {WATCH_CHANNELS} (plus anything enabled in the UI channel manager)")


def channel_allowed(chat_title, chat_username=None):
    """A chat is allowed if it matches EITHER the static .env WATCH_CHANNELS
    list OR a currently-enabled row in the UI-managed ui_channels table -
    same two-way match either way: a case-insensitive substring of the
    chat's display title/first_name (works for anything, but display names
    can be renamed or duplicated - e.g. this account has both "Pocket
    Option Quant Algorithm" and a "PO Quant Algo" contact), or an exact
    case-insensitive match against the chat's @username, which Telegram
    guarantees is unique and immutable - the more reliable of the two for
    allow-listing a specific bot/channel. Fail-closed if neither source has
    anything configured."""
    title_lower = (chat_title or "").lower()
    username_lower = (chat_username or "").lower()

    if any(
        channel.lower() in title_lower or channel.lower() == username_lower
        for channel in WATCH_CHANNELS
    ):
        return True

    for row in database.get_enabled_channels():
        entry_username = (row["username"] or "").lower()
        entry_title = (row["title"] or "").lower()
        if (entry_username and entry_username == username_lower) or (
            entry_title and entry_title in title_lower
        ):
            return True

    return False


def _debug_safe(text):
    """Belt-and-suspenders alongside the UTF-8 stdout reconfigure above -
    guards print() calls if stdout is ever swapped for a stream that
    doesn't support .reconfigure, so a stray unencodable character never
    crashes the process. Never touches what actually gets parsed or
    executed."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return (text or "").encode(encoding, "replace").decode(encoding, "replace")


@client.on(events.NewMessage)
async def handler(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""
    chat_username = getattr(chat, "username", "") or ""

    if TELEGRAM_DEBUG_LOG:
        debug_sender = await event.get_sender()
        filter_decision = channel_allowed(chat_title, chat_username)
        print("\n[TELEGRAM_DEBUG] ------------------------------")
        print(f"[TELEGRAM_DEBUG] chat_title    : {_debug_safe(chat_title)}")
        print(f"[TELEGRAM_DEBUG] chat_username : {_debug_safe(chat_username)}")
        print(f"[TELEGRAM_DEBUG] sender_id     : {getattr(debug_sender, 'id', None)}")
        print(f"[TELEGRAM_DEBUG] chat_id       : {event.chat_id}")
        print(f"[TELEGRAM_DEBUG] raw_text[:200]: {_debug_safe(event.raw_text)[:200]}")
        print(f"[TELEGRAM_DEBUG] filter_decision: {'ALLOWED' if filter_decision else 'BLOCKED'}")
        if not filter_decision:
            print("[TELEGRAM_DEBUG] parser_decision: (not evaluated - blocked by filter)")
        else:
            debug_signal = parse_signal(event.raw_text)
            print(f"[TELEGRAM_DEBUG] parser_decision: {'PARSED -> ' + repr(debug_signal) if debug_signal else 'REJECTED (returned None)'}")

    if not channel_allowed(chat_title, chat_username):
        return

    database.record_channel_signal_seen(chat_id=event.chat_id, username=chat_username, title=chat_title)

    control_state = database.get_control_state()
    if control_state["emergency_stop"] or control_state["paused"]:
        reason = "emergency_stop" if control_state["emergency_stop"] else "paused"
        print(f"\n[UI CONTROL] Signal from {_debug_safe(chat_title)!r} skipped - AXIM is {reason} via the UI.")
        return

    timeline = TradeTimeline()
    timeline.mark("signal_received")

    sender = await event.get_sender()

    print("\n========================")
    print("New Telegram Message")
    print("========================")
    print(f"Chat   : {_debug_safe(chat_title)}")
    print(f"Sender : {sender.id}")
    print(f"Message:\n{_debug_safe(event.raw_text)}")

    signal = parse_signal(event.raw_text)
    timeline.mark("signal_parsed")

    if signal:
        execution_result = await coordinator.handle_signal(
            signal,
            source=chat_title,
            sender=str(sender.id),
            message_id=event.id,
            sent_at=event.date,
            timeline=timeline,
        )

        print(f"Execution Status: {execution_result['status']}")

        print("\nAXIM SIGNAL PARSED")
        print(f"Asset     : {signal['asset']}")
        print(f"Direction : {signal['direction']}")
        print(f"Expiry    : {signal['expiry']}")


async def _startup():
    """(Re)builds the browser/worker-pool stack and runs startup recovery.
    Called both on first launch and after every process-level restart -
    each call creates a fresh BrowserWarmupService/BrowserWorkerPool rather
    than reusing instances that may be left in a broken state by whatever
    triggered the restart."""
    global warmup_service, worker_pool, coordinator
    print("AXIM: starting persistent Pocket Option session...")
    warmup_service = BrowserWarmupService()
    await warmup_service.start()
    print(f"AXIM: starting browser worker pool ({MAX_CONCURRENT_WORKERS} worker(s))...")
    worker_pool = BrowserWorkerPool(warmup_service, num_workers=MAX_CONCURRENT_WORKERS)
    await worker_pool.start()
    coordinator = TradeCoordinator(worker_pool, warmup_service)
    print("AXIM: worker pool ready, running startup recovery...")
    await recovery.run_recovery(warmup_service)


async def _shutdown():
    global warmup_service, worker_pool
    if worker_pool is not None:
        try:
            await worker_pool.stop()
        except Exception as e:
            logger.error("telegram_listener: error stopping worker pool: %s", e)
    if warmup_service is not None:
        try:
            await warmup_service.stop()
        except Exception as e:
            logger.error("telegram_listener: error stopping browser: %s", e)


def run_forever():
    """Top-level 24/7 supervisor (P0 requirement: continuous operation with
    automatic recovery). Rebuilds the whole browser/worker-pool/Telegram
    connection stack and retries with exponential backoff (capped) on any
    unexpected failure - a crashed browser that couldn't self-heal, a
    Telegram-side disconnect, or any other unhandled error - so the process
    survives without manual intervention. Complements the existing
    browser-level (BrowserWarmupService) and worker-level
    (BrowserWorkerPool) recovery, which handle failures that don't need a
    full process restart; this is the outermost layer for the failures
    that do.

    Records "process_restart" succeeded/failed recovery_events - matching
    the other 3 recovery layers (browser_reconnect, worker_pool_rebuild,
    resume_open_trade), which all record a real terminal outcome, not just
    an attempt. Only recorded for actual RESTARTS (following a prior
    disconnect or failure), not the initial clean startup, which isn't a
    recovery from anything.

    Ctrl+C (or SIGTERM via SystemExit) shuts down cleanly and does not
    retry - only genuinely unexpected failures trigger a restart."""
    backoff = 1
    max_backoff = 60
    is_restart = False
    while True:
        try:
            client.loop.run_until_complete(_startup())
            client.start(phone=phone)
            print("Connected to Telegram")
            if is_restart:
                database.record_recovery_event("process_restart", "succeeded", "listener operational again after restart")
            backoff = 1  # reset only after a fully successful (re)start
            is_restart = False
            client.run_until_disconnected()
            # A clean, intentional stop arrives as KeyboardInterrupt/
            # SystemExit below, not a normal return here - reaching this
            # point means the connection dropped unexpectedly. Not itself
            # a recovery outcome - it's the failure that the NEXT
            # successful (or failed) restart attempt reports on.
            logger.warning("telegram_listener: client disconnected unexpectedly - restarting")
            is_restart = True
        except (KeyboardInterrupt, SystemExit):
            print("\nAXIM: shutdown requested, closing cleanly...")
            client.loop.run_until_complete(_shutdown())
            raise
        except Exception as e:
            logger.error("telegram_listener: unhandled error, restarting: %s", e)
            database.record_recovery_event("process_restart", "failed", str(e))
            is_restart = True
            try:
                client.loop.run_until_complete(_shutdown())
            except Exception as shutdown_error:
                logger.error("telegram_listener: error during shutdown before restart: %s", shutdown_error)

        print(f"AXIM: restarting in {backoff}s...")
        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    run_forever()
