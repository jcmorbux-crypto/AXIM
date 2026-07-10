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

# Live-cabinet URL + its own DOM verification signal (the live-mode
# equivalent of DEMO_URL/"is-chart-demo" in execution/browser_warmup.py).
# Deliberately unset by default and NEVER guessed at - unlike DEMO_URL,
# which was found and verified against the real site early in this
# project, nobody has inspected a real live Pocket Option cabinet page
# in this codebase's history. A broker account configured mode="live"/
# "both" with live_enabled=true will refuse to start
# (LiveModeNotConfiguredError) until both of these are set to values an
# operator has personally verified against their own live account - see
# docs/AXIM_APP_PLAN.md's live-trading section for exactly what to look
# for (the same technique DEMO_URL's is-chart-demo check used: open
# devtools, find a CSS class on <body> or similar that's present on the
# live cabinet and absent elsewhere).
LIVE_URL = os.getenv("LIVE_URL")
LIVE_MODE_VERIFICATION_CLASS = os.getenv("LIVE_MODE_VERIFICATION_CLASS")

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
# How long a Live-mode trade with require_confirmation waits for an
# operator's Confirm/Reject before failing closed (rejecting the trade)
# - see core/session_manager.wait_for_trade_confirmation. Kept short
# relative to a typical binary-options expiry window on purpose.
TRADE_CONFIRMATION_TIMEOUT_SECONDS = int(os.getenv("TRADE_CONFIRMATION_TIMEOUT_SECONDS", 45))

# Concurrency (demo-only multi-worker execution)
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", 2))
WORKER_ACQUIRE_TIMEOUT_SECONDS = float(os.getenv("WORKER_ACQUIRE_TIMEOUT_SECONDS", 5))

# Dashboard (core/dashboard_server.py) - read-only, local-only web UI over
# the same signals.db every other tool already reads
ENABLE_DASHBOARD = os.getenv("ENABLE_DASHBOARD", "true").lower() == "true"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", 8080))

# Main API (api/main.py) bind address and remote-client access
# (docs/AXIM_REMOTE_ACCESS.md). Defaults keep the server local-only, exactly
# today's behavior - a Remote Client (over Tailscale or any other private
# network) is opt-in only, never automatic. Setting API_BIND_HOST to a
# Tailscale interface IP (or 0.0.0.0) is what actually allows another
# device to reach this API; ALLOWED_ORIGINS must then list that device's
# own origin for its browser-based requests to be accepted (CORS is a
# browser-side check, not a security boundary against non-browser clients,
# but it's still required for the web UI to function remotely).
API_BIND_HOST = os.getenv("API_BIND_HOST", "127.0.0.1")
API_BIND_PORT = int(os.getenv("API_BIND_PORT", 8090))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

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

# Password reset email (core/email_sender.py) - treats an unset SMTP_HOST
# as "email not configured" and falls back to logging the reset link
# server-side (an Owner/Admin can relay it manually), the same
# gate-don't-fabricate pattern billing.py uses for Stripe. Setting these
# activates real email delivery with no other code changes.
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "no-reply@axim.local")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"