import asyncio
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import expect
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
LOG_DIR = PROJECT_ROOT / "logs"
FAILURE_DIR = LOG_DIR / "failures"

sys.path.insert(0, str(CORE_DIR))
from logger import get_logger
from timeline import timed, time_category, get_current_timeline

# Raised from 10s during the production stress test: 8 truly-simultaneous
# signals showed a real ~37% "search field not visible"/DOM-contention
# failure rate under that load (docs/AXIM_PRODUCTION_READINESS_REPORT.md
# section 4.1) - real Telegram traffic is naturally spaced out, so this
# mainly buys margin for genuine bursts rather than changing steady-state
# behavior, which was already comfortably under 10s.
DEFAULT_TIMEOUT_MS = 15_000
RETRY_ATTEMPTS = 2

# How many of the most recent Closed-list items wait_for_trade_result scans
# per read. Must comfortably exceed how many trades can realistically close
# within one settlement-retry window (~30s) - with the worker-pool
# redesign removing the old cap on concurrent OPEN trades, more than 10
# trades can now close in that window, which pushed an older trade's own
# closed item past a too-small scan window before it was ever matched
# (confirmed live: a real EUR/RUB OTC trade's result_read_failed while 3+
# other trades closed around the same time).
CLOSED_ITEMS_SCAN_COUNT = 40

SEL_PROMO_CLOSE = ".mfp-close"

SEL_ASSET_TRIGGER = ".pair-number-wrap"
SEL_ASSET_SEARCH_INPUT = ".search__field"
SEL_CURRENT_SYMBOL = ".current-symbol"
SEL_ASSETS_BODY = ".assets-block__col-body"

SEL_EXPIRY_TRIGGER = ".block--expiration-inputs"
SEL_EXPIRY_PANEL = ".expiration-inputs-list-modal"
SEL_EXPIRY_INPUTS = f"{SEL_EXPIRY_PANEL} input"

SEL_AMOUNT_INPUT = ".block--bet-amount .value__val input"

SEL_BUY_BUTTON = ".btn.btn-call"
SEL_SELL_BUTTON = ".btn.btn-put"

SEL_DEALS_LIST = ".deals-list"
SEL_NO_DEALS = ".no-deals"
SEL_TRADES_PANEL = ".widget-slot.deals"
SEL_CLOSED_LIST_ITEM = f"{SEL_DEALS_LIST} .deals-list__item"

SEL_PAYOUT_BLOCK = ".block--payout"
SEL_PAYOUT_PERCENT = f"{SEL_PAYOUT_BLOCK} .value__val-start"

SEL_ACTIVE_DROPDOWN_MODAL = "#modal-root .drop-down-modal-wrap.active"

# Fixed point inside the chart backdrop, deliberately far left of the
# BUY/SELL/amount control column (which sits in the right-hand panel
# regardless of window size, per direct measurement at both 1600x1000 and
# maximized/screen-fit windows). Re-verify if the site's layout changes.
NEUTRAL_CLICK_POINT = (800, 500)

_RETRYABLE_ERRORS = (PlaywrightTimeoutError, AssertionError)

logger = get_logger("axim.pocket_dom", filename="pocket_dom.log")


def _log_selector_event(action, selector, timeout_ms, retry, found, visible, enabled):
    logger.info(
        "SELECTOR_CHECK action=%s selector=%r timeout_ms=%d retry=%d/%d found=%s visible=%s enabled=%s",
        action, selector, timeout_ms, retry, RETRY_ATTEMPTS, found, visible, enabled,
    )


async def _probe_state(locator):
    try:
        found = await locator.count() > 0
    except Exception:
        found = False

    visible = False
    enabled = False
    if found:
        try:
            visible = await locator.is_visible()
        except Exception:
            visible = False
        try:
            enabled = await locator.is_enabled()
        except Exception:
            enabled = False

    return found, visible, enabled


