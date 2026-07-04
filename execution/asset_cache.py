import logging
import sys
from pathlib import Path

from playwright.async_api import expect

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"

sys.path.insert(0, str(EXECUTION_DIR))
import pocket_dom

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

CATEGORY_TABS = [
    ("Currencies", "assets-block__nav-item--currency"),
    ("Cryptocurrencies", "assets-block__nav-item--cryptocurrency"),
    ("Commodities", "assets-block__nav-item--commodity"),
    ("Stocks", "assets-block__nav-item--stock"),
    ("Indices", "assets-block__nav-item--index"),
]

_SCAN_ROWS_JS = """
() => {
    const items = Array.from(document.querySelectorAll('.alist__item'));
    return items.map(item => {
        const labelEl = item.querySelector('.alist__label');
        const scheduleInfo = item.querySelector('.alist__schedule-info');
        return {
            name: labelEl ? labelEl.innerText.trim() : null,
            tradeable: !scheduleInfo,
        };
    }).filter(r => r.name);
}
"""

# Populated once by build_cache(), read by is_known_tradeable()/lookup().
# This is a fast-path optimization only - it can go stale within a session
# (a market can close mid-session), so it is never treated as the sole
# source of truth: select_asset() still performs its own live DOM check
# before actually clicking. The cache's job is to reject an obviously bad
# or already-known-untradeable asset without touching the browser at all.
_cache = {}


async def scan_all_assets(page, timeout=10000):
    """Opens the asset picker, scans every category tab, and returns a dict
    of display_name -> {"tradeable": bool, "category": str}."""
    results = {}

    await page.locator(pocket_dom.SEL_ASSET_TRIGGER).first.click(timeout=timeout)
    panel = page.locator(pocket_dom.SEL_ASSETS_BODY)
    await expect(panel).to_be_visible(timeout=timeout)

    for label, nav_class in CATEGORY_TABS:
        try:
            await page.locator(f".{nav_class}").first.click(timeout=timeout)
        except Exception as e:
            logger.warning("asset_cache: could not open category %r: %s", label, e)
            continue

        try:
            rows = await page.evaluate(_SCAN_ROWS_JS)
        except Exception as e:
            logger.warning("asset_cache: could not scan category %r: %s", label, e)
            continue

        for row in rows:
            results[row["name"]] = {"tradeable": row["tradeable"], "category": label}

    await pocket_dom._close_active_dropdown_modal(page, timeout=timeout)

    logger.info(
        "asset_cache: scanned %d assets across %d categories",
        len(results), len(CATEGORY_TABS),
    )
    return results


async def build_cache(page):
    global _cache
    _cache = await scan_all_assets(page)
    return _cache


def get_cache():
    return _cache


def lookup(asset_name):
    return _cache.get(asset_name)


def is_known_tradeable(asset_name):
    """Returns True/False if the asset is in the cache, or None if it's
    unknown (not scanned, or scanned before this asset existed) - callers
    should treat None as "fall back to the live DOM check", not as a
    rejection."""
    entry = _cache.get(asset_name)
    if entry is None:
        return None
    return entry["tradeable"]
