"""Extract EPS Growth Rate for the first stock in the latest validated CSV."""

from pathlib import Path
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

from read_csv import latest_csv, read_symbols


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_STATE = PROJECT_ROOT / "auth" / "marketsmith_state.json"
LOG_DIR = PROJECT_ROOT / "logs"
EVALUATION_URL = (
    "https://marketsmithindia.com/mstool/eval/list/{symbol}/evaluation.jsp"
)
EPS_LABEL = re.compile(r"^\s*EPS\s+Growth\s+Rate\s*$", re.IGNORECASE)
PERCENTAGE = re.compile(r"(?<!\w)[+-]?\d+(?:\.\d+)?\s*%")


def normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def confirm_stock_page(page: Page, symbol: str, company_name: str) -> None:
    expected_path = f"/mstool/eval/list/{symbol}/evaluation.jsp".casefold()
    actual_path = urlsplit(page.url).path.casefold()
    if actual_path != expected_path:
        raise RuntimeError(
            f"Unexpected evaluation page URL for {symbol}: {page.url}"
        )

    title = page.title()
    if normalized_text(company_name) not in normalized_text(title):
        raise RuntimeError(
            f"Page title does not contain expected company {company_name!r}: {title!r}"
        )


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "eps_growth_failure.png"
    html_path = LOG_DIR / "eps_growth_failure.html"

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
    summary = page.locator("#slide-details_placeholder")
    summary.wait_for(state="attached", timeout=60_000)

    consolidated = summary.locator("#consolidated1")
    standalone = summary.locator("#standalone1")
    consolidated.wait_for(state="attached", timeout=60_000)
    standalone.wait_for(state="attached", timeout=60_000)
    return summary, consolidated, standalone


def ensure_consolidated_mode(page: Page) -> None:
    _, consolidated, standalone = financial_mode_controls(page)
    if consolidated.is_checked() and not standalone.is_checked():
        return

    consolidated_label = page.locator(
        '#slide-details_placeholder label[for="consolidated1"]'
    )
    consolidated_label.wait_for(state="visible", timeout=60_000)

    try:
        with page.expect_response(
            lambda response: "financeDetails.json" in urlsplit(response.url).path,
            timeout=60_000,
        ) as response_info:
            consolidated_label.evaluate("element => element.click()")
        response = response_info.value
        response.body()
    except PlaywrightTimeoutError as error:
        raise RuntimeError(
            "Timed out waiting for MarketSmith financeDetails.json refresh."
        ) from error

    _, consolidated, standalone = financial_mode_controls(page)
    wait_for_eps_summary(page)
    expect(consolidated).to_be_checked(timeout=60_000)
    expect(standalone).not_to_be_checked(timeout=60_000)

    if not consolidated.is_checked() or standalone.is_checked():
        raise RuntimeError("Could not verify Consolidated financial mode.")


def percentage_from_text(text: str) -> str | None:
    match = PERCENTAGE.search(" ".join(text.split()))
    return match.group(0).replace(" ", "") if match else None


def value_from_table_label(label: Locator) -> str | None:
    value_cell = label.locator("xpath=following-sibling::td[1]")
    if value_cell.count() and value_cell.first.is_visible():
        return percentage_from_text(value_cell.first.inner_text())

    row = label.locator("xpath=ancestor::tr[1]")
    if row.count() and row.first.is_visible():
        return percentage_from_text(row.first.inner_text())
    return None


def value_from_details_label(label: Locator) -> str | None:
    parent = label.locator("xpath=..").first
    if parent.count() and parent.is_visible():
        return percentage_from_text(parent.inner_text())
    return None


def extract_eps_growth_rate(page: Page) -> str:
    candidates = [
        (
            "desktop summary table",
            page.locator("td.text-left").filter(has_text=EPS_LABEL),
            value_from_table_label,
        ),
        (
            "alternate summary details",
            page.locator("div.details").filter(has_text=EPS_LABEL),
            value_from_details_label,
        ),
    ]

    for _, labels, extractor in candidates:
        for index in range(labels.count()):
            label = labels.nth(index)
            try:
                if not label.is_visible():
                    continue
                value = extractor(label)
                if value is not None:
                    return value
            except PlaywrightError:
                continue

    raise RuntimeError("Could not find a visible EPS Growth Rate percentage.")


def wait_for_eps_summary(page: Page) -> None:
    labels = [
        page.locator("td.text-left").filter(has_text=EPS_LABEL).first,
        page.locator("div.details").filter(has_text=EPS_LABEL).first,
    ]
    for label in labels:
        try:
            label.wait_for(state="visible", timeout=60_000)
            return
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError("EPS Growth Rate summary did not become visible.")


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
                ensure_consolidated_mode(page)
                wait_for_eps_summary(page)
                eps_growth_rate = extract_eps_growth_rate(page)

                print(f"Symbol: {symbol}")
                print(f"Company: {company_name}")
                print(f"Page URL: {page.url}")
                print("Financial mode: Consolidated")
                print(f"EPS Growth Rate: {eps_growth_rate}")
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                RuntimeError,
                OSError,
            ) as error:
                print(f"EPS Growth Rate extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