class PocketDomError(Exception):
    def __init__(self, action, selector, reason, screenshot_path=None, html_path=None, url=None):
        self.action = action
        self.selector = selector
        self.reason = reason
        self.screenshot_path = screenshot_path
        self.html_path = html_path
        self.url = url
        super().__init__(f"{action} failed on selector '{selector}': {reason}")


class AssetUntradeableError(Exception):
    """Raised when the requested asset's row is found but marked
    unavailable (e.g. live forex market closed) - a legitimate business
    condition, not a DOM verification failure, so it is not retried and
    does not trigger a failure-diagnostics capture."""
    def __init__(self, asset_name, reason="market closed / schedule unavailable"):
        self.asset_name = asset_name
        self.reason = reason
        super().__init__(f"{asset_name} is currently untradeable: {reason}")


async def _capture_failure(page, action, selector, reason):
    FAILURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}_{action}"
    out_dir = FAILURE_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = out_dir / "screenshot.png"
    html_path = out_dir / "page.html"
    url_path = out_dir / "url.txt"

    try:
        await page.screenshot(path=str(screenshot_path))
    except Exception as e:
        screenshot_path = None
        logger.error("screenshot capture failed for action=%s: %s", action, e)

    try:
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
    except Exception as e:
        html_path = None
        logger.error("html capture failed for action=%s: %s", action, e)

    try:
        url = page.url
        url_path.write_text(url, encoding="utf-8")
    except Exception as e:
        url = None
        logger.error("url capture failed for action=%s: %s", action, e)

    logger.error(
        "VERIFICATION FAILED action=%s selector=%s reason=%s screenshot=%s html=%s url=%s",
        action, selector, reason, screenshot_path, html_path, url,
    )

    raise PocketDomError(action, selector, reason, screenshot_path, html_path, url)


def _expiry_to_hms(expiry_str):
    match = re.match(r"(\d+)\s*(Second|Minute)", expiry_str or "", re.IGNORECASE)
    if not match:
        raise ValueError(f"Unrecognized expiry format: {expiry_str!r}")

    value = int(match.group(1))
    unit = match.group(2).lower()
    total_seconds = value if unit.startswith("second") else value * 60

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return hours, minutes, seconds


def expiry_to_seconds(expiry_str):
    hours, minutes, seconds = _expiry_to_hms(expiry_str)
    return hours * 3600 + minutes * 60 + seconds


def _format_amount(amount):
    amount = float(amount)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}"


def _asset_search_term(asset_name):
    # Confirmed by direct testing: the search field does not reliably match
    # "EUR/USD OTC" or "EURUSD.OTC" - the compact symbol with no separators
    # and no "OTC" (e.g. "EURUSD") is what surfaces both the OTC and
    # non-OTC rows. Disambiguation between those rows happens afterward,
    # explicitly, based on whether the signal asset requested OTC.
    compact = re.sub(r"[/.\s]", "", asset_name)
    compact = re.sub(r"OTC$", "", compact, flags=re.IGNORECASE)
    return compact.upper()


def _wants_otc(asset_name):
    return asset_name.upper().endswith(" OTC")


@timed("browser")
async def dismiss_blocking_modals(page, timeout=3000):
    close_btn = page.locator(SEL_PROMO_CLOSE).first
    try:
        await close_btn.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        return

    await close_btn.click(timeout=timeout)
    await expect(close_btn).to_be_hidden(timeout=timeout)


async def _close_active_dropdown_modal(page, timeout=DEFAULT_TIMEOUT_MS):
    modal = page.locator(SEL_ACTIVE_DROPDOWN_MODAL).first
    if await modal.count() == 0 or not await modal.is_visible():
        return
    x, y = NEUTRAL_CLICK_POINT
    await page.mouse.click(x, y)
    await expect(modal).to_be_hidden(timeout=timeout)


