"""Extract annual EPS history from a MarketSmith evaluation page."""

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
UNAVAILABLE_VALUES = {"", "n/a", "na", "-"}
FOUR_DIGIT_YEAR = re.compile(r"^(\d{4})$")
FISCAL_YEAR = re.compile(r"^FY\s*(\d{2}|\d{4})$", re.IGNORECASE)
MARCH_YEAR = re.compile(r"^Mar[ -](\d{2}|\d{4})$", re.IGNORECASE)


@dataclass(frozen=True)
class AnnualEpsRecord:
    year_label: str
    fiscal_year_end: str | None
    eps: str
    high: str
    low: str


@dataclass(frozen=True)
class AnnualEpsHistory:
    records: tuple[AnnualEpsRecord, ...]


class AnnualEpsExtractionError(RuntimeError):
    """Raised when MarketSmith annual EPS data cannot be extracted safely."""


@dataclass(frozen=True)
class AnnualTable:
    table_selector: str
    expand_selector: str
    primary: bool


TABLE_CANDIDATES = (
    AnnualTable(
        table_selector=(
            "#details_placeholder_annualearnings "
            "> .industryGroup > table#fundamntlEarningTableId"
        ),
        expand_selector="#fundamntlEarningsTablePlus",
        primary=True,
    ),
    AnnualTable(
        table_selector=(
            "#slide-details_placeholder table#sldfundamntlEarningTableId"
        ),
        expand_selector="#sldfundamntlEarningsTablePlus",
        primary=False,
    ),
)


def normalize_year(year_text: str) -> int:
    year = int(year_text)
    return year + 2000 if len(year_text) == 2 else year


def fiscal_year_end(year_label: str, march_fiscal_table: bool) -> str | None:
    """Derive a March fiscal-year end only for a validated fiscal table."""
    if not march_fiscal_table:
        return None

    label = normalize_whitespace(year_label)
    for pattern in (FOUR_DIGIT_YEAR, FISCAL_YEAR, MARCH_YEAR):
        match = pattern.fullmatch(label)
        if match is not None:
            year = normalize_year(match.group(1))
            return f"{year:04d}-03-31"
    return None


