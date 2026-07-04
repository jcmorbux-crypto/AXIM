from telethon import TelegramClient, events
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, "parsers")
sys.path.insert(0, "execution")
sys.path.insert(0, "core")

from signal_parser import parse_signal
from trade_coordinator import TradeCoordinator
from browser_warmup import BrowserWarmupService
from latency import LatencyTracker
import recovery

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

client = TelegramClient("axim_session", api_id, api_hash)
warmup_service = BrowserWarmupService()
coordinator = TradeCoordinator(warmup_service)

print("AXIM Telegram Listener Starting...")


@client.on(events.NewMessage)
async def handler(event):
    latency = LatencyTracker()
    latency.mark("telegram_received")

    sender = await event.get_sender()
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""

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
print("AXIM: persistent session ready, running startup recovery...")
client.loop.run_until_complete(recovery.run_recovery(warmup_service))

client.start(phone=phone)

print("Connected to Telegram")

client.run_until_disconnected()