async def _ensure_opened_tab_active(page, timeout=DEFAULT_TIMEOUT_MS):
    """The Opened/Closed sub-tabs in the Trades panel persist whichever was
    last active across page loads (confirmed: a prior session leaving
    Closed active meant .no-deals - which only has meaning on the Opened
    tab - never appeared, breaking both click_direction's confirmation and
    wait_for_trade_result's initial wait). Always confirm Opened is active
    before relying on either."""
    opened_tab = page.locator(SEL_TRADES_PANEL).get_by_text("Opened", exact=True).first
    try:
        is_active = await opened_tab.evaluate(
            "(el) => { const li = el.closest('li'); return li ? li.classList.contains('active') : false; }"
        )
    except Exception:
        is_active = False

    if not is_active:
        await opened_tab.click(timeout=timeout)
        await expect(opened_tab.locator("xpath=..")).to_have_class(re.compile(r"\bactive\b"), timeout=timeout)


async def _read_current_asset(page):
    try:
        return (await page.locator(SEL_CURRENT_SYMBOL).first.inner_text(timeout=2000)).strip()
    except Exception:
        return None


async def _read_current_expiry_display(page):
    try:
        return (await page.locator(f"{SEL_EXPIRY_TRIGGER} .value__val").first.inner_text(timeout=2000)).strip()
    except Exception:
        return None


async def _read_current_amount(page):
    try:
        return (await page.locator(SEL_AMOUNT_INPUT).first.input_value(timeout=2000)).strip()
    except Exception:
        return None


@timed("browser")
async def select_asset(page, asset_name, timeout=DEFAULT_TIMEOUT_MS):
    if await _read_current_asset(page) == asset_name:
        logger.info("select_asset: %r already selected, no-op", asset_name)
        return

    search_term = _asset_search_term(asset_name)
    wants_otc = _wants_otc(asset_name)
    logger.info(
        "select_asset: target=%r search_term=%r wants_otc=%s",
        asset_name, search_term, wants_otc,
    )

    last_reason = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            panel = page.locator(SEL_ASSETS_BODY)
            if not await panel.is_visible():
                await page.locator(SEL_ASSET_TRIGGER).first.click(timeout=timeout)
                await expect(panel).to_be_visible(timeout=timeout)

            # Asset search input. No _probe_state()/_log_selector_event()
            # here (removed - measured via fine-grained timing at ~56ms per
            # call, pure diagnostic overhead: found/visible/enabled were
            # only ever fed into a log line, never a control-flow decision.
            # The actual correctness checks are the two expect() calls
            # below, which are unchanged and still run every time.
            search = page.locator(SEL_ASSET_SEARCH_INPUT).first
            await expect(search).to_be_visible(timeout=timeout)
            await expect(search).to_be_enabled(timeout=timeout)
            await search.fill(search_term, timeout=timeout)

            # Asset selection - explicit OTC / non-OTC disambiguation.
            # A compact search term can surface both variants of a pair
            # (e.g. "EUR/USD" and "EUR/USD OTC"). Exact-text matching against
            # the full asset_name - which itself does or doesn't carry the
            # "OTC" suffix - is what picks the correct row. Never assume the
            # first result is correct.
            matches = panel.get_by_text(asset_name, exact=True)
            row = matches.first
            await expect(row).to_be_visible(timeout=timeout)

            tradeable = await row.evaluate("""
                (el) => {
                    const item = el.closest('.alist__item');
                    if (!item) return true;
                    return !item.querySelector('.alist__schedule-info');
                }
            """)
            if not tradeable:
                logger.warning(
                    "select_asset: %r matched a row but it is currently untradeable "
                    "(market closed / schedule unavailable) - aborting without retry",
                    asset_name,
                )
                raise AssetUntradeableError(asset_name)

            logger.info(
                "select_asset: search_term=%r -> clicking target=%r (wants_otc=%s)",
                search_term, asset_name, wants_otc,
            )
            await row.click(timeout=timeout)

            symbol = page.locator(SEL_CURRENT_SYMBOL).first
            await expect(symbol).to_have_text(asset_name, timeout=timeout)

            await _close_active_dropdown_modal(page, timeout=timeout)

            logger.info("select_asset: %r confirmed on screen", asset_name)
            return
        except _RETRYABLE_ERRORS as e:
            last_reason = str(e)
            logger.warning(
                "select_asset attempt %d/%d failed for %r (search_term=%r): %s",
                attempt, RETRY_ATTEMPTS, asset_name, search_term, last_reason,
            )

    await _capture_failure(page, "select_asset", SEL_ASSET_SEARCH_INPUT, last_reason or "unknown")


