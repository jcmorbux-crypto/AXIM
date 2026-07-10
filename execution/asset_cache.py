import re
import sys
from pathlib import Path

from playwright.async_api import expect

EXECUTION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTION_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"

sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CORE_DIR))
import pocket_dom
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

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


def _normalize_for_fuzzy_match(name):
    """Collapse whitespace and drop an "OTC" suffix, for comparing asset
    names that differ only in spacing or whether OTC was included - not for
    anything stricter, since real asset names differ in ways that matter
    (GameStop vs Gamestop is a real mismatch, not noise)."""
    collapsed = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"\s*OTC$", "", collapsed, flags=re.IGNORECASE).lower()


class AssetCache:
    """One instance per broker account (docs/AXIM_APP_PLAN.md's multi-
    broker-account architecture) - each account gets its own persistent
    browser context and can have its own tradeable-asset set, so this can
    no longer be a single module-level global shared by every account
    (previously scanned/read by every account into the SAME dict, a real
    cross-account correctness bug once accounts run concurrently, even
    though it's a fast-path-only optimization and never the sole source
    of truth: select_asset() still performs its own live DOM check before
    actually clicking, regardless of what this cache says)."""

    def __init__(self):
        self._cache = {}

    async def build_cache(self, page):
        self._cache = await scan_all_assets(page)
        return self._cache

    def get_cache(self):
        return self._cache

    def lookup(self, asset_name):
        return self._cache.get(asset_name)

    def is_known_tradeable(self, asset_name):
        """Returns True/False if the asset is in the cache, or None if
        it's unknown (not scanned, or scanned before this asset existed)
        - callers should treat None as "fall back to the live DOM check",
        not as a rejection."""
        entry = self._cache.get(asset_name)
        if entry is None:
            return None
        return entry["tradeable"]

    def resolve_exact_name(self, asset_name):
        """Looks up the cache's real display names against a parsed asset
        name, tolerating minor formatting differences a source or the
        parser might introduce - casing, extra/collapsed whitespace, or
        an inconsistently present "OTC" suffix. Returns the exact cached
        name to use for select_asset() (which does an exact-text DOM
        match) whenever exactly one such match exists, else the input
        unchanged. Catches a parsed asset that's right in substance but
        not in exact formatting before it wastes a doomed browser search
        - returns the input as-is if the cache is empty or has no
        confident match, since an unrecognized name may still be correct
        (cache can go stale) and the live DOM check remains the final
        authority."""
        if not asset_name or asset_name in self._cache:
            return asset_name

        target = asset_name.lower()
        for name in self._cache:
            if name.lower() == target:
                return name

        fuzzy_target = _normalize_for_fuzzy_match(asset_name)
        fuzzy_matches = [
            name for name in self._cache if _normalize_for_fuzzy_match(name) == fuzzy_target
        ]
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0]

        return asset_name
