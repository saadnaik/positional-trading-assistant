"""Extract Quarterly Earnings values from a MarketSmith evaluation page."""

from dataclasses import dataclass
from pathlib import Path
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

from automation.read_csv import latest_csv, read_symbols
from extract.summary import (
    AUTH_STATE,
    EVALUATION_URL,
    SummaryExtractionError,
    confirm_stock_page,
    ensure_consolidated_mode,
    normalize_whitespace,
    pause_before_close,
    verify_consolidated_mode,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
QUARTERLY_HEADING = "Quarterly Earnings (INR)"
QUARTER_LABEL = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[ -](\d{2}|\d{4})$",
    re.IGNORECASE,
)
QUARTER_ENDS = {
    "mar": "03-31",
    "jun": "06-30",
    "sep": "09-30",
    "dec": "12-31",
}


@dataclass(frozen=True)
class QuarterlyEarningsRecord:
    quarter_label: str
    quarter_end_date: str | None
    eps: str
    eps_change_percent: str
    sales: str
    sales_change_percent: str


@dataclass(frozen=True)
class QuarterlyEarnings:
    records: tuple[QuarterlyEarningsRecord, ...]


class QuarterlyEarningsExtractionError(RuntimeError):
    """Raised when Quarterly Earnings cannot be extracted safely."""


@dataclass(frozen=True)
class QuarterlyTable:
    table_selector: str
    expand_selector: str
    heading_selector: str


TABLE_CANDIDATES = (
    QuarterlyTable(
        table_selector="#formattedSalesAndEarningTable",
        expand_selector="#salesAndEarningsTablePlus",
        heading_selector="xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' quarterlyEarnings ')][1]//h2",
    ),
    QuarterlyTable(
        table_selector="#sldformattedSalesAndEarningTable",
        expand_selector="#sldsalesAndEarningsTablePlus",
        heading_selector="xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' row ')][1]//h6",
    ),
)


def normalized_casefold(value: str) -> str:
    return normalize_whitespace(value).casefold()


def quarter_end_date(quarter_label: str) -> str | None:
    """Map a recognized MarketSmith fiscal-quarter label to an ISO date."""
    match = QUARTER_LABEL.fullmatch(normalize_whitespace(quarter_label))
    if match is None:
        return None

    month, displayed_year = match.groups()
    month_end = QUARTER_ENDS.get(month.casefold())
    if month_end is None:
        return None

    year = int(displayed_year)
    if len(displayed_year) == 2:
        year += 2000
    return f"{year:04d}-{month_end}"


