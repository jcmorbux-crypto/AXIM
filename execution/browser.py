from pathlib import Path
from playwright.sync_api import sync_playwright

USER_DATA_DIR = Path("sessions/pocket_browser")


def open_pocket_option():
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