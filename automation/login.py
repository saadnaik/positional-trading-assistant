"""Open MarketSmith India for manual login and save browser authentication state."""

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


MARKETSMITH_URL = "https://marketsmithindia.com/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"


def main() -> int:
    AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(MARKETSMITH_URL, wait_until="domcontentloaded", timeout=60_000)

            print("Log in manually in the Chromium window.")
            input("After login is complete, press Enter here to save the session: ")

            context.storage_state(path=AUTH_STATE)
            print(f"Authentication state saved to {AUTH_STATE}")
            browser.close()
        return 0
    except (PlaywrightError, OSError, EOFError) as error:
        print(f"Login session failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
