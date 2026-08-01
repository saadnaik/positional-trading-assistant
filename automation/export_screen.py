"""Download the MarketSmith India 1-Month Minervini screen as CSV."""

from datetime import datetime, timezone
from pathlib import Path
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


MARKETSMITH_URL = "https://marketsmithindia.com/"
SCREEN_NAME = "1-Month Minervini"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"
INCOMING_DIR = PROJECT_ROOT / "data" / "incoming"
LOG_DIR = PROJECT_ROOT / "logs"


def first_visible(locators: list[Locator], description: str) -> Locator:
    for locator in locators:
        try:
            if locator.first.is_visible(timeout=2_000):
                return locator.first
        except PlaywrightError:
            continue
    raise RuntimeError(f"Could not locate {description} on the current page.")


def open_minervini_screen(page: Page) -> None:
    target_pattern = re.compile(r"^1[ -]Month Minervini$", re.IGNORECASE)
    target_locators = [
        page.get_by_role("link", name=target_pattern),
        page.get_by_role("button", name=target_pattern),
        page.get_by_text(target_pattern, exact=True),
    ]

    try:
        target = first_visible(target_locators, f"the {SCREEN_NAME!r} screen")
    except RuntimeError:
        screens_pattern = re.compile(r"^(screens|screeners?)$", re.IGNORECASE)
        screens = first_visible(
            [
                page.get_by_role("link", name=screens_pattern),
                page.get_by_role("button", name=screens_pattern),
                page.get_by_text(screens_pattern, exact=True),
            ],
            "the Screens navigation control",
        )
        screens.click()
        page.wait_for_timeout(1_000)
        target = first_visible(target_locators, f"the {SCREEN_NAME!r} screen")

    target.click()
    page.wait_for_load_state("domcontentloaded")


def safe_download_name(suggested_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(suggested_name).name)
    if not cleaned.lower().endswith(".csv"):
        cleaned = f"{cleaned}.csv"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{cleaned}"


def main() -> int:
    if not AUTH_STATE.is_file():
        print(f"Authentication state not found: {AUTH_STATE}")
        print("Run automation/login.py and complete manual login first.")
        return 1

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    page: Page | None = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                storage_state=AUTH_STATE,
                accept_downloads=True,
            )
            page = context.new_page()
            page.goto(MARKETSMITH_URL, wait_until="domcontentloaded", timeout=60_000)
            open_minervini_screen(page)

            export_pattern = re.compile(r"export", re.IGNORECASE)
            export_control = first_visible(
                [
                    page.get_by_role("button", name=export_pattern),
                    page.get_by_role("link", name=export_pattern),
                    page.get_by_text(export_pattern, exact=True),
                    page.locator('[title*="Export" i], [aria-label*="Export" i]'),
                ],
                "an Export control",
            )

            with page.expect_download(timeout=60_000) as download_info:
                export_control.click()
            download = download_info.value
            destination = INCOMING_DIR / safe_download_name(download.suggested_filename)
            download.save_as(destination)
            if download.failure():
                raise RuntimeError(f"Browser reported a download failure: {download.failure()}")

            print(f"CSV downloaded to {destination}")
            browser.close()
        return 0
    except (PlaywrightTimeoutError, PlaywrightError, RuntimeError, OSError) as error:
        print(f"Export failed: {error}")
        if page is not None:
            screenshot = LOG_DIR / "export_failure.png"
            try:
                page.screenshot(path=screenshot, full_page=True)
                print(f"Failure screenshot saved to {screenshot}")
            except PlaywrightError as screenshot_error:
                print(f"Could not save failure screenshot: {screenshot_error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
