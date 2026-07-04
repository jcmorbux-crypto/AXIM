from telethon import TelegramClient, events
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, "parsers")
sys.path.insert(0, "execution")
sys.path.insert(0, "core")
sys.path.insert(0, "config")

from signal_parser import parse_signal
from trade_coordinator import TradeCoordinator
from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from latency import LatencyTracker
from settings import WATCH_CHANNELS, MAX_CONCURRENT_WORKERS
import recovery

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

client = TelegramClient("axim_session", api_id, api_hash)
warmup_service = BrowserWarmupService()
worker_pool = BrowserWorkerPool(warmup_service, num_workers=MAX_CONCURRENT_WORKERS)
coordinator = TradeCoordinator(worker_pool)

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


def source_allowed(chat_title):
    if not WATCH_CHANNELS:
        return False
    title_lower = (chat_title or "").lower()
    return any(channel.lower() in title_lower for channel in WATCH_CHANNELS)


@client.on(events.NewMessage)
async def handler(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""

    if not source_allowed(chat_title):
        return

    latency = LatencyTracker()
    latency.mark("telegram_received")

    sender = await event.get_sender()

    print("\n========================")
    print("New Telegram Message")
    print("========================")
    print(f"Chat   : {chat_title}")
    print(f"Sender : {sender.id}")
    print(f"Message:\n{event.raw_text}")

    signal = parse_signal(event.raw_text)
    latency.mark("parsed")

    if signal:
        execution_result = await coordinator.handle_signal(
            signal,
            source=chat_title,
            sender=str(sender.id),
            message_id=event.id,
            sent_at=event.date,
            latency=latency,
        )

        print(f"Execution Status: {execution_result['status']}")

        print("\nAXIM SIGNAL PARSED")
        print(f"Asset     : {signal['asset']}")
        print(f"Direction : {signal['direction']}")
        print(f"Expiry    : {signal['expiry']}")


print("AXIM: starting persistent Pocket Option session...")
client.loop.run_until_complete(warmup_service.start())
print(f"AXIM: starting browser worker pool ({MAX_CONCURRENT_WORKERS} worker(s))...")
client.loop.run_until_complete(worker_pool.start())
print("AXIM: worker pool ready, running startup recovery...")
client.loop.run_until_complete(recovery.run_recovery(worker_pool))

client.start(phone=phone)

print("Connected to Telegram")

client.run_until_disconnected()
