from pathlib import Path
from playwright.sync_api import sync_playwright

USER_DATA_DIR = Path("sessions/pocket_browser")


def probe_page():
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