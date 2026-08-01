from dotenv import load_dotenv
import os

load_dotenv()

WATCH_CHANNELS = [
    channel.strip()
    for channel in os.getenv("WATCH_CHANNELS", "").split(",")
    if channel.strip()
]

# Pocket Option
POCKET_URL = os.getenv("POCKET_URL", "https://pocketoption.com")
PO_EMAIL = os.getenv("PO_EMAIL")
PO_PASSWORD = os.getenv("PO_PASSWORD")

# Demo-cabinet DOM verification signal - was hardcoded as the literal
# string "is-chart-demo" in execution/browser_warmup.py; now configurable
# (default unchanged) so it lives alongside its live-mode counterpart
# below rather than split across two places.
DEMO_MODE_VERIFICATION_CLASS = os.getenv("DEMO_MODE_VERIFICATION_CLASS", "is-chart-demo")

# Live-cabinet URL + its own DOM verification signal (the live-mode
# equivalent of DEMO_URL/DEMO_MODE_VERIFICATION_CLASS above). Deliberately
# unset by default and NEVER guessed at - unlike DEMO_URL, which was found
# and verified against the real site early in this project, nobody had
# inspected a real live Pocket Option cabinet page in this codebase's
# history until 2026-07-31. A broker account configured mode="live"/"both"
# with live_enabled=true will refuse to start (LiveModeNotConfiguredError)
# until all three of these are set to values an operator has personally
# verified against their own live account.
#
# 2026-07-31 finding, from actually inspecting a real live cabinet: unlike
# demo, Pocket Option puts NO demo/real/live-specific class on <body> for
# a live account - so, unlike DEMO_MODE_VERIFICATION_CLASS, this is NOT a
# body class and execution/account_mode_verification.py's verify_live_mode
# must never treat it as one. The real, positive signal is a nested,
# human-readable status element:
#   <div class="type-of-trade-label type-of-trade-label--real">
#     You are trading on Real account
#   </div>
# LIVE_MODE_VERIFICATION_SELECTOR must resolve to exactly ONE element on
# the page, and its whitespace-normalized text must exactly equal
# LIVE_MODE_VERIFICATION_TEXT - both are required together, and verify_
# live_mode fails closed if either is unset, ambiguous, or mismatched.
LIVE_URL = os.getenv("LIVE_URL")
LIVE_MODE_VERIFICATION_SELECTOR = os.getenv("LIVE_MODE_VERIFICATION_SELECTOR")
LIVE_MODE_VERIFICATION_TEXT = os.getenv("LIVE_MODE_VERIFICATION_TEXT")

# Trading
ACCOUNT = os.getenv("ACCOUNT", "DEMO")
AUTO_EXECUTE = os.getenv("AUTO_EXECUTE", "false").lower() == "true"
PREVIEW_ONLY = os.getenv("PREVIEW_ONLY", "true").lower() == "true"
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", os.getenv("DEFAULT_TRADE_AMOUNT", 10)))
MINIMUM_PAYOUT = int(os.getenv("MINIMUM_PAYOUT", 90))
MAX_SIGNAL_AGE = int(os.getenv("MAX_SIGNAL_AGE", 10))

# Martin Trader scheduled-entry execution (core/trade_series_engine.py,
# docs/opt_signals_gap_queue.md item 2) - the ONLY channel gated into
# treating its own signal as a multi-entry series instead of one
# immediate trade. ui_channels.id 163 is the real, confirmed "Martin
# Trader" channel (see docs/opt_signals_audit_ledger.json) - kept as a
# single overridable setting, never a hardcoded literal scattered through
# telegram_listener.py/trade_series_engine.py, so a real channel_id change
# (a re-sync, a different environment) is a one-line config edit.
MARTIN_TRADER_CHANNEL_ID = int(os.getenv("MARTIN_TRADER_CHANNEL_ID", 163))

# This series' own flat, non-scaling stake for every entry - deliberately
# NOT the shared TRADE_AMOUNT above (confirmed live: that global default is
# independently configured to $1 in this environment for reasons unrelated
# to Martin Trader specifically) - see TradeCoordinator.handle_signal's own
# docstring for why relying on the global fallback would silently use the
# wrong figure. Passed explicitly as fixed_stake on every entry, bypassing
# the Risk Engine's own sizing entirely.
MARTIN_TRADER_STAKE = float(os.getenv("MARTIN_TRADER_STAKE", 10))

# Precision-latency audit (2026-07-27): how long before the scheduled
# five-minute boundary AXIM reserves a browser worker and pre-stages
# asset/expiry/amount, so only the final direction click remains at the
# boundary itself. Deliberately NOT from signal receipt (which can be
# several minutes early) - reserving a worker that long would leave only
# 1 of MAX_CONCURRENT_WORKERS free for every other provider for that
# whole span. Kept configurable (15-30s is the sane range; latency
# telemetry - see core/execution_latency.py - should inform any future
# adjustment) rather than hand-tuned once and forgotten.
MARTIN_TRADER_PRESTAGE_SECONDS = float(os.getenv("MARTIN_TRADER_PRESTAGE_SECONDS", 20))

# Database
DATABASE_FILE = "data/axim.db"

# Logging
SAVE_SCREENSHOTS = os.getenv("SAVE_SCREENSHOTS", "true").lower() == "true"

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
# FastAPI's default /docs, /redoc, /openapi.json are unauthenticated -
# anyone who can reach the API at all (any device on the Tailscale
# network once API_BIND_HOST is opened up) could otherwise browse the
# full route/schema map, including every admin-only endpoint's exact
# shape, without logging in. Off by default, matching "no public
# exposure by default" - a developer debugging locally can opt in.
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"

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