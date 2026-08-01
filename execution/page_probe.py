import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_session import USER_DATA_DIR, guard_against_production_profile_in_tests


def probe_page():
    # USER_DATA_DIR and this guard both come from browser_session.py, the
    # single authoritative source for both - see its
    # guard_against_production_profile_in_tests docstring for the 2026-08-01
    # audit that found this script independently redefining both with no
    # guard at all.
    guard_against_production_profile_in_tests(USER_DATA_DIR)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://pocketoption.com/en/cabinet/demo-quick-high-low/", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        print("\nAXIM PAGE PROBE")
        print("====================")
        print("Title:", page.title())
        print("URL:", page.url)

        text = page.locator("body").inner_text(timeout=10000)

        keywords = [
            "BUY",
            "SELL",
            "Amount",
            "Time",
            "Payout",
            "EUR/USD OTC",
            "Demo",
        ]

        for word in keywords:
            print(f"{word}: {'FOUND' if word in text else 'NOT FOUND'}")

        print("====================")
        print("Probe complete. Close browser when finished.")

        input("Press ENTER to close...")


if __name__ == "__main__":
    probe_page()