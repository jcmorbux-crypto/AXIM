from telethon import TelegramClient, events
from dotenv import load_dotenv
import os
import re
import asyncio

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")

client = TelegramClient("axim_session", api_id, api_hash)

pending_signal = None

asset_regex = re.compile(r"([A-Z]{3}/[A-Z]{3}\sOTC)\sM(\d+)", re.IGNORECASE)


@client.on(events.NewMessage)
async def new_message(event):
    global pending_signal

    text = event.raw_text.strip()

    # Ignore your own messages
    if event.out:
        return

    print(f"\nTelegram: {text}")

    asset = asset_regex.search(text)

    if asset:
        pending_signal = {
            "asset": asset.group(1).upper(),
            "expiry": int(asset.group(2))
        }

        print("Waiting for direction...")
        return

    upper = text.upper()

    if pending_signal:

        if "UP" in upper:
            direction = "BUY"

        elif "DOWN" in upper:
            direction = "SELL"

        else:
            return

        print("\n======================")
        print("EMMA SIGNAL DETECTED")
        print("======================")
        print(f"Asset     : {pending_signal['asset']}")
        print(f"Direction : {direction}")
        print(f"Expiry    : {pending_signal['expiry']} Minutes")
        print("======================\n")

        pending_signal = None


async def main():
    print("AXIM is listening...")

    await client.start(phone)

    await client.run_until_disconnected()


asyncio.run(main())