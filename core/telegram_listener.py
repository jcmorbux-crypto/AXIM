from telethon import TelegramClient, events
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, "parsers")
sys.path.insert(0, "execution")
sys.path.insert(0, "core")

from signal_parser import parse_signal
from trade_coordinator import TradeCoordinator
import recovery

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

client = TelegramClient("axim_session", api_id, api_hash)
coordinator = TradeCoordinator()

print("AXIM Telegram Listener Starting...")


@client.on(events.NewMessage)
async def handler(event):
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

    if signal:
        execution_result = await coordinator.handle_signal(
            signal,
            source=chat_title,
            sender=str(sender.id),
            message_id=event.id,
            sent_at=event.date,
        )

        print(f"Execution Status: {execution_result['status']}")

        print("\nAXIM SIGNAL PARSED")
        print(f"Asset     : {signal['asset']}")
        print(f"Direction : {signal['direction']}")
        print(f"Expiry    : {signal['expiry']}")


client.loop.run_until_complete(recovery.run_recovery())

client.start(phone=phone)

print("Connected to Telegram")

client.run_until_disconnected()