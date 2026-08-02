"""Extract summary metrics from a MarketSmith stock evaluation page."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

from automation.read_csv import latest_csv, read_symbols


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"
LOG_DIR = PROJECT_ROOT / "logs"
EVALUATION_URL = (
    "https://marketsmithindia.com/mstool/eval/list/{symbol}/evaluation.jsp"
)
SUMMARY_SELECTOR = "#slide-details_placeholder"
PERCENTAGE_VALUE = re.compile(r"^[+-]?\d+(?:\.\d+)?%$")
NUMERIC_VALUE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
EPS_LABEL = re.compile(r"^\s*EPS\s+Growth\s+Rate\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SummaryMetrics:
    eps_growth_rate: str
    earnings_stability: str
    return_on_equity: str


class SummaryExtractionError(RuntimeError):
    """Raised when MarketSmith summary data cannot be extracted safely."""


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def normalized_text(value: str) -> str:
    return normalize_whitespace(value).casefold()


def confirm_stock_page(page: Page, symbol: str, company_name: str) -> None:
    expected_path = f"/mstool/eval/list/{symbol}/evaluation.jsp".casefold()
    actual_path = urlsplit(page.url).path.casefold()
    if actual_path != expected_path:
        raise SummaryExtractionError(
            f"Unexpected evaluation page URL for {symbol}: {page.url}"
        )

    title = page.title()
    if normalized_text(company_name) not in normalized_text(title):
        raise SummaryExtractionError(
            f"Page title does not contain expected company {company_name!r}: {title!r}"
        )


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "summary_failure.png"
    html_path = LOG_DIR / "summary_failure.html"

    try:
        print(f"Current page URL: {page.url}")
        print(f"Current page title: {page.title()}")
    except PlaywrightError as error:
        print(f"Could not read current page metadata: {error}")

    try:
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Failure screenshot saved to {screenshot_path}")
    except (PlaywrightError, OSError) as error:
        print(f"Could not save failure screenshot: {error}")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"Failure page HTML saved to {html_path}")
    except (PlaywrightError, OSError) as error:
        print(f"Could not save failure page HTML: {error}")


def financial_mode_controls(page: Page) -> tuple[Locator, Locator, Locator]:
    summary = page.locator(SUMMARY_SELECTOR)
    summary.wait_for(state="attached", timeout=60_000)

    consolidated = summary.locator("#consolidated1")
    standalone = summary.locator("#standalone1")
    consolidated.wait_for(state="attached", timeout=60_000)
    standalone.wait_for(state="attached", timeout=60_000)
    return summary, consolidated, standalone


def verify_consolidated_mode(page: Page) -> None:
    _, consolidated, standalone = financial_mode_controls(page)
    try:
        expect(consolidated).to_be_checked(timeout=60_000)
        expect(standalone).not_to_be_checked(timeout=60_000)
    except AssertionError as error:
        raise SummaryExtractionError(
            "Could not verify Consolidated financial mode; refusing to read summary data."
        ) from error
    if not consolidated.is_checked() or standalone.is_checked():
        raise SummaryExtractionError(
            "Could not verify Consolidated financial mode; refusing to read summary data."
        )


def ensure_consolidated_mode(page: Page) -> None:
    _, consolidated, standalone = financial_mode_controls(page)
    if consolidated.is_checked() and not standalone.is_checked():
        verify_consolidated_mode(page)
        return

    consolidated_label = page.locator(
        f'{SUMMARY_SELECTOR} label[for="consolidated1"]:visible'
    )
    consolidated_label.wait_for(state="visible", timeout=60_000)

    try:
        with page.expect_response(
            lambda response: "financeDetails.json" in urlsplit(response.url).path,
            timeout=60_000,
        ) as response_info:
            consolidated_label.evaluate("element => element.click()")
        response_info.value.body()
    except PlaywrightTimeoutError as error:
        raise SummaryExtractionError(
            "Timed out waiting for MarketSmith financeDetails.json refresh."
        ) from error

    # MarketSmith replaces summary markup during the refresh, so do not reuse
    # any control or data locators acquired before the click.
    wait_for_summary_metrics(page)
    verify_consolidated_mode(page)


def visible_exact_label(locator: Locator, expected_label: str) -> bool:
    try:
        return locator.is_visible() and normalized_text(locator.inner_text()) == normalized_text(
            expected_label
        )
    except PlaywrightError:
        return False


def desktop_value(summary: Locator, label_text: str) -> str | None:
    labels = summary.locator("tr td:first-child")
    for index in range(labels.count()):
        label = labels.nth(index)
        if not visible_exact_label(label, label_text):
            continue
        value_cell = label.locator("xpath=following-sibling::td[1]")
        if value_cell.count() and value_cell.first.is_visible():
            return normalize_whitespace(value_cell.first.inner_text())
    return None


def alternate_value(summary: Locator, label_text: str) -> str | None:
    rows = summary.locator(".details-metric-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        label = row.locator(".details").first
        if not visible_exact_label(label, label_text):
            continue
        value = row.locator(".value").first
        if value.count() and value.is_visible():
            return normalize_whitespace(value.inner_text())
    return None


def extract_value(summary: Locator, label: str) -> str:
    value = desktop_value(summary, label)
    if value is None:
        value = alternate_value(summary, label)
    if value is None:
        raise SummaryExtractionError(f"Could not find visible summary field {label!r}.")
    if not value:
        raise SummaryExtractionError(f"Summary field {label!r} is empty.")
    return value


def validate_percentage(label: str, value: str) -> str:
    if not PERCENTAGE_VALUE.fullmatch(value):
        raise SummaryExtractionError(
            f"Summary field {label!r} has malformed percentage text: {value!r}."
        )
    return value


def validate_numeric(label: str, value: str) -> str:
    if not NUMERIC_VALUE.fullmatch(value):
        raise SummaryExtractionError(
            f"Summary field {label!r} has malformed numeric text: {value!r}."
        )
    return value


def wait_for_summary_metrics(page: Page) -> None:
    summary = page.locator(SUMMARY_SELECTOR)
    summary.wait_for(state="attached", timeout=60_000)
    candidates = [
        summary.locator("tr td:first-child", has_text=EPS_LABEL).first,
        summary.locator(".details", has_text=EPS_LABEL).first,
    ]
    for label in candidates:
        try:
            label.wait_for(state="visible", timeout=60_000)
            if visible_exact_label(label, "EPS Growth Rate"):
                return
        except PlaywrightTimeoutError:
            continue
    raise SummaryExtractionError("MarketSmith summary metrics did not become visible.")


def extract_summary_metrics(page: Page) -> SummaryMetrics:
    """Return raw Consolidated summary values from an open evaluation page."""
    ensure_consolidated_mode(page)
    verify_consolidated_mode(page)

    # Reacquire the root after ensure_consolidated_mode because switching modes
    # refreshes and replaces the summary DOM.
    summary = page.locator(SUMMARY_SELECTOR)
    summary.wait_for(state="attached", timeout=60_000)

    eps_growth_rate = validate_percentage(
        "EPS Growth Rate", extract_value(summary, "EPS Growth Rate")
    )
    earnings_stability = validate_numeric(
        "Earnings Stability", extract_value(summary, "Earnings Stability")
    )
    return_on_equity = validate_percentage(
        "Return on Equity", extract_value(summary, "Return on Equity")
    )

    return SummaryMetrics(
        eps_growth_rate=eps_growth_rate,
        earnings_stability=earnings_stability,
        return_on_equity=return_on_equity,
    )


def pause_before_close() -> None:
    try:
        input("Press Enter to close the browser...")
    except EOFError:
        print("No interactive input was available; closing the browser.")


def main() -> int:
    if not AUTH_STATE.is_file():
        print(f"Authentication state not found: {AUTH_STATE}")
        print("Run automation/login.py and complete manual login first.")
        return 1

    try:
        csv_path = latest_csv()
        records = read_symbols(csv_path)
        symbol, company_name = records[0]
    except (ValueError, OSError, IndexError) as error:
        print(f"Could not read the first validated stock: {error}")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(storage_state=AUTH_STATE)
            page = context.new_page()
            try:
                page.goto(
                    EVALUATION_URL.format(symbol=symbol),
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                confirm_stock_page(page, symbol, company_name)
                metrics = extract_summary_metrics(page)

                print(f"Symbol: {symbol}")
                print(f"Company: {company_name}")
                print(f"Page URL: {page.url}")
                print("Financial mode: Consolidated")
                print(f"EPS Growth Rate: {metrics.eps_growth_rate}")
                print(f"Earnings Stability: {metrics.earnings_stability}")
                print(f"Return on Equity: {metrics.return_on_equity}")
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                SummaryExtractionError,
                OSError,
            ) as error:
                print(f"Summary metrics extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
