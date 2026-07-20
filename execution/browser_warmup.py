import asyncio
import sys
from datetime import datetime
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

from browser_session import PocketBrowserSession, get_trading_page, DEMO_URL
import pocket_dom
from asset_cache import AssetCache
from logger import get_logger
import database
from settings import LIVE_URL, LIVE_MODE_VERIFICATION_CLASS

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


class DemoModeVerificationError(Exception):
    pass


class LiveModeNotConfiguredError(Exception):
    """Raised instead of ever guessing a live cabinet URL/verification
    signal - see config/settings.py's LIVE_URL/LIVE_MODE_VERIFICATION_CLASS
    docstring. A broker account configured for live capability
    (mode='live'/'both' + live_enabled=true) hits this until an operator
    has personally verified and set both values against a real live
    Pocket Option account."""
    pass


class BrowserWarmupService:
    """
    Long-lived, single persistent Pocket Option browser context. Launches
    once at AXIM startup and stays open for the life of the process, and
    relaunches itself if the whole browser process dies.

    Owns the shared browser context, demo-mode verification, and the
    asset cache scan (all context/account-level facts, not per-page).
    Trade execution itself happens through execution/browser_worker_pool.py's
    BrowserWorkerPool, which opens additional pages (tabs) from
    get_context() - each with its own lock, enabling real concurrency.
    This service's own `get_page()` (the bootstrap page) remains available
    for single-page use (e.g. the asset cache scan) but is not the primary
    trade-execution interface.

    `generation` increments every time start() completes (including via a
    reconnect) - BrowserWorkerPool compares this against the generation it
    last built its workers from to detect a whole-browser crash (its own
    pages would otherwise silently point into a browser process that no
    longer exists) and knows to rebuild itself, via ensure_alive().
    """

    def __init__(self, user_data_dir=None, mode="demo", broker_account_id=None):
        # user_data_dir=None keeps the original single-shared-profile
        # behavior (PocketBrowserSession's own default) for any caller
        # that doesn't care - core/broker_account_manager.py is what
        # actually passes each broker account's own distinct profile
        # directory (docs/AXIM_APP_PLAN.md's multi-broker-account
        # architecture), so different accounts' browser contexts and
        # login sessions can never bleed into each other.
        self._user_data_dir = user_data_dir
        # None for the legacy single-shared-connection path (no
        # broker_accounts row to attribute a reconnect failure to) -
        # broker_account_manager.py passes the real id for every
        # account-scoped context. Used by _reconnect() to surface a
        # needs-reauth condition on the right account (see its docstring).
        self._broker_account_id = broker_account_id
        # "demo" (default) or "live" - which cabinet URL this account's
        # persistent session loads and verifies against. Callers pass
        # the account's own effective mode (see
        # core/broker_account_manager.py's account_effective_cabinet_mode)
        # rather than this service deciding on its own.
        self._mode = mode
        self._session = None
        self._context = None
        self._page = None
        self.generation = 0
        self._reconnect_lock = asyncio.Lock()
        # Serializes track_outcome's tab-switch+read step on this ONE
        # dedicated page - see wait_for_trade_result's docstring for why
        # outcome-watching lives here instead of borrowing from
        # BrowserWorkerPool: this page is otherwise idle after startup, so
        # using it means outcome reads never compete with trade placement
        # for a worker slot, at any concurrency level.
        self.outcome_lock = asyncio.Lock()
        # One per account (see AssetCache's own docstring for why this
        # can no longer be a shared module-level global).
        self.asset_cache = AssetCache()

    async def start(self):
        if self._mode == "live":
            if not LIVE_URL or not LIVE_MODE_VERIFICATION_CLASS:
                logger.error(
                    "browser_warmup: mode=live requested but LIVE_URL/LIVE_MODE_VERIFICATION_CLASS "
                    "are not configured in .env - refusing to start rather than guess"
                )
                raise LiveModeNotConfiguredError(
                    "Live mode was requested for this account, but LIVE_URL and/or "
                    "LIVE_MODE_VERIFICATION_CLASS are not set in .env. These must be set to "
                    "values verified against a real live Pocket Option account before live "
                    "trading can start - see docs/AXIM_APP_PLAN.md."
                )
            target_url = LIVE_URL
        else:
            target_url = DEMO_URL

        self._session = (
            PocketBrowserSession(user_data_dir=self._user_data_dir)
            if self._user_data_dir is not None
            else PocketBrowserSession()
        )
        self._context = await self._session.__aenter__()
        # reuse_existing=True is correct here specifically because this
        # call always immediately follows a fresh context launch (see
        # get_trading_page's own docstring for why every other caller
        # must NOT do this) - reuses launch_persistent_context's
        # auto-opened blank tab for this service's own dedicated page
        # instead of leaving it idle and opening a redundant one.
        self._page = await get_trading_page(self._context, target_url, reuse_existing=True)
        await pocket_dom.dismiss_blocking_modals(self._page)
        await self._verify_account_mode()
        self.generation += 1
        logger.info("browser_warmup: session started and verified (mode=%s), generation=%s", self._mode, self.generation)

        await self.asset_cache.build_cache(self._page)

    async def _verify_account_mode(self):
        """Demo verification (is-chart-demo) is proven against the real
        site. Live verification uses whatever class an operator has
        personally confirmed on a real live cabinet page
        (LIVE_MODE_VERIFICATION_CLASS) - this service never assumes what
        that class is."""
        if self._mode == "live":
            verification_class = LIVE_MODE_VERIFICATION_CLASS
            mode_label = "live"
        else:
            verification_class = "is-chart-demo"
            mode_label = "demo"

        matches = await self._page.evaluate(
            "(cls) => document.body.classList.contains(cls)", verification_class
        )
        if not matches:
            logger.error("browser_warmup: page is NOT showing %s mode - refusing to proceed", mode_label)
            raise DemoModeVerificationError(
                f"Pocket Option page is not showing {mode_label} mode "
                f"({verification_class!r} class missing on <body>)"
            )
        logger.info("browser_warmup: %s mode verified (%r present)", mode_label, verification_class)

    async def health_check(self):
        try:
            if self._page is None or self._page.is_closed():
                return False
            await asyncio.wait_for(self._page.evaluate("() => 1"), timeout=3)
            return True
        except Exception as e:
            logger.warning("browser_warmup: health check failed: %s", e)
            return False

    async def get_page(self):
        if not await self.health_check():
            await self._reconnect()
        return self._page

    def get_context(self):
        return self._context

    async def ensure_alive(self):
        """Public health-check-and-recover entry point for components (e.g.
        BrowserWorkerPool) that hold their own state derived from this
        service's context but don't go through get_page() themselves.
        Returns the current generation so callers can detect whether a
        reconnect happened and their own derived state needs rebuilding."""
        if not await self.health_check():
            await self._reconnect()
        return self.generation

    async def _reconnect(self):
        async with self._reconnect_lock:
            # Re-check after acquiring the lock - a concurrent caller may
            # have already completed a reconnect while this one waited.
            if await self.health_check():
                return
            logger.warning("browser_warmup: reconnecting after crashed/closed browser")
            try:
                await self.stop()
                await self.start()
            except Exception as e:
                database.record_recovery_event("browser_reconnect", "failed", str(e))
                if isinstance(e, DemoModeVerificationError) and self._broker_account_id is not None:
                    self._flag_needs_reauth(str(e))
                raise
            else:
                database.record_recovery_event("browser_reconnect", "succeeded", f"generation={self.generation}")

    def _flag_needs_reauth(self, detail):
        """A RECONNECT's mode-verification failing (not the very first
        start(), which core/broker_account_manager.py's own
        _build_account_context already handles) usually means the
        persisted login session itself is no longer valid - retrying
        can't fix that, only an operator re-completing the login flow can
        (the existing "Connect" button on web/broker.html already handles
        re-auth for any non-"connected" status, so no new UI flow is
        needed). Worded honestly, not overclaimed: this is a CSS-class
        presence check, not a definitive login-state read, so it could
        also mean Pocket Option changed their page markup.

        Everything downstream already does the right thing once this is
        set: broker_account_manager._balance_refresh_loop already
        self-evicts any context whose connection_status isn't
        "connected" (within its own 30s cadence), and
        get_or_build_account_context already rejects new signals for a
        non-connected account with the status in the reason - so this
        stops the reconnect-forever loop and makes it actionable without
        any new plumbing beyond this flag."""
        message = (
            "Pocket Option verification failed after reconnecting - this usually means the "
            "session was logged out and needs to be reconnected, but could also indicate a "
            f"site change. Detail: {detail}"
        )
        database.update_broker_account(
            self._broker_account_id, connection_status="needs_reauth",
            last_error=message, last_error_at=datetime.now().isoformat(),
        )
        owner = database.get_owner_user()
        if owner is not None:
            database.create_notification(owner["id"], message, source="browser_warmup:reauth_required")
        logger.error("browser_warmup: account_id=%s flagged needs_reauth: %s", self._broker_account_id, detail)

    async def stop(self):
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as e:
                logger.error("browser_warmup: error closing session: %s", e)
        self._session = None
        self._context = None
        self._page = None