@timed("browser")
async def select_expiry(page, expiry_str, timeout=DEFAULT_TIMEOUT_MS):
    hours, minutes, seconds = _expiry_to_hms(expiry_str)
    targets = [f"{hours:02d}", f"{minutes:02d}", f"{seconds:02d}"]
    target_display = f"{targets[0]}:{targets[1]}:{targets[2]}"

    if await _read_current_expiry_display(page) == target_display:
        logger.info("select_expiry: %r already set (%s), no-op", expiry_str, target_display)
        return

    last_reason = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            # No _probe_state()/_log_selector_event() here or in the field
            # loop below (removed - same reasoning as select_asset: these
            # found/visible/enabled reads were only ever fed into a log
            # line, never a control-flow decision, and this loop ran the
            # probe 3 times, once per hours/minutes/seconds field. The
            # actual correctness checks are trigger.click()'s own
            # actionability wait plus the expect() per field, both
            # unchanged and still run every time).
            panel = page.locator(SEL_EXPIRY_PANEL)
            if not await panel.is_visible():
                trigger = page.locator(SEL_EXPIRY_TRIGGER).first
                await trigger.click(timeout=timeout)

            inputs = page.locator(SEL_EXPIRY_INPUTS)
            await expect(inputs.nth(2)).to_be_visible(timeout=timeout)

            for idx, target in enumerate(targets):
                field = inputs.nth(idx)
                await field.fill(target, timeout=timeout)
                await expect(field).to_have_value(target, timeout=timeout)

            await _close_active_dropdown_modal(page, timeout=timeout)
            return
        except _RETRYABLE_ERRORS as e:
            last_reason = str(e)
            logger.warning(
                "select_expiry attempt %d/%d failed for %r: %s",
                attempt, RETRY_ATTEMPTS, expiry_str, last_reason,
            )

    await _capture_failure(page, "select_expiry", SEL_EXPIRY_TRIGGER, last_reason or "unknown")


@timed("browser")
async def set_amount(page, amount, timeout=DEFAULT_TIMEOUT_MS):
    target = _format_amount(amount)

    if await _read_current_amount(page) == target:
        logger.info("set_amount: %r already set, no-op", target)
        return

    last_reason = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            # No _probe_state()/_log_selector_event() here - same reasoning
            # as select_asset/select_expiry: purely diagnostic, the actual
            # correctness checks are the two expect() calls below.
            field = page.locator(SEL_AMOUNT_INPUT).first
            await expect(field).to_be_visible(timeout=timeout)
            await expect(field).to_be_enabled(timeout=timeout)
            await field.fill(target, timeout=timeout)
            await expect(field).to_have_value(target, timeout=timeout)
            return
        except _RETRYABLE_ERRORS as e:
            last_reason = str(e)
            logger.warning(
                "set_amount attempt %d/%d failed for %r: %s",
                attempt, RETRY_ATTEMPTS, amount, last_reason,
            )

    await _capture_failure(page, "set_amount", SEL_AMOUNT_INPUT, last_reason or "unknown")


_UNCOVERED_CHECK_JS = """([buySelector, sellSelector]) => {
    function isUncovered(selector) {
        const el = document.querySelector(selector);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        const cx = r.x + r.width / 2;
        const cy = r.y + r.height / 2;
        const top = document.elementFromPoint(cx, cy);
        return top !== null && (top === el || el.contains(top));
    }
    return isUncovered(buySelector) && isUncovered(sellSelector);
}"""