def visible(locator: Locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except PlaywrightError:
        return False


def locate_annual_table(page: Page) -> tuple[AnnualTable, Locator]:
    for candidate in TABLE_CANDIDATES:
        table = page.locator(candidate.table_selector).first
        if visible(table):
            return candidate, table
    raise AnnualEpsExtractionError(
        "Could not find a visible MarketSmith annual EPS table."
    )


def normalized_headers(row: Locator) -> list[str]:
    return [
        normalize_whitespace(value).casefold()
        for value in row.locator(":scope > th").all_inner_texts()
    ]


def validate_headers(table: Locator) -> bool:
    header_rows = table.locator("thead > tr")
    if header_rows.count() != 2:
        raise AnnualEpsExtractionError(
            "Annual EPS table must contain exactly two header rows; found "
            f"{header_rows.count()}."
        )

    first_headers = normalized_headers(header_rows.nth(0))
    second_headers = normalized_headers(header_rows.nth(1))
    if first_headers != ["year", "eps", "price (inr)"]:
        raise AnnualEpsExtractionError(
            "Unexpected first Annual EPS header row: " + " | ".join(first_headers)
        )
    if second_headers != ["(mar)", "(inr)", "hi", "lo"]:
        raise AnnualEpsExtractionError(
            "Unexpected second Annual EPS header row: " + " | ".join(second_headers)
        )

    price_header = header_rows.nth(0).locator(":scope > th").nth(2)
    if price_header.get_attribute("colspan") != "2":
        raise AnnualEpsExtractionError(
            "Annual EPS Price (INR) header does not span the expected Hi and Lo columns."
        )
    return True


def expand_fallback_history(
    page: Page, candidate: AnnualTable, table: Locator
) -> None:
    if candidate.primary:
        return

    expand = page.locator(candidate.expand_selector).first
    if not visible(expand):
        return

    expand.click()
    try:
        expect(expand).to_be_hidden(timeout=10_000)
    except AssertionError as error:
        raise AnnualEpsExtractionError(
            "Annual EPS Show More control did not complete expansion."
        ) from error

    # Expansion updates row styles in place. Reacquire the table and ensure the
    # final historical row is exposed before reading any values.
    _, refreshed_table = locate_annual_table(page)
    rows = refreshed_table.locator("tbody > tr")
    if rows.count() == 0:
        raise AnnualEpsExtractionError(
            "Annual EPS fallback table is empty after expansion."
        )
    try:
        expect(rows.last).to_be_visible(timeout=10_000)
    except AssertionError as error:
        raise AnnualEpsExtractionError(
            "Annual EPS fallback history did not become fully visible after expansion."
        ) from error


def report_unavailable(year_label: str, values: tuple[tuple[str, str], ...]) -> None:
    unavailable = []
    for field, value in values:
        if value.casefold() in UNAVAILABLE_VALUES:
            state = "blank" if value == "" else repr(value)
            unavailable.append(f"{field} is {state}")
    if unavailable:
        print(
            f"Unavailable data for year {year_label!r}: "
            + "; ".join(unavailable)
            + "."
        )


def extract_annual_eps(page: Page) -> AnnualEpsHistory:
    """Return all genuine Consolidated annual EPS records in source order."""
    ensure_consolidated_mode(page)
    verify_consolidated_mode(page)

    candidate, table = locate_annual_table(page)
    march_fiscal_table = validate_headers(table)
    expand_fallback_history(page, candidate, table)

    # Reacquire after any Consolidated refresh or fallback expansion.
    _, table = locate_annual_table(page)
    march_fiscal_table = validate_headers(table)
    rows = table.locator("tbody > tr")
    if rows.count() == 0:
        raise AnnualEpsExtractionError(
            "Annual EPS table contains no historical records."
        )

    records: list[AnnualEpsRecord] = []
    for index in range(rows.count()):
        row_number = index + 1
        cells = rows.nth(index).locator(":scope > td")
        if cells.count() != 4:
            raise AnnualEpsExtractionError(
                f"Annual EPS row {row_number} has {cells.count()} cells; expected 4."
            )

        year_label, eps, high, low = (
            normalize_whitespace(cells.nth(cell_index).inner_text())
            for cell_index in range(4)
        )
        if not any((year_label, eps, high, low)):
            print(f"Skipped Annual EPS row {row_number}: empty non-data row.")
            continue
        if not year_label:
            raise AnnualEpsExtractionError(
                f"Annual EPS row {row_number} contains financial data but has no year label."
            )

        report_unavailable(
            year_label, (("EPS", eps), ("Hi", high), ("Lo", low))
        )
        records.append(
            AnnualEpsRecord(
                year_label=year_label,
                fiscal_year_end=fiscal_year_end(
                    year_label, march_fiscal_table=march_fiscal_table
                ),
                eps=eps,
                high=high,
                low=low,
            )
        )

    if len(records) < 3:
        raise AnnualEpsExtractionError(
            f"Annual EPS extraction produced {len(records)} genuine record(s); "
            "at least 3 are required."
        )
    return AnnualEpsHistory(records=tuple(records))


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "annual_eps_failure.png"
    html_path = LOG_DIR / "annual_eps_failure.html"

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


def print_annual_eps(history: AnnualEpsHistory) -> None:
    def displayed_value(value: str) -> str:
        return "<blank>" if value == "" else value

    print("Annual EPS:")
    for index, record in enumerate(history.records, start=1):
        displayed_date = record.fiscal_year_end or "None"
        print(f"{index}. Year label: {record.year_label}")
        print(f"   Fiscal year end: {displayed_date}")
        print(f"   EPS: {displayed_value(record.eps)}")
        print(f"   Hi: {displayed_value(record.high)}")
        print(f"   Lo: {displayed_value(record.low)}")


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
                history = extract_annual_eps(page)

                print(f"Symbol: {symbol}")
                print(f"Company: {company_name}")
                print(f"Page URL: {page.url}")
                print("Financial mode: Consolidated")
                print_annual_eps(history)
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                SummaryExtractionError,
                AnnualEpsExtractionError,
                OSError,
            ) as error:
                print(f"Annual EPS extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