def visible(locator: Locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except PlaywrightError:
        return False


def locate_quarterly_table(page: Page) -> tuple[QuarterlyTable, Locator]:
    for candidate in TABLE_CANDIDATES:
        table = page.locator(candidate.table_selector).first
        if not visible(table):
            continue

        heading = table.locator(candidate.heading_selector).first
        if not visible(heading):
            continue
        if normalized_casefold(heading.inner_text()) != normalized_casefold(
            QUARTERLY_HEADING
        ):
            continue
        return candidate, table

    raise QuarterlyEarningsExtractionError(
        "Could not find a visible Quarterly Earnings (INR) table."
    )


def validate_headers(table: Locator) -> int:
    headers = [
        normalize_whitespace(value)
        for value in table.locator("thead tr th").all_inner_texts()
    ]
    if len(headers) < 5:
        raise QuarterlyEarningsExtractionError(
            f"Quarterly Earnings table has {len(headers)} headers; expected at least 5."
        )

    normalized = [value.casefold() for value in headers[:5]]
    valid = (
        normalized[0].startswith("date")
        and normalized[1] == "eps"
        and normalized[2] == "%chg"
        and normalized[3] == "sales(cr)"
        and normalized[4] == "%chg"
    )
    if not valid:
        raise QuarterlyEarningsExtractionError(
            "Unexpected Quarterly Earnings headers: " + " | ".join(headers)
        )
    return len(headers)


def expand_quarterly_history(page: Page, candidate: QuarterlyTable) -> None:
    expand = page.locator(candidate.expand_selector).first
    if not visible(expand):
        return

    expand.click()

    # The expansion is a client-side DOM update. Wait for its control to be
    # hidden, then reacquire the table and rows rather than retaining locators.
    try:
        expect(expand).to_be_hidden(timeout=10_000)
    except AssertionError as error:
        raise QuarterlyEarningsExtractionError(
            "Quarterly Earnings Show More control did not complete expansion."
        ) from error

    _, table = locate_quarterly_table(page)
    rows = table.locator("tbody tr")
    if rows.count() == 0:
        raise QuarterlyEarningsExtractionError(
            "Quarterly Earnings table is empty after expanding its history."
        )


def row_values(
    row: Locator, row_number: int, expected_cell_count: int
) -> tuple[str, str, str, str, str]:
    cells = row.locator(":scope > td")
    actual_cell_count = cells.count()
    if actual_cell_count != expected_cell_count:
        raise QuarterlyEarningsExtractionError(
            f"Quarterly Earnings row {row_number} has {actual_cell_count} cells; "
            f"expected {expected_cell_count} to match the table header."
        )
    values = tuple(
        normalize_whitespace(cells.nth(index).inner_text()) for index in range(5)
    )
    return values  # type: ignore[return-value]


def record_from_values(
    values: tuple[str, str, str, str, str], row_number: int
) -> QuarterlyEarningsRecord | None:
    quarter_label, eps, eps_change, sales, sales_change = values

    if not any(values):
        print(f"Skipped Quarterly Earnings row {row_number}: empty non-data row.")
        return None

    if not quarter_label:
        raise QuarterlyEarningsExtractionError(
            f"Quarterly Earnings row {row_number} contains financial data but has no "
            "quarter label."
        )

    unavailable = []
    for label, value in (
        ("EPS", eps),
        ("EPS % Chg", eps_change),
        ("Sales", sales),
        ("Sales % Chg", sales_change),
    ):
        normalized = value.casefold()
        if normalized in {"", "n/a", "na", "-"}:
            state = "blank" if value == "" else repr(value)
            unavailable.append(f"{label} is {state}")
    if unavailable:
        print(
            f"Unavailable data for quarter {quarter_label!r}: "
            + "; ".join(unavailable)
            + "."
        )

    return QuarterlyEarningsRecord(
        quarter_label=quarter_label,
        quarter_end_date=quarter_end_date(quarter_label),
        eps=eps,
        eps_change_percent=eps_change,
        sales=sales,
        sales_change_percent=sales_change,
    )


def extract_quarterly_earnings(page: Page) -> QuarterlyEarnings:
    """Return all complete Consolidated quarterly records in source order."""
    ensure_consolidated_mode(page)
    verify_consolidated_mode(page)

    candidate, table = locate_quarterly_table(page)
    validate_headers(table)
    expand_quarterly_history(page, candidate)

    # Reacquire after both the financial-mode refresh and Show More update.
    _, table = locate_quarterly_table(page)
    expected_cell_count = validate_headers(table)
    rows = table.locator("tbody tr")
    if rows.count() == 0:
        raise QuarterlyEarningsExtractionError(
            "Quarterly Earnings table contains no historical records."
        )

    records: list[QuarterlyEarningsRecord] = []
    for index in range(rows.count()):
        row_number = index + 1
        record = record_from_values(
            row_values(rows.nth(index), row_number, expected_cell_count), row_number
        )
        if record is not None:
            records.append(record)

    if len(records) < 3:
        raise QuarterlyEarningsExtractionError(
            "Quarterly Earnings extraction produced "
            f"{len(records)} complete record(s); at least 3 are required."
        )
    return QuarterlyEarnings(records=tuple(records))


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "quarterly_failure.png"
    html_path = LOG_DIR / "quarterly_failure.html"

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


def print_quarterly_earnings(quarterly: QuarterlyEarnings) -> None:
    def displayed_value(value: str) -> str:
        return "<blank>" if value == "" else value

    print("Quarterly Earnings:")
    for index, record in enumerate(quarterly.records, start=1):
        displayed_date = record.quarter_end_date or "None"
        print(f"{index}. Quarter label: {record.quarter_label}")
        print(f"   Quarter end date: {displayed_date}")
        print(f"   EPS: {displayed_value(record.eps)}")
        print(f"   EPS % Chg: {displayed_value(record.eps_change_percent)}")
        print(f"   Sales: {displayed_value(record.sales)}")
        print(f"   Sales % Chg: {displayed_value(record.sales_change_percent)}")


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
                quarterly = extract_quarterly_earnings(page)

                print(f"Symbol: {symbol}")
                print(f"Company: {company_name}")
                print(f"Page URL: {page.url}")
                print("Financial mode: Consolidated")
                print_quarterly_earnings(quarterly)
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                SummaryExtractionError,
                QuarterlyEarningsExtractionError,
                OSError,
            ) as error:
                print(f"Quarterly Earnings extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