@timed("browser")
async def verify_direction_controls_ready(page, timeout=DEFAULT_TIMEOUT_MS):
    # No _probe_state()/_log_selector_event() here (removed - same
    # reasoning as select_asset/select_expiry/set_amount: 4 calls' worth of
    # found/visible/enabled reads that only ever fed a log line, never a
    # control-flow decision. The real correctness checks - the two expect()
    # pairs and the hit-testing wait_for_function below - are unchanged and
    # still run every time this is called, which is every single trade).
    last_reason = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            buy = page.locator(SEL_BUY_BUTTON).first
            sell = page.locator(SEL_SELL_BUTTON).first

            await expect(buy).to_be_visible(timeout=timeout)
            await expect(buy).to_be_enabled(timeout=timeout)

            await expect(sell).to_be_visible(timeout=timeout)
            await expect(sell).to_be_enabled(timeout=timeout)

            # Visible+enabled isn't sufficient - something can still be layered
            # on top and intercept the click. Test real click-reachability via
            # the same hit-testing Playwright's own actionability engine uses.
            await page.wait_for_function(
                _UNCOVERED_CHECK_JS,
                arg=[SEL_BUY_BUTTON, SEL_SELL_BUTTON],
                timeout=timeout,
            )
            return
        except _RETRYABLE_ERRORS as e:
            last_reason = str(e)
            logger.warning(
                "verify_direction_controls_ready attempt %d/%d failed: %s",
                attempt, RETRY_ATTEMPTS, last_reason,
            )

    await _capture_failure(
        page, "verify_direction_controls_ready",
        f"{SEL_BUY_BUTTON} / {SEL_SELL_BUTTON}", last_reason or "unknown",
    )


async def click_direction(page, direction, timeout=DEFAULT_TIMEOUT_MS):
    """
    Splits into two separately-timed, separately-marked phases (closing a
    documented instrumentation gap: previously "clicked" and "confirmation
    detected" were marked back-to-back with no separating work, making
    trade-confirmation latency measure as 0ms - not a real finding, just an
    instrumentation blind spot):
    1. The actual button click - marked "clicked" the instant it returns.
    2. Waiting for Pocket Option's own UI to confirm the trade opened
       (.no-deals hidden) - marked "confirmation_detected" once that
       resolves. This part is network/server-bound, not something client-
       side optimization can shrink - measuring it separately is what lets
       that be shown precisely instead of assumed.
    """
    direction = direction.upper()
    if direction == "BUY":
        button_selector = SEL_BUY_BUTTON
    elif direction == "SELL":
        button_selector = SEL_SELL_BUTTON
    else:
        raise ValueError(f"Unknown direction: {direction!r}")

    try:
        async with time_category("browser"):
            await _ensure_opened_tab_active(page, timeout=timeout)

            # No _probe_state()/_log_selector_event() here - same reasoning
            # as the other pocket_dom functions: purely diagnostic, the
            # real correctness check is the expect() call below.
            button = page.locator(button_selector).first
            await expect(button).to_be_enabled(timeout=timeout)
            await button.click(timeout=timeout)

        timeline = get_current_timeline()
        if timeline is not None:
            timeline.mark("clicked")

        async with time_category("browser"):
            no_deals = page.locator(SEL_DEALS_LIST).locator(SEL_NO_DEALS)
            await expect(no_deals).to_be_hidden(timeout=timeout)

        if timeline is not None:
            timeline.mark("confirmation_detected")
    except _RETRYABLE_ERRORS as e:
        await _capture_failure(page, "click_direction", button_selector, str(e))


@timed("browser")
async def read_payout_percent(page, timeout=DEFAULT_TIMEOUT_MS):
    """Reads the displayed payout percentage (e.g. 81 for '+81%'). Purely
    informational - returns None on failure rather than raising, since a
    missing payout reading should not block an otherwise-verified trade."""
    locator = page.locator(SEL_PAYOUT_PERCENT).first
    found, visible, enabled = await _probe_state(locator)
    _log_selector_event("payout_selector", SEL_PAYOUT_PERCENT, timeout, 1, found, visible, enabled)

    try:
        await expect(locator).to_be_visible(timeout=timeout)
        text = (await locator.inner_text()).strip()
        match = re.search(r"(-?\d+)", text)
        return int(match.group(1)) if match else None
    except _RETRYABLE_ERRORS as e:
        logger.warning("read_payout_percent: could not read payout (%s) - non-fatal", e)
        return None


SEL_ASSET_INACTIVE_OVERLAY = ".asset-inactive"

_PAYOUT_AND_TRADEABLE_JS = """
() => {
    const overlay = document.querySelector('.asset-inactive');
    let tradeable = true;
    if (overlay) {
        const style = getComputedStyle(overlay);
        const opacity = parseFloat(style.opacity || '1');
        // Confirmed by direct measurement: this element is ALWAYS present
        // in the DOM with non-zero width/height, display:block, and
        // visibility:visible even when completely inert (dormant
        // CSS-transition scaffolding) - checking those properties alone
        // (the original, wrong approach) always reports "blocking" and
        // never actually distinguishes anything. opacity and
        // pointer-events are what genuinely differ between a real,
        // active "instrument unavailable" state and the normal dormant
        // one.
        tradeable = !(opacity > 0.05 && style.pointerEvents !== 'none');
    }
    const payoutEl = document.querySelector('.block--payout .value__val-start');
    return {
        tradeable,
        payoutText: payoutEl ? payoutEl.innerText.trim() : null,
    };
}
"""


@timed("browser")
async def read_payout_and_check_tradeable(page):
    """
    Single combined DOM read for the two live facts needed right before a
    click: is the currently-selected asset still tradeable right now, and
    what is its current payout. Complements select_asset's own
    pre-selection tradeable check (via the search-results row's
    .alist__schedule-info) by re-confirming on the actual trading page
    immediately before the click - closing the narrow window between "was
    tradeable when selected" and "is still tradeable now", in one DOM
    round-trip instead of two separate reads.

    Returns (payout, tradeable) - payout is None if it couldn't be read
    (non-fatal on its own; risk_manager.check_minimum_payout fails closed
    on None). tradeable defaults to True if the DOM read itself fails,
    since a failed read here isn't evidence of an actual problem - the
    picker-level check already covers the primary case.
    """
    try:
        result = await page.evaluate(_PAYOUT_AND_TRADEABLE_JS)
    except Exception as e:
        logger.warning("read_payout_and_check_tradeable: DOM read failed (%s)", e)
        return None, True

    tradeable = result.get("tradeable", True)
    payout_text = result.get("payoutText")
    payout = None
    if payout_text:
        match = re.search(r"(-?\d+)", payout_text)
        if match:
            payout = int(match.group(1))

    logger.info(
        "read_payout_and_check_tradeable: payout=%s tradeable=%s",
        payout, tradeable,
    )
    if not tradeable:
        logger.warning(
            "read_payout_and_check_tradeable: asset-inactive overlay is genuinely "
            "active (opacity>0.05, pointer-events enabled) - asset became "
            "untradeable after selection"
        )

    return payout, tradeable


_CLOSED_ITEMS_JS = """
(maxItems) => {
    const items = Array.from(document.querySelectorAll('.deals-list .deals-list__item')).slice(0, maxItems);
    return items.map(el => {
        const rows = el.querySelectorAll('.item-row');
        // rows[0] contains TWO <a> tags: a .favorites star icon (empty
        // text) first, then the actual asset name - querySelector('a')
        // grabs the star icon and always returns ''. Confirmed this was a
        // pre-existing bug (present in the original single-item version
        // of this extraction too, silently masked because nothing
        // previously filtered by asset - it always just took .first).
        const assetLink = rows[0] ? Array.from(rows[0].querySelectorAll('a')).find(a => !a.closest('.favorites')) : null;
        const timeDiv = rows[0] ? rows[0].querySelectorAll('div')[1] : null;
        const valueDivs = rows[1] ? Array.from(rows[1].querySelectorAll('div')) : [];
        return {
            asset: assetLink ? assetLink.innerText.trim() : null,
            direction: rows[1]
                ? (rows[1].querySelector('.fa-arrow-up') ? 'BUY'
                    : (rows[1].querySelector('.fa-arrow-down') ? 'SELL' : null))
                : null,
            time_text: timeDiv ? timeDiv.innerText.trim() : null,
            values: valueDivs.map(d => d.innerText.trim()),
        };
    });
}
"""


