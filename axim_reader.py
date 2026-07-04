import os
import re
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")

sources = [
    source.strip().lower()
    for source in os.getenv("SIGNAL_SOURCES", "").split(",")
    if source.strip()
]

client = TelegramClient("axim_session", api_id, api_hash)

pending_signal = None

asset_pattern = re.compile(r"([A-Z]{3}/[A-Z]{3}\s+OTC)\s+M(\d+)", re.IGNORECASE)


def source_allowed(chat_title):
    return any(source in chat_title.lower() for source in sources)


@client.on(events.NewMessage)
async def handler(event):
    global pending_signal

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""

    if not source_allowed(chat_title):
        return

    text = event.raw_text.strip()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {chat_title}: {text}")

    asset_match = asset_pattern.search(text)

    if asset_match:
        pending_signal = {
            "source": chat_title,
            "asset": asset_match.group(1).upper(),
            "expiry": int(asset_match.group(2)),
            "time": datetime.now()
        }

        print(f"Pending: {pending_signal['asset']} M{pending_signal['expiry']}")
        return

    if pending_signal:
        upper = text.upper()

        if "UP" in upper:
            direction = "BUY"
        elif "DOWN" in upper:
            direction = "SELL"
        else:
            return

        print("\n==============================")
        print("AXIM SIGNAL DETECTED")
        print("==============================")
        print(f"Source    : {pending_signal['source']}")
        print(f"Asset     : {pending_signal['asset']}")
        print(f"Expiry    : {pending_signal['expiry']} minutes")
        print(f"Direction : {direction}")
        print("==============================\n")

        pending_signal = None


async def main():
    print("AXIM Reader starting...")
    print(f"Watching sources: {sources}")
    await client.start(phone)
    print("AXIM is listening for signals...")
    await client.run_until_disconnected()


asyncio.run(main())