from telethon import TelegramClient, events
import os
import sys
import time
from dotenv import load_dotenv

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

client = TelegramClient("axim_session", api_id, api_hash)

# Rebuilt fresh on every (re)start by _startup() - not constructed once at
# module load, since a process-level restart needs a clean browser/worker
# stack rather than reusing objects that may be in a broken state.
warmup_service = None
worker_pool = None
coordinator = None

print("AXIM Telegram Listener Starting...")

if not WATCH_CHANNELS:
    print(
        "\n*** WARNING: WATCH_CHANNELS is empty - no chat is allow-listed. ***\n"
        "AXIM will NOT process signals from ANY chat until you set\n"
        "WATCH_CHANNELS=<comma-separated substrings of channel/chat titles>\n"
        "in .env. This is intentional: previously this listener processed\n"
        "every message in every chat the account could see - fail-closed by\n"
        "default is safer than that.\n"
    )
else:
    print(f"Watching for chat titles containing: {WATCH_CHANNELS}")


def source_allowed(chat_title, chat_username=None):
    """WATCH_CHANNELS entries are matched two ways: as a case-insensitive
    substring of the chat's display title/first_name (works for anything,
    but display names can be renamed or duplicated - e.g. this account has
    both "Pocket Option Quant Algorithm" and a "PO Quant Algo" contact), and
    as an exact case-insensitive match against the chat's @username, which
    Telegram guarantees is unique and immutable - the more reliable of the
    two for allow-listing a specific bot/channel. Either match is
    sufficient; still fail-closed if WATCH_CHANNELS is empty."""
    if not WATCH_CHANNELS:
        return False
    title_lower = (chat_title or "").lower()
    username_lower = (chat_username or "").lower()
    return any(
        channel.lower() in title_lower or channel.lower() == username_lower
        for channel in WATCH_CHANNELS
    )


@client.on(events.NewMessage)
async def handler(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""
    chat_username = getattr(chat, "username", "") or ""

    if not source_allowed(chat_title, chat_username):
        return

    timeline = TradeTimeline()
    timeline.mark("signal_received")

    sender = await event.get_sender()

    print("\n========================")
    print("New Telegram Message")
    print("========================")
    print(f"Chat   : {chat_title}")
    print(f"Sender : {sender.id}")
    print(f"Message:\n{event.raw_text}")

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
    coordinator = TradeCoordinator(worker_pool)
    print("AXIM: worker pool ready, running startup recovery...")
    await recovery.run_recovery(worker_pool)


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

    Ctrl+C (or SIGTERM via SystemExit) shuts down cleanly and does not
    retry - only genuinely unexpected failures trigger a restart."""
    backoff = 1
    max_backoff = 60
    while True:
        try:
            client.loop.run_until_complete(_startup())
            client.start(phone=phone)
            print("Connected to Telegram")
            backoff = 1  # reset only after a fully successful (re)start
            client.run_until_disconnected()
            # A clean, intentional stop arrives as KeyboardInterrupt/
            # SystemExit below, not a normal return here - reaching this
            # point means the connection dropped unexpectedly.
            logger.warning("telegram_listener: client disconnected unexpectedly - restarting")
            database.record_recovery_event("process_restart", "attempted", "client disconnected unexpectedly")
        except (KeyboardInterrupt, SystemExit):
            print("\nAXIM: shutdown requested, closing cleanly...")
            client.loop.run_until_complete(_shutdown())
            raise
        except Exception as e:
            logger.error("telegram_listener: unhandled error, restarting: %s", e)
            database.record_recovery_event("process_restart", "attempted", str(e))
            try:
                client.loop.run_until_complete(_shutdown())
            except Exception as shutdown_error:
                logger.error("telegram_listener: error during shutdown before restart: %s", shutdown_error)

        print(f"AXIM: restarting in {backoff}s...")
        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    run_forever()
