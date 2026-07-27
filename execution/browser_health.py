import asyncio
import os
import sys
import time
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
CORE_DIR = EXECUTION_DIR.parent / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))

import pocket_dom
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

# Session-authenticated and live-data-flow checks are real DOM/network round
# trips (unlike the free, local page.is_closed() call) - re-verified on this
# cadence per worker rather than on every single trade, matching
# browser_worker_pool.py's own HEALTH_CHECK_TTL_SECONDS tradeoff (P0 latency
# sprint - see that module's docstring) for the same reason: these are
# slow-changing conditions (a session doesn't flip from authenticated to
# logged-out except during a real incident, which the cheap page-responsive
# check below would very likely also catch on its own), so paying a full
# round trip for them on every single trade would reintroduce the exact
# latency regression that sprint fixed. A fresh page (just respawned) always
# gets the full deep check regardless of this TTL - see ensure_worker_healthy.
DEEP_HEALTH_CHECK_TTL_SECONDS = float(os.getenv("DEEP_HEALTH_CHECK_TTL_SECONDS", 30))

# Short, health-check-scale timeouts - never the DEFAULT_TIMEOUT_MS (15s)
# pocket_dom's own trade-interaction helpers use, since a health check that
# takes that long to fail is itself a latency problem on the trade path.
_RESPONSIVE_TIMEOUT_SECONDS = 3
_DOM_READY_TIMEOUT_MS = 2000
_BALANCE_READ_TIMEOUT_MS = 3000

# Same P0-latency-sprint tradeoff as browser_worker_pool.py's own
# HEALTH_CHECK_TTL_SECONDS (previously the only TTL that existed, now
# relocated here alongside the checks it gates): page.is_closed() is always
# checked (free, local, no IPC) but the live page.evaluate() responsiveness
# probe is skipped - trusted immediately - if this worker already passed it
# within this window, to avoid paying a redundant IPC round trip on the hot
# acquire_worker() path most of the time.
HEALTH_CHECK_TTL_SECONDS = float(os.getenv("HEALTH_CHECK_TTL_SECONDS", 2))


class RetryableBrowserError(Exception):
    """Raised for a transient, pre-trade-commitment browser/session problem
    that a single retry with a fresh worker is expected to clear - shared by
    BrowserHealthManager (this module, at worker-acquire time) and
    pocket_executor.prepare_trade (mid-DOM-interaction, before
    click_direction). trade_coordinator.handle_signal is the only intended
    catcher, and only ever retries once."""


class HealthCheckResult:
    def __init__(self, healthy, failed_check=None, detail=None):
        self.healthy = healthy
        self.failed_check = failed_check
        self.detail = detail

    def __repr__(self):
        if self.healthy:
            return "HealthCheckResult(healthy=True)"
        return f"HealthCheckResult(healthy=False, failed_check={self.failed_check!r}, detail={self.detail!r})"


class BrowserHealthManager:
    """
    Layered pre-trade health check for one BrowserWorker's page, run from
    BrowserWorkerPool.acquire_worker() before a worker is ever handed to a
    trade. Cheapest/fastest checks first, so the overwhelmingly common case
    (a healthy worker) pays for as little as possible:

      1. context alive        - NOT this class's job: BrowserWorkerPool's
                                 own _ensure_pool_healthy() already asks
                                 warmup_service.ensure_alive() before a
                                 worker is even pulled from the queue, and
                                 rebuilds every worker if the whole browser
                                 was replaced. Documented here rather than
                                 re-implemented, so this module's checks are
                                 read as "given a live context, is THIS
                                 worker's page actually fit to trade" - one
                                 concern, not a duplicate of the other.
      2. page responsive      - page.is_closed() (free, local) + a bounded
                                 page.evaluate("() => 1")
      3. DOM ready             - the same asset-trigger element
                                 get_trading_page() already waits for is
                                 still visible (catches a page stuck on an
                                 interstitial/overlay/wrong URL)
      4. session authenticated - the demo/live mode class BrowserWarmupService
                                 verified at startup is still present on
                                 <body> (catches a session that got logged
                                 out from under a long-lived worker page)
      5. live data flowing     - reading a real, parseable balance figure -
                                 the practical, implementable proxy for "the
                                 account's live feed is healthy": this
                                 codebase has no supported way to inspect
                                 Pocket Option's own WebSocket object from
                                 outside its page JS, so this checks the
                                 OBSERVABLE EFFECT of that feed (balance only
                                 ever renders a real figure while it's live)
                                 rather than claiming to verify the socket
                                 itself.

    Recovery on any failure is BrowserWorkerPool.ensure_worker_healthy's job,
    not this class's - this class only reports what it found. That keeps
    "what's healthy" (this file) separate from "what to do about it"
    (the pool's own respawn-then-escalate sequence), so each stays testable
    on its own.
    """

    def __init__(self):
        self._last_deep_check = {}  # worker_id -> monotonic time of last passed deep check

    async def _check_page_responsive(self, worker, force=False):
        page = worker.page
        if page.is_closed():
            return False, "page closed"
        if not force and (time.monotonic() - worker.last_health_check) < HEALTH_CHECK_TTL_SECONDS:
            return True, None
        try:
            await asyncio.wait_for(page.evaluate("() => 1"), timeout=_RESPONSIVE_TIMEOUT_SECONDS)
        except Exception as e:
            return False, f"page unresponsive: {e}"
        worker.last_health_check = time.monotonic()
        return True, None

    async def _check_dom_ready(self, page):
        try:
            visible = await page.locator(pocket_dom.SEL_ASSET_TRIGGER).first.is_visible(timeout=_DOM_READY_TIMEOUT_MS)
        except Exception as e:
            return False, f"dom not ready: {e}"
        if not visible:
            return False, "asset trigger not visible"
        return True, None

    async def _check_session_authenticated(self, page, expected_mode_class):
        try:
            present = await asyncio.wait_for(
                page.evaluate("(cls) => document.body.classList.contains(cls)", expected_mode_class),
                timeout=_RESPONSIVE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            return False, f"could not verify session: {e}"
        if not present:
            return False, f"{expected_mode_class!r} class missing - session may be logged out or site changed"
        return True, None

    async def _check_live_data_flowing(self, page):
        try:
            balance = await pocket_dom.read_balance(page, timeout=_BALANCE_READ_TIMEOUT_MS)
        except Exception as e:
            return False, f"could not read live balance: {e}"
        if balance is None:
            return False, "balance unreadable - live data feed may be down"
        return True, None

    def _due_for_deep_check(self, worker_id):
        return (time.monotonic() - self._last_deep_check.get(worker_id, 0.0)) >= DEEP_HEALTH_CHECK_TTL_SECONDS

    def note_page_replaced(self, worker_id):
        """Forces the next check_worker() call for this worker_id to run
        the full deep check regardless of TTL - called by the pool right
        after a respawn, since a fresh page has no track record yet and
        must prove itself in full before being handed to a trade."""
        self._last_deep_check.pop(worker_id, None)

    async def check_worker(self, worker, expected_mode_class, force_deep=False):
        page = worker.page
        ok, detail = await self._check_page_responsive(worker, force=force_deep)
        if not ok:
            return HealthCheckResult(False, "page_responsive", detail)

        if not force_deep and not self._due_for_deep_check(worker.worker_id):
            return HealthCheckResult(True)

        ok, detail = await self._check_dom_ready(page)
        if not ok:
            return HealthCheckResult(False, "dom_ready", detail)

        ok, detail = await self._check_session_authenticated(page, expected_mode_class)
        if not ok:
            return HealthCheckResult(False, "session_authenticated", detail)

        ok, detail = await self._check_live_data_flowing(page)
        if not ok:
            return HealthCheckResult(False, "live_data_flowing", detail)

        self._last_deep_check[worker.worker_id] = time.monotonic()
        return HealthCheckResult(True)
