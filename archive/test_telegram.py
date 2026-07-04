from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")

print("Starting AXIM...")

client = TelegramClient("axim_session", api_id, api_hash)

async def main():
    me = await client.get_me()
    print(f"✅ Connected as: {me.first_name}")
    print(f"Phone: {me.phone}")

with client:
    client.loop.run_until_complete(main())