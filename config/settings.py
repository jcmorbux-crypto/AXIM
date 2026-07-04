from dotenv import load_dotenv
import os

load_dotenv()

# Telegram
API_ID = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
PHONE = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

SESSION_NAME = os.getenv("SESSION_NAME", "axim_session")

WATCH_CHANNELS = [
    channel.strip()
    for channel in os.getenv("WATCH_CHANNELS", "").split(",")
    if channel.strip()
]

# Pocket Option
POCKET_URL = os.getenv("POCKET_URL", "https://pocketoption.com")
PO_EMAIL = os.getenv("PO_EMAIL")
PO_PASSWORD = os.getenv("PO_PASSWORD")

# Trading
ACCOUNT = os.getenv("ACCOUNT", "DEMO")
AUTO_EXECUTE = os.getenv("AUTO_EXECUTE", "false").lower() == "true"
PREVIEW_ONLY = os.getenv("PREVIEW_ONLY", "true").lower() == "true"
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", os.getenv("DEFAULT_TRADE_AMOUNT", 10)))
MINIMUM_PAYOUT = int(os.getenv("MINIMUM_PAYOUT", 90))
MAX_SIGNAL_AGE = int(os.getenv("MAX_SIGNAL_AGE", 10))
TRADE_DELAY = int(os.getenv("TRADE_DELAY", 0))

# Database
DATABASE_FILE = "data/axim.db"

# Logging
SAVE_SCREENSHOTS = os.getenv("SAVE_SCREENSHOTS", "true").lower() == "true"
SAVE_HTML = os.getenv("SAVE_HTML", "false").lower() == "true"

# Risk management
MAX_TRADE_AMOUNT = float(os.getenv("MAX_TRADE_AMOUNT", 50))
MAX_TRADES_PER_HOUR = int(os.getenv("MAX_TRADES_PER_HOUR", 10))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 3))
COOLDOWN_AFTER_LOSS_SECONDS = int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", 300))
DUPLICATE_SIGNAL_WINDOW_SECONDS = int(os.getenv("DUPLICATE_SIGNAL_WINDOW_SECONDS", 120))

# Concurrency (demo-only multi-worker execution)
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", 2))
WORKER_ACQUIRE_TIMEOUT_SECONDS = float(os.getenv("WORKER_ACQUIRE_TIMEOUT_SECONDS", 5))