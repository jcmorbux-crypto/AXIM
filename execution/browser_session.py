import json
import os
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pocket_dom

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_DATA_DIR = PROJECT_ROOT / "sessions" / "pocket_browser"
DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"


class ProductionProfileInTestError(Exception):
    """Raised instead of ever letting a test touch the real Pocket Option
    profile - see guard_against_production_profile_in_tests's own
    docstring for the 2026-07-31 incident this exists to make structurally
    impossible, not just discouraged by convention."""
    pass


def guard_against_production_profile_in_tests(user_data_dir):
    """Hard safety guard, not a naming convention: refuses to open a
    browser against the production Pocket Option profile
    (sessions/pocket_browser) while running under pytest - called by
    PocketBrowserSession.__init__ below, and by every other module in
    this codebase that can launch a real browser against USER_DATA_DIR
    (execution/browser.py, execution/page_probe.py - see the 2026-08-01
    audit note below).

    2026-07-31: while investigating a stalled full-suite pytest run, the
    production profile directory was found under contention from a Chrome
    process that (initial, later-corrected analysis showed) most likely
    belonged to the LIVE listener's own legitimate reconnect - but the
    investigation surfaced that nothing in this codebase actually
    PREVENTED a test from launching a real browser against that exact
    directory; only convention (every existing browser-touching test
    happens to use an isolated temp profile) did. A single missed
    `user_data_dir=` override in a future test would have opened, and
    could have corrupted, the real production session - cookies, login
    state, account mode - with no code-level check to stop it. This
    closes that gap unconditionally, not just for the one test file
    that prompted it.

    2026-08-01 audit: execution/browser.py and execution/page_probe.py
    turned out to be a second and third gap of the exact same shape - both
    standalone manual debugging scripts that independently redefined their
    own USER_DATA_DIR = Path("sessions/pocket_browser") and called
    launch_persistent_context directly, with zero guard, zero relation to
    this function, and (being ordinary importable modules under
    execution/, the exact directory every test's own sys.path.insert
    already reaches into) nothing stopping a future test from importing
    either one and hitting the same incident this function exists to
    prevent. Both now import USER_DATA_DIR from here instead of
    redefining it, and both call this function before launching - this
    module is the only place either the path or the guard is defined.

    Path normalization (resolve()) so a relative-path trick
    ('../../sessions/pocket_browser', a symlink, a trailing
    'sessions/pocket_browser/./'), not just a literal string match,
    cannot bypass this - both the candidate and the real production path
    are fully resolved to their real, absolute, symlink-free form before
    comparing. Matches on EQUALITY (the production dir itself) or
    CONTAINMENT (anything inside it, e.g. a test pointing at
    'sessions/pocket_browser/subdir') - both refused.

    Detects "running under pytest" the same way core/logger.py already
    does ("pytest" in sys.modules) - a real production run (the listener
    process, or a manual non-test script) never has pytest imported at
    all, so this never fires outside a test process, and does not apply
    to core/logger.py's LOG_DIR redirect at all (a completely separate
    mechanism for a completely separate resource).

    One deliberate, pre-existing exception: tests/test_pocket_execution_
    dryrun.py intentionally drives a real browser against the real
    Pocket Option DEMO cabinet (never BUY/SELL) for DOM verification,
    already gated behind its own explicit human opt-in
    (AXIM_RUN_LIVE_DOM_TESTS=true - normal pytest runs never set this and
    that test skips itself immediately). This guard defers to that same
    existing opt-in rather than blocking it outright - a second,
    conflicting env var would just invite someone to silence THIS guard
    without realizing it's the same real-browser-against-production-
    profile action either way."""
    if "pytest" not in sys.modules:
        return
    if os.getenv("AXIM_RUN_LIVE_DOM_TESTS", "false").lower() == "true":
        return
    resolved = Path(user_data_dir).resolve()
    production_resolved = USER_DATA_DIR.resolve()
    if resolved == production_resolved or production_resolved in resolved.parents:
        raise ProductionProfileInTestError(
            f"Refusing to launch a browser against {resolved} under pytest - this IS "
            f"(or is inside) the production Pocket Option profile ({production_resolved}). "
            f"Tests must use an isolated temporary profile directory "
            f"(tempfile.TemporaryDirectory(), pytest's tmp_path, or another explicit "
            f"non-production path) - see tests/test_browser_session.py for the established "
            f"pattern."
        )

# Fixed off-screen position/size for the (headed, not headless - Pocket
# Option's own anti-bot checks are the reason this was never switched to
# headless) automation window - confirmed live: leaving it at
# --start-maximized put a real, visible, focus-stealing Chrome window on
# the operator's actual desktop, and a restart-storm (multiple launch
# attempts colliding on the profile lock) could open several of them in a
# row. A large negative position moves the window fully off any real
# monitor's bounds while still rendering/painting normally (unlike
# --start-minimized, which can leave the page unpainted and break
# Playwright's own screenshot/DOM-visibility checks) - Pocket Option's
# layout still gets the same generously-large viewport pocket_dom.py's
# selectors were originally validated against (see the old "1600x1000"
# assumption this replaces), just positioned where no one can see it move.
OFFSCREEN_WINDOW_ARGS = ["--window-position=-32000,-32000", "--window-size=1600,1000"]


def _kill_stale_processes_for(user_data_dir):
    """Startup guard (confirmed necessary live - see docs/AXIM_RELEASE_CHECKLIST.md's
    browser crash investigation): Stop-ScheduledTask on the AXIM Listener
    task does not kill the underlying python.exe (it only stops Task
    Scheduler's own tracking of it - see scripts/run_listener_supervised.ps1's
    Job Object fix for the supervisor-level half of this), so a botched
    restart can leave a fully-alive, orphaned chrome.exe still holding this
    exact user_data_dir's singleton profile lock. Every fresh launch attempt
    against that same directory then fails with Playwright's own "Opening in
    existing browser session" error and pops a new visible window before
    failing - repeatedly, once per retry. Since this codebase's own
    architecture guarantees at most one legitimate process should ever hold
    a given profile directory at a time (adopt_existing_connection's
    docstring), any chrome.exe already holding it at the moment a fresh
    launch is starting is by definition stale, never a second legitimate
    holder - safe to kill unconditionally before proceeding, rather than
    guessing whether it's "still healthy"."""
    resolved = str(Path(user_data_dir).resolve())
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*--user-data-dir=" + resolved + "*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 and result.stderr.strip():
            pass  # best-effort - a launch that still fails afterward surfaces its own real error
    except Exception:
        pass  # best-effort guard, never blocks a launch attempt on its own failure


def _suppress_restore_session_prompt(user_data_dir):
    """Chrome shows a "Restore pages?" prompt on next launch whenever its
    profile's exit_type wasn't "Normal" - true after EVERY forced kill
    (Stop-Process, a genuine crash, or the orphan-cleanup above), which was
    happening routinely enough that a real operator would see it constantly.
    Patched before every launch, unconditionally - cheap, idempotent, and
    always correct regardless of how the previous session actually ended."""
    prefs_path = Path(user_data_dir) / "Default" / "Preferences"
    if not prefs_path.exists():
        return
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        profile = prefs.setdefault("profile", {})
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        prefs_path.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        pass  # best-effort - a stale/corrupt Preferences file just means the prompt may show once


class PocketBrowserSession:
    def __init__(self, user_data_dir=USER_DATA_DIR, headless=False, viewport="maximize"):
        guard_against_production_profile_in_tests(user_data_dir)
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        # "maximize" (default): no fixed content viewport, window opens at
        # OFFSCREEN_WINDOW_ARGS's fixed off-screen position/size rather than
        # a real, visible, maximized-on-the-real-desktop window. Pass an
        # explicit {"width": w, "height": h} dict to force a normal
        # (on-screen, at Playwright's default position) fixed viewport
        # instead - e.g. for headless CI runs where there's no desktop to
        # keep clear in the first place.
        self.viewport = viewport
        self._playwright = None
        self._context = None

    async def __aenter__(self):
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        _kill_stale_processes_for(self.user_data_dir)
        _suppress_restore_session_prompt(self.user_data_dir)
        self._playwright = await async_playwright().start()

        if self.viewport == "maximize":
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                no_viewport=True,
                args=OFFSCREEN_WINDOW_ARGS + ["--disable-session-crashed-bubble"],
            )
        else:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                viewport=self.viewport,
                args=["--disable-session-crashed-bubble"],
            )
        return self._context

    async def __aexit__(self, exc_type, exc, tb):
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()


async def get_trading_page(context, url=DEMO_URL, ready_timeout=15000, reuse_existing=False):
    """reuse_existing=True reuses launch_persistent_context's own
    auto-opened blank tab (context.pages[0]) instead of leaving it idle
    and opening a redundant one - correct ONLY for the single call made
    immediately after a fresh context launch (browser_warmup.py's own
    dedicated page). Every other caller (BrowserWorkerPool building or
    respawning a worker) needs a genuinely separate page and must use
    the default False - context.pages stays non-empty forever after the
    first page exists, so `context.pages[0] if context.pages else ...`
    would otherwise hand out that SAME page object to every subsequent
    caller (confirmed empirically, not assumed): each worker's own
    asyncio.Lock would then protect nothing real, since it guards the
    worker, not the page multiple workers secretly shared - two
    concurrently acquired workers could manipulate the identical
    browser tab at once with no real mutual exclusion."""
    page = context.pages[0] if (reuse_existing and context.pages) else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await expect(page.locator(pocket_dom.SEL_ASSET_TRIGGER).first).to_be_visible(timeout=ready_timeout)
    return page
