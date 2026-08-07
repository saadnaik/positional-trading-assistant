"""Download a configured MarketSmith India stock screen as CSV."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright


@dataclass(frozen=True)
class ScreenConfig:
    name: str
    cli_name: str
    url: str
    output_directory: Path
    expected_filename_hint: str
    role: Literal["primary", "signal"]


BUILD_YOUR_SCREEN = ScreenConfig(
    name="Build Your Screen",
    cli_name="build-your-screen",
    url=(
        "https://marketsmithindia.com/mstool/list/build-your-screen/"
        "filter-india-stocks/idealists.jsp#/"
    ),
    output_directory=Path("data/incoming/build_your_screen"),
    expected_filename_hint="Filter_India_Stocks",
    role="primary",
)

MINERVINI_1_MONTH = ScreenConfig(
    name="Mark Minervini 1-Month",
    cli_name="minervini",
    url=(
        "https://marketsmithindia.com/mstool/list/marketsmith-stock-screens/"
        "minervini-trend-template-1-month/idealists.jsp#/"
    ),
    output_directory=Path("data/incoming/minervini_1_month"),
    expected_filename_hint="Minervini_Trend_Template-1_Month",
    role="signal",
)

SCREENS = {
    "build-your-screen": BUILD_YOUR_SCREEN,
    "minervini": MINERVINI_1_MONTH,
}

LANDING_URL = "https://marketsmithindia.com/mstool/landing.jsp#/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"
LOG_DIR = PROJECT_ROOT / "logs"


class ElementNotFoundError(RuntimeError):
    """Raised when an expected MarketSmith control is not visible."""


class SessionExpiredError(RuntimeError):
    """Raised when the saved MarketSmith session no longer grants access."""


def _normalized_url_parts(url: str) -> tuple[str, str, str, str]:
    parts = urlsplit(url)
    return (
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        parts.fragment.rstrip("/"),
    )


def _matches_url(actual_url: str, expected_url: str) -> bool:
    return _normalized_url_parts(actual_url) == _normalized_url_parts(expected_url)


def _looks_like_login(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in ("login", "log-in", "signin", "sign-in"))


def _verify_url(page: Page, expected_url: str, description: str) -> None:
    if _looks_like_login(page.url):
        raise SessionExpiredError(
            "MarketSmith redirected to login; the saved authentication session has expired. "
            "Run automation/login.py again."
        )
    if not _matches_url(page.url, expected_url):
        raise RuntimeError(
            f"Expected {description} URL {expected_url!r}, but reached {page.url!r}. "
            "The saved MarketSmith session may have expired."
        )


def export_locator_candidates(page: Page) -> list[tuple[str, Locator]]:
    """Return Export locators in preferred-to-fallback order."""

    export_pattern = re.compile(r"^Export$", re.IGNORECASE)
    return [
        ("#ideaDownloadList", page.locator("#ideaDownloadList")),
        ("button role", page.get_by_role("button", name=export_pattern)),
        ("link role", page.get_by_role("link", name=export_pattern)),
        ('exact text "Export"', page.get_by_text(export_pattern, exact=True)),
        ('title containing "export"', page.locator('[title*="export" i]')),
        ('aria-label containing "export"', page.locator('[aria-label*="export" i]')),
    ]


def find_export_control(page: Page) -> Locator:
    for description, locator in export_locator_candidates(page):
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


def dismiss_screen_information_modal(page: Page) -> None:
    """Dismiss MarketSmith's optional screen-description overlay."""

    modal = page.locator(".modalWithArrow.in:visible").first
    if not modal.is_visible():
        return
    close_control = modal.locator('[data-dismiss="modal"]', has_text="Close").first
    close_control.click()
    modal.wait_for(state="hidden", timeout=10_000)
    print("Dismissed screen information modal")


def save_failure_diagnostics(page: Page) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = LOG_DIR / "export_failure.png"
    html_path = LOG_DIR / "export_failure.html"

    print(f"Current page URL: {page.url}")
    print(f"Current page title: {page.title()}")

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


