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
# Off by default (0) as of docs/AXIM_SESSION_ARCHITECTURE.md section 3 -
# AXIM keeps trading within an active session after a loss unless the
# user explicitly opts into a cooldown as a custom rule.
COOLDOWN_AFTER_LOSS_SECONDS = int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", 0))
DUPLICATE_SIGNAL_WINDOW_SECONDS = int(os.getenv("DUPLICATE_SIGNAL_WINDOW_SECONDS", 120))
# Drawdown circuit breaker - flagged as a genuine gap in
# docs/AXIM_LIVE_READINESS_REVIEW.md: MAX_CONSECUTIVE_LOSSES alone never
# trips on a steady bleed-out through an alternating win/loss pattern,
# which is exactly what a no-edge binary-options payout structure produces
# on average. Default of 100 is a conservative starting point (10x the
# TRADE_AMOUNT default), consistent with every other risk knob in this
# file defaulting to a real, active, adjustable threshold rather than
# "off" - fail-closed by default, like the rest of this codebase.
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", 100))

# Concurrency (demo-only multi-worker execution)
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", 2))
WORKER_ACQUIRE_TIMEOUT_SECONDS = float(os.getenv("WORKER_ACQUIRE_TIMEOUT_SECONDS", 5))

# Dashboard (core/dashboard_server.py) - read-only, local-only web UI over
# the same signals.db every other tool already reads
ENABLE_DASHBOARD = os.getenv("ENABLE_DASHBOARD", "true").lower() == "true"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", 8080))

# Billing scaffold (docs/AXIM_APP_PLAN.md Phase 6) - core/billing.py treats
# an unset STRIPE_SECRET_KEY as "billing not configured" and disables real
# Stripe calls entirely; Owner manual tier activation (api/admin.py) remains
# the only way to change a user's access_tier until real keys are set here.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO")
STRIPE_PRICE_ELITE = os.getenv("STRIPE_PRICE_ELITE")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8090")