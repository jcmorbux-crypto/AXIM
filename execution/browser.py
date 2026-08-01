import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_session import USER_DATA_DIR, guard_against_production_profile_in_tests


def open_pocket_option():
    # Manual debugging entry point (see __main__ below) - USER_DATA_DIR
    # and this guard both come from browser_session.py, the single
    # authoritative source for both, so this can never drift out of sync
    # with or bypass the same production-profile safety check every other
    # browser-launching path in this codebase goes through.
    guard_against_production_profile_in_tests(USER_DATA_DIR)
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    playwright = sync_playwright().start()

    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=False,
        viewport={"width": 1400, "height": 900},
    )

    page = browser.new_page()
    page.goto("https://pocketoption.com", wait_until="domcontentloaded")

    print("Pocket Option browser opened.")
    print("Log in manually if needed.")
    print("When finished, close the browser window.")

    page.wait_for_timeout(300000)

    browser.close()
    playwright.stop()


if __name__ == "__main__":
    open_pocket_option()