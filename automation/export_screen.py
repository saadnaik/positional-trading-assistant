"""Download the MarketSmith India 1-Month Minervini screen as CSV."""

from datetime import datetime, timezone
from pathlib import Path
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright


MARKETSMITH_URL = "https://marketsmithindia.com/"
LANDING_URL = "https://marketsmithindia.com/mstool/landing.jsp#/"
MINERVINI_URL = (
    "https://marketsmithindia.com/mstool/list/marketsmith-stock-screens/"
    "minervini-trend-template-1-month/idealists.jsp#/"
)
SCREEN_NAME = "1-Month Minervini"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"
INCOMING_DIR = PROJECT_ROOT / "data" / "incoming"
LOG_DIR = PROJECT_ROOT / "logs"


class ElementNotFoundError(RuntimeError):
    """Raised when an expected MarketSmith control is not visible."""


def first_visible(locators: list[Locator], description: str) -> Locator:
    for locator in locators:
        try:
            if locator.first.is_visible(timeout=2_000):
                return locator.first
        except PlaywrightError:
            continue
    raise ElementNotFoundError(f"Could not locate {description} on the current page.")


def find_export_control(page: Page) -> Locator:
    export_pattern = re.compile("export", re.IGNORECASE)
    candidates = [
        ("button role", page.get_by_role("button", name=export_pattern)),
        ("link role", page.get_by_role("link", name=export_pattern)),
        ('text containing "Export"', page.get_by_text("Export", exact=False)),
        ('title containing "export"', page.locator('[title*="export" i]')),
        ('aria-label containing "export"', page.locator('[aria-label*="export" i]')),
    ]

    for description, locator in candidates:
        candidate = locator.first
        try:
            candidate.wait_for(state="visible", timeout=10_000)
            is_clickable = candidate.evaluate(
                """element =>
                element.matches('button, a, [role="button"]')"""
            )
            if not is_clickable:
                candidate = candidate.locator(
                    'xpath=ancestor::*[self::button or self::a or @role="button"][1]'
                )
            expect(candidate).to_be_visible(timeout=10_000)
            expect(candidate).to_be_enabled(timeout=10_000)
            print(f"Export locator matched: {description}")
            return candidate
        except PlaywrightError:
            continue

    raise ElementNotFoundError(
        "Could not locate a visible and enabled Export control."
    )


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "export_failure.png"
    html_path = LOG_DIR / "export_failure.html"

    current_url = page.url
    current_title = page.title()
    print(f"Current page URL: {current_url}")
    print(f"Current page title: {current_title}")

    page.screenshot(path=screenshot_path, full_page=True)
    html_path.write_text(page.content(), encoding="utf-8")
    print(f"Failure screenshot saved to {screenshot_path}")
    print(f"Failure page HTML saved to {html_path}")


def wait_for_stock_cards(page: Page) -> Locator:
    candidates = [
        (
            "#idea_lists_placeholder .stockContant",
            page.locator("#idea_lists_placeholder .stockContant").first,
        ),
        (
            ".resultList .stockContant",
            page.locator(".resultList .stockContant").first,
        ),
        (
            ".stocksScroll .stockContant",
            page.locator(".stocksScroll .stockContant").first,
        ),
        (".stockContant", page.locator(".stockContant").first),
    ]

    for description, candidate in candidates:
        try:
            candidate.wait_for(state="attached", timeout=15_000)
            candidate.wait_for(state="visible", timeout=15_000)
            expect(candidate).to_contain_text(re.compile(r"\S"), timeout=15_000)
            print(f"Stock-card locator matched: {description}")
            return candidate
        except PlaywrightError:
            continue

    raise ElementNotFoundError(
        "Could not locate visible MarketSmith stock-card results with non-empty text."
    )


def open_minervini_screen(page: Page) -> None:
    page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_url(re.compile(r"/mstool/landing\.jsp(?:#/)?$"), timeout=60_000)
    page.wait_for_function("document.readyState === 'complete'", timeout=60_000)

    page.goto(MINERVINI_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_url(
        re.compile(
            r"/mstool/list/marketsmith-stock-screens/"
            r"minervini-trend-template-1-month/idealists\.jsp(?:#/)?$"
        ),
        timeout=60_000,
    )
    page.wait_for_function("document.readyState === 'complete'", timeout=60_000)
    wait_for_stock_cards(page)

    print(f"Final URL: {page.url}")
    print(f"Page title: {page.title()}")


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
            try:
                open_minervini_screen(page)

                export_control = find_export_control(page)

                with page.expect_download(timeout=60_000) as download_info:
                    export_control.click()
                download = download_info.value
                destination = INCOMING_DIR / safe_download_name(download.suggested_filename)
                download.save_as(destination)
                if download.failure():
                    raise RuntimeError(
                        f"Browser reported a download failure: {download.failure()}"
                    )

                print(f"CSV downloaded to {destination}")
                browser.close()
                return 0
            except (PlaywrightTimeoutError, PlaywrightError, RuntimeError, OSError) as error:
                print(f"Export failed: {error}")
                try:
                    save_failure_diagnostics(page)
                except (PlaywrightError, OSError) as diagnostic_error:
                    print(f"Could not save complete failure diagnostics: {diagnostic_error}")

                if isinstance(error, ElementNotFoundError):
                    try:
                        input("Press Enter after inspecting the open browser...")
                    except EOFError:
                        print("No interactive input was available; closing the browser.")
                browser.close()
                return 1
    except (PlaywrightTimeoutError, PlaywrightError, RuntimeError, OSError) as error:
        print(f"Export failed: {error}")
        print("No active browser page was available for failure diagnostics.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
