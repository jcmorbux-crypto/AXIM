"""Shared demo/live Pocket Option account-mode verification - the single
source of truth for both BrowserWarmupService.start()'s initial check and
BrowserHealthManager's ongoing per-worker deep health check, so the two
can never drift out of sync.

2026-07-31 live-verification fix: the original design assumed live mode
could be verified the same way as demo - a single CSS class present on
<body> (LIVE_MODE_VERIFICATION_CLASS). Inspecting a real live Pocket
Option cabinet proved that assumption false: <body> carries no demo/
real/live-specific class there at all. The only positive, human-readable
signal is a nested status element:
    <div class="type-of-trade-label type-of-trade-label--real">
      You are trading on Real account
    </div>
verify_live_mode below checks that element directly (selector + exact
text + expected class + visibility), NOT document.body, and fails closed
on every condition rather than inferring live from demo's mere absence.
"""


class ModeVerificationResult:
    def __init__(self, passed, mode, checks, failed_check=None, detail=None):
        self.passed = passed
        self.mode = mode
        self.checks = checks  # dict[str, bool] - every condition that was actually evaluated
        self.failed_check = failed_check
        self.detail = detail

    def as_log_dict(self):
        """Safe to log verbatim - only booleans, check names, and this
        module's own short detail strings (selector/expected-vs-actual
        text/URL). Never page HTML, cookies, tokens, or account
        identifiers beyond the broker_account_id the caller already
        logs elsewhere."""
        return {
            "mode": self.mode, "passed": self.passed,
            "checks": dict(self.checks), "failed_check": self.failed_check,
            "detail": self.detail,
        }

    def __repr__(self):
        return f"ModeVerificationResult({self.as_log_dict()!r})"


async def verify_demo_mode(page, demo_class):
    """Unchanged behavior from the original single-class check: a CSS
    class present on <body>."""
    try:
        present = await page.evaluate(
            "(cls) => document.body.classList.contains(cls)", demo_class
        )
    except Exception as e:
        return ModeVerificationResult(
            False, "demo", {"demo_class_present": False}, "page_evaluate",
            f"could not evaluate page: {e}",
        )
    checks = {"demo_class_present": bool(present)}
    if not present:
        return ModeVerificationResult(
            False, "demo", checks, "demo_class_present",
            f"{demo_class!r} class missing on <body>",
        )
    return ModeVerificationResult(True, "demo", checks)


# Single round trip: resolves the selector, and - only when it matches
# exactly one element - reads that element's visibility/text/class in the
# same evaluate() call, so the page can't change state between separate
# calls. Also reads window.location.href and the demo class in the same
# pass for the same reason.
_LIVE_ELEMENT_PROBE_JS = """
(args) => {
    const [selector, demoClass] = args;
    const matches = document.querySelectorAll(selector);
    const out = {
        url: window.location.href,
        demo_class_present: document.body.classList.contains(demoClass),
        match_count: matches.length,
    };
    if (matches.length === 1) {
        const el = matches[0];
        const rect = el.getBoundingClientRect();
        out.visible = !!el.offsetParent && rect.width > 0 && rect.height > 0;
        out.text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
        out.class_name = el.className || '';
    }
    return out;
}
"""


def _selector_class_token(selector):
    """Best-effort: a simple '.some-class' selector's bare class name, for
    the redundant classList assertion below - None for anything more
    complex (combinators, attribute selectors, descendant selectors,
    etc.), in which case that one extra check is skipped rather than
    guessed at from an ambiguous selector string."""
    if selector and selector.startswith(".") and len(selector) > 1 and all(
        c.isalnum() or c in "-_" for c in selector[1:]
    ):
        return selector[1:]
    return None


async def verify_live_mode(page, *, selector, expected_text, demo_class, live_url,
                            broker_account_id=None, account_lookup=None):
    """Fails closed on every condition - ANY of these being false, absent,
    or ambiguous means "not verifiably live", never "probably fine":

      1. selector resolves to EXACTLY one element (0 = absent, >1 =
         ambiguous/conflicting indicators - both refused, never guessed)
      2. that element is actually visible (not a hidden template/clone)
      3. its whitespace-normalized text matches expected_text exactly
      4. it carries the expected real-account CSS class (derived from
         selector when selector is a plain '.class' form) - a second,
         independent assertion beyond "the selector happened to match"
      5. the demo-mode class is NOT also present on <body> (a page in a
         genuinely inconsistent state must never read as live)
      6. the page's current URL matches the configured live_url
      7. (only if broker_account_id and account_lookup are both given)
         the broker account's OWN persisted config agrees this account is
         live-enabled right now - re-checked fresh here rather than
         trusted from whatever mode the caller was constructed with
         earlier.

    account_lookup, if given, is a broker_account_id -> bool callable
    (kept as an injected callable rather than importing database/
    broker_account_manager directly here, both to keep this module
    Playwright-only + side-effect-free and to avoid a circular import -
    core/broker_account_manager.py already imports execution/
    browser_warmup.py, which is this module's caller)."""
    checks = {}
    if not selector or not expected_text:
        return ModeVerificationResult(
            False, "live", checks, "not_configured",
            "LIVE_MODE_VERIFICATION_SELECTOR/LIVE_MODE_VERIFICATION_TEXT not set",
        )

    try:
        raw = await page.evaluate(_LIVE_ELEMENT_PROBE_JS, [selector, demo_class])
    except Exception as e:
        return ModeVerificationResult(
            False, "live", checks, "page_evaluate", f"could not evaluate page: {e}",
        )

    match_count = raw.get("match_count", 0)
    checks["selector_resolves_uniquely"] = (match_count == 1)
    if match_count == 0:
        return ModeVerificationResult(False, "live", checks, "selector_absent",
                                       f"selector {selector!r} matched no elements")
    if match_count > 1:
        return ModeVerificationResult(False, "live", checks, "selector_ambiguous",
                                       f"selector {selector!r} matched {match_count} elements")

    checks["element_visible"] = bool(raw.get("visible"))
    if not checks["element_visible"]:
        return ModeVerificationResult(False, "live", checks, "element_hidden",
                                       f"element matching {selector!r} is not visible")

    actual_text = raw.get("text", "")
    checks["text_matches"] = (actual_text == expected_text)
    if not checks["text_matches"]:
        return ModeVerificationResult(False, "live", checks, "text_mismatch",
                                       f"expected text {expected_text!r}, got {actual_text!r}")

    expected_class = _selector_class_token(selector)
    class_name = raw.get("class_name", "")
    has_class = expected_class is None or expected_class in class_name.split()
    checks["element_has_expected_class"] = has_class
    if not has_class:
        return ModeVerificationResult(False, "live", checks, "class_missing",
                                       f"element does not carry expected class {expected_class!r}")

    checks["demo_class_absent"] = not raw.get("demo_class_present", False)
    if not checks["demo_class_absent"]:
        return ModeVerificationResult(False, "live", checks, "demo_class_present",
                                       f"{demo_class!r} is ALSO present on <body> - inconsistent state")

    actual_url = (raw.get("url") or "").rstrip("/")
    expected_url = (live_url or "").rstrip("/")
    checks["url_matches_configured_live_url"] = bool(expected_url) and actual_url == expected_url
    if not checks["url_matches_configured_live_url"]:
        return ModeVerificationResult(False, "live", checks, "url_mismatch",
                                       f"expected url {expected_url!r}, page is at {actual_url!r}")

    if broker_account_id is not None and account_lookup is not None:
        try:
            account_is_live = bool(account_lookup(broker_account_id))
        except Exception as e:
            return ModeVerificationResult(False, "live", checks, "account_lookup_failed",
                                           f"could not verify broker account config: {e}")
        checks["broker_account_marked_live"] = account_is_live
        if not account_is_live:
            return ModeVerificationResult(False, "live", checks, "account_not_live",
                                           f"broker_account_id={broker_account_id} is not currently live-enabled")

    return ModeVerificationResult(True, "live", checks)
