"""
AXIM Research Module - Source Observer

Passively monitors messages from a Telegram signal source your account
already has access to (default: @PocketOption_quant_algorithm_bot),
purely to study its timing and signal behavior.

Explicit boundaries:
- Read-only. Never joins, starts, or interacts with the source beyond
  normal Telegram message reception your account is already authorized for.
- Never scrapes credentials, bypasses protections, or reverse-engineers
  anything - it uses the same Telethon user-account API the rest of AXIM
  already uses, with your own credentials.
- Never executes trades. This file does not import trade_coordinator,
  pocket_executor, or risk_manager, and never will - that is a structural
  guarantee, not just a runtime check.

Run: python core/source_observer.py
"""
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import RPCError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"

sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
from signal_parser import parse_signal

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

# Deliberately a separate session from the live listener's "axim_session" -
# avoids sqlite lock contention if both run at once, which is expected
# (comparing this source's timing against AXIM's own execution timing
# means the observer typically runs alongside the live listener).
SESSION_NAME = os.getenv("RESEARCH_SESSION_NAME", "axim_observer_session")
SOURCE_USERNAME = os.getenv("RESEARCH_SOURCE_USERNAME", "PocketOption_quant_algorithm_bot").lstrip("@")

DB_FILE = DATA_DIR / "axim.db"

from logger import get_logger

logger = get_logger("axim.source_observer", filename="source_observer.log")

# Best-effort scan for countdown/timing language. Not an exhaustive parser -
# it flags phrases worth a human (or source_profiler.py) reviewing, since
# there is no reliable structured way to extract "seconds until entry" from
# arbitrary signal-channel text.
_TIMING_RE = re.compile(
    r"\bnow\b|\bimmediate(?:ly)?\b|\bwait\b|\bget ready\b|\bentry\s*(?:at|in)\b|"
    r"\bcountdown\b|\bnext candle\b|\bon the (?:open|close)\b|"
    r"\b\d{1,3}\s*(?:sec|second|seconds|min|minute|minutes)\b",
    re.IGNORECASE,
)


def extract_timing_language(text):
    spans = [m.group(0) for m in _TIMING_RE.finditer(text or "")]
    if not spans:
        return None
    return "; ".join(dict.fromkeys(spans))  # de-duplicate, preserve order


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_observations_table():
    conn = get_connection()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS source_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        sender_id TEXT,
        message_id INTEGER,
        raw_message TEXT,
        received_at TEXT,
        message_date TEXT,
        asset TEXT,
        direction TEXT,
        expiry TEXT,
        timing_language TEXT,
        parsed_successfully INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    conn.close()


def record_observation(source, sender_id, message_id, raw_message, message_date, signal):
    conn = get_connection()
    conn.execute("""
        INSERT INTO source_observations (
            source, sender_id, message_id, raw_message, received_at, message_date,
            asset, direction, expiry, timing_language, parsed_successfully
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        source,
        sender_id,
        message_id,
        raw_message,
        datetime.now().isoformat(),
        message_date.isoformat() if message_date else None,
        signal["asset"] if signal else None,
        signal["direction"] if signal else None,
        signal["expiry"] if signal else None,
        extract_timing_language(raw_message),
        1 if signal else 0,
    ))
    conn.commit()
    conn.close()


client = TelegramClient(SESSION_NAME, api_id, api_hash)


@client.on(events.NewMessage(chats=SOURCE_USERNAME))
async def handler(event):
    received_at = datetime.now()
    sender = await event.get_sender()
    raw_text = event.raw_text or ""

    signal = parse_signal(raw_text)
    record_observation(
        source=SOURCE_USERNAME,
        sender_id=str(sender.id) if sender else None,
        message_id=event.id,
        raw_message=raw_text,
        message_date=event.date,
        signal=signal,
    )

    logger.info(
        "OBSERVED source=%s message_id=%s asset=%s direction=%s expiry=%s "
        "timing=%r parsed=%s",
        SOURCE_USERNAME, event.id,
        signal["asset"] if signal else None,
        signal["direction"] if signal else None,
        signal["expiry"] if signal else None,
        extract_timing_language(raw_text),
        bool(signal),
    )
    print(
        f"[{received_at.strftime('%H:%M:%S')}] Observed message {event.id} "
        f"from {SOURCE_USERNAME} (parsed={bool(signal)})"
    )


async def _verify_source_access():
    try:
        entity = await client.get_entity(SOURCE_USERNAME)
        logger.info("source_observer: resolved source %r (id=%s)", SOURCE_USERNAME, entity.id)
        return True
    except (RPCError, ValueError) as e:
        print(
            f"\nCannot access '{SOURCE_USERNAME}': {e}\n"
            "This usually means your account hasn't started a conversation "
            "with this bot/channel yet, or it doesn't exist. Open it in "
            "Telegram and press Start, then try again.\n"
        )
        logger.error("source_observer: could not resolve source %r: %s", SOURCE_USERNAME, e)
        return False


def main():
    initialize_observations_table()
    print(f"AXIM Source Observer - watching {SOURCE_USERNAME!r} (observation only, no trades will ever be placed)")
    client.start(phone=phone)

    if not client.loop.run_until_complete(_verify_source_access()):
        client.disconnect()
        return

    print("Connected. Listening for messages... (Ctrl+C to stop)")
    client.run_until_disconnected()


if __name__ == "__main__":
    main()