def open_screen(page: Page, config: ScreenConfig) -> Locator:
    page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_function("document.readyState === 'complete'", timeout=60_000)
    _verify_url(page, LANDING_URL, "MarketSmith landing page")

    page.goto(config.url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_function("document.readyState === 'complete'", timeout=60_000)
    _verify_url(page, config.url, config.name)
    try:
        stock_card = wait_for_stock_cards(page)
    except ElementNotFoundError:
        if not _matches_url(page.url, config.url):
            raise SessionExpiredError(
                f"MarketSmith left the {config.name!r} route and returned to "
                f"{page.url!r}; the saved authentication session has likely expired. "
                "Run automation/login.py again."
            ) from None
        raise

    print(f"Final URL: {page.url}")
    print(f"Page title: {page.title()}")
    return stock_card


def configured_output_directory(config: ScreenConfig) -> Path:
    if config.output_directory.is_absolute():
        raise ValueError("Screen output_directory must be relative to the project root.")
    destination = (PROJECT_ROOT / config.output_directory).resolve()
    incoming_root = (PROJECT_ROOT / "data" / "incoming").resolve()
    if destination == incoming_root or incoming_root not in destination.parents:
        raise ValueError(
            "Screen output_directory must be a dedicated directory under data/incoming."
        )
    return destination


def timestamped_destination(
    config: ScreenConfig,
    suggested_filename: str,
    *,
    now: datetime | None = None,
) -> Path:
    suggested_basename = Path(suggested_filename).name
    if not suggested_basename:
        raise ValueError("Browser did not suggest a download filename.")
    if config.expected_filename_hint not in suggested_basename:
        raise RuntimeError(
            f"Unexpected filename for {config.name}: {suggested_basename!r}; "
            f"expected it to contain {config.expected_filename_hint!r}."
        )

    output_directory = configured_output_directory(config)
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    destination = output_directory / (
        f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{suggested_basename}"
    )
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing download: {destination}")
    return destination


def _pause_for_browser_inspection() -> None:
    try:
        input("Press Enter after inspecting the open browser...")
    except EOFError:
        print("No interactive input was available; closing the browser.")


def export_screen(
    config: ScreenConfig,
    *,
    pause_on_failure: bool = True,
) -> Path:
    """Export one configured MarketSmith screen and return its final CSV path."""

    if not AUTH_STATE.is_file():
        raise FileNotFoundError(
            f"Authentication state not found: {AUTH_STATE}. "
            "Run automation/login.py and complete manual login first."
        )

    page: Page | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context(
                storage_state=AUTH_STATE,
                accept_downloads=True,
            )
            page = context.new_page()
            try:
                open_screen(page, config)
                dismiss_screen_information_modal(page)
                export_control = find_export_control(page)

                with page.expect_download(timeout=60_000) as download_info:
                    export_control.click()
                download = download_info.value
                destination = timestamped_destination(
                    config, download.suggested_filename
                )
                download.save_as(destination)
                if download.failure():
                    raise RuntimeError(
                        f"Browser reported a download failure: {download.failure()}"
                    )
                return destination
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                RuntimeError,
                OSError,
                ValueError,
            ):
                try:
                    save_failure_diagnostics(page)
                except (PlaywrightError, OSError) as diagnostic_error:
                    print(f"Could not save complete failure diagnostics: {diagnostic_error}")
                if pause_on_failure:
                    _pause_for_browser_inspection()
                raise
        finally:
            browser.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "screen",
        choices=sorted(SCREENS),
        help="MarketSmith screen to export",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = SCREENS[args.screen]
    try:
        destination = export_screen(config)
    except (
        PlaywrightTimeoutError,
        PlaywrightError,
        RuntimeError,
        OSError,
        ValueError,
    ) as error:
        print(f"Export failed: {error}")
        return 1

    print(f"Screen: {config.name}")
    print(f"CSV downloaded to: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