def _closest_closed_item(items, asset, direction, expected_close_dt):
    """Among Closed-list items matching asset+direction, picks the one
    whose displayed close time (HH:MM, minute-resolution only - the site
    doesn't render seconds) is closest to when THIS trade was expected to
    close. Reduces but does not eliminate ambiguity between same-asset,
    same-direction trades that close within the same clock-minute under
    heavy concurrency - a documented residual limit, not a claim of
    perfect uniqueness."""
    candidates = [i for i in items if i.get("asset") == asset and i.get("direction") == direction]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _time_diff(item):
        try:
            hh, mm = item["time_text"].split(":")
            candidate_dt = expected_close_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            # HH:MM alone is ambiguous across a day boundary - try the
            # neighboring day in each direction and keep whichever is
            # actually closest to the expected close time.
            best = abs((candidate_dt - expected_close_dt).total_seconds())
            for delta_days in (-1, 1):
                alt = candidate_dt + timedelta(days=delta_days)
                best = min(best, abs((alt - expected_close_dt).total_seconds()))
            return best
        except (ValueError, AttributeError, TypeError):
            return float("inf")

    return min(candidates, key=_time_diff)


async def wait_for_trade_result(warmup_service, expiry_seconds, asset=None, direction=None,
                                 settlement_buffer_seconds=2):
    """
    Waits for the currently-open trade to close, then classifies win/loss/
    draw from the Closed tab.

    settlement_buffer_seconds default tuned from measurement, not a guess:
    a dedicated probe (6 real trades) polled every 250ms starting exactly
    at nominal expiry and found the Closed item actually appears just
    172-250ms later (avg 206ms) - the previous 8s default was roughly 32x
    more than needed. 2s keeps a real ~8x margin over the observed max
    while still cutting 6s of pure dead time off every single trade; the
    bounded retry loop below remains as a safety net for any outlier
    slower than that.

    Does NOT rely on .no-deals to detect closure. Confirmed via direct live
    testing (two concurrent trades, 15s and 60s expiry, watched every 2s):
    the 15s trade's own .no-deals stayed False for the FULL ~60 seconds,
    flipping True only once the 60s trade ALSO closed - .no-deals reflects
    "zero open positions across this whole browser context", not "this
    specific trade closed". Under concurrency this silently delayed outcome
    detection by however long the slowest concurrent trade took (measured
    up to 28s in the P0 sprint benchmark) - a correctness gap for
    downstream risk tracking (consecutive-losses, cooldown), not only a
    latency one.

    Fix: this trade's own close time is already known deterministically
    (expiry_seconds, fixed at signal time) - sleep for that duration plus a
    short settlement buffer, then read the Closed tab once and match the
    specific item by asset + direction + closest closing-time to when this
    trade was expected to close (see _closest_closed_item). Falls back to
    a few bounded retries (short extra sleeps, re-reading the list each
    time) if the item isn't rendered yet, up to a generous ceiling - but no
    longer polls indefinitely on a signal that can't distinguish this
    trade's closure from an unrelated one.

    Takes the warmup service's own dedicated bootstrap page (via
    `warmup_service.get_page()`), NOT a BrowserWorkerPool worker - that
    page is otherwise idle after startup, so reading outcomes here means
    outcome-watching NEVER competes with trade placement for a worker slot,
    at any concurrency level. (An earlier version borrowed a placement
    worker for this brief read; moving it to this always-idle dedicated
    page instead removes that contention entirely rather than just
    shortening it.) The sleep itself needs no browser resource of any kind
    regardless. Safe to reuse one page across every trade's outcome check
    because the Opened/Closed active-tab toggle is confirmed shared live
    across every page in the same browser context (verified: clicking
    Closed on one page instantly flips another, untouched page's rendered
    active tab too) - this dedicated page can read the same Closed list
    that reflects trades placed on any worker. `warmup_service.outcome_lock`
    serializes the tab-switch+read step itself across trades finishing at
    nearly the same moment, since it is only ever this one page doing the
    reading now.

    Win/loss classification is confirmed against both a real win ($1 stake
    -> $1.92 returned, 92% payout) and a real loss ($1 stake -> $0
    returned) sample. Classification: total returned == 0 -> loss, total
    returned > stake -> win, otherwise -> draw.
    """
    # Intentional waiting, not active execution - a deliberate delay for a
    # deterministic, known duration (the trade's own contractual expiry),
    # timed separately so it never gets miscounted as browser/CPU work.
    # No browser resource is held during this wait at all.
    async with time_category("waiting"):
        await asyncio.sleep(expiry_seconds + settlement_buffer_seconds)

    max_wait = time.monotonic() + 30
    retry_sleep = 3
    match = None
    data = None

    page = await warmup_service.get_page()
    try:
        while True:
            async def _read_once():
                async with time_category("browser"):
                    await _ensure_opened_tab_active(page)
                    trades_panel = page.locator(SEL_TRADES_PANEL)
                    closed_tab = trades_panel.get_by_text("Closed", exact=True).first
                    found, visible, enabled = await _probe_state(closed_tab)
                    _log_selector_event("closed_tab_selector", "text=Closed", DEFAULT_TIMEOUT_MS, 1, found, visible, enabled)
                    await closed_tab.click(timeout=DEFAULT_TIMEOUT_MS)
                    item = page.locator(SEL_CLOSED_LIST_ITEM).first
                    await expect(item).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
                    return await page.evaluate(_CLOSED_ITEMS_JS, CLOSED_ITEMS_SCAN_COUNT)

            async with warmup_service.outcome_lock:
                items = await _read_once()

            if asset is not None and direction is not None:
                match = _closest_closed_item(items, asset, direction, datetime.now())
            else:
                match = items[0] if items else None

            if match is not None or time.monotonic() >= max_wait:
                data = match
                break
            async with time_category("waiting"):
                await asyncio.sleep(retry_sleep)
    except _RETRYABLE_ERRORS as e:
        await _capture_failure(page, "wait_for_trade_result", SEL_CLOSED_LIST_ITEM, str(e))
        return None

    if data is None:
        logger.error(
            "wait_for_trade_result: no matching closed item found for asset=%r direction=%r after settlement wait",
            asset, direction,
        )
        return None

    timeline = get_current_timeline()
    if timeline is not None:
        timeline.mark("trade_settled")

    logger.info("wait_for_trade_result: closed_item=%s", data)

    def _to_amount(text):
        try:
            return float(text.replace("$", "").replace(",", ""))
        except (ValueError, AttributeError, TypeError):
            return None

    values = data.get("values") or []
    stake = _to_amount(values[0]) if len(values) > 0 else None
    # values[1] is the TOTAL amount returned (win: stake+profit, e.g.
    # "$1.92" on a $1 stake; loss: "$0") - confirmed against both a real
    # win and a real loss sample. values[-1] is just the profit delta
    # alone on a win (e.g. "+$0.92") and must NOT be compared against the
    # stake for classification - doing so was a real bug found here: a
    # genuine $1.92-return win was misclassified as "draw" because its
    # $0.92 profit delta is less than the $1 stake.
    total_returned = _to_amount(values[1]) if len(values) > 1 else None

    if stake is None or total_returned is None:
        result = "unknown"
    elif total_returned == 0:
        result = "loss"
    elif total_returned > stake:
        result = "win"
    else:
        result = "draw"

    return {
        "result": result,
        "asset": data.get("asset"),
        "direction": data.get("direction"),
        "stake": stake,
        "final_value": total_returned,
        "raw_values": values,
    }
