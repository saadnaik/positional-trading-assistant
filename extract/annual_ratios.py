"""Extract annual After Tax Margin history from MarketSmith Key Ratios."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright

from automation.read_csv import latest_csv, read_symbols
from extract.summary import (
    AUTH_STATE,
    EVALUATION_URL,
    SummaryExtractionError,
    confirm_stock_page,
    normalize_whitespace,
    pause_before_close,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
RATIOS_SECTION_SELECTOR = "#moreRatios"
ANNUAL_RATIOS_SELECTOR = "#annualRatiosDiv"
RATIOS_TABLE_SELECTOR = f"{ANNUAL_RATIOS_SELECTOR} table#keyRatioTable"
RATIOS_BODY_SELECTOR = "tbody#keyRatioTableDesk"
CONSOLIDATED_SELECTOR = "#consolidatedRatios"
STANDALONE_SELECTOR = "#standaloneRatios"
AFTER_TAX_MARGIN_LABEL = "After Tax Margin (%)"
UNAVAILABLE_VALUES = {"", "n/a", "na", "-"}
FOUR_DIGIT_YEAR = re.compile(r"^(\d{4})$")
FISCAL_YEAR = re.compile(r"^FY\s*(\d{2}|\d{4})$", re.IGNORECASE)
MARCH_YEAR = re.compile(r"^Mar[ -](\d{2}|\d{4})$", re.IGNORECASE)


@dataclass(frozen=True)
class AnnualRatioRecord:
    year_label: str
    fiscal_year_end: str | None
    after_tax_margin_percent: str


@dataclass(frozen=True)
class AnnualRatios:
    records: tuple[AnnualRatioRecord, ...]


class AnnualRatiosExtractionError(RuntimeError):
    """Raised when Annual Key Ratios cannot be extracted safely."""


def normalized_casefold(value: str) -> str:
    return normalize_whitespace(value).casefold()


def visible(locator: Locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except PlaywrightError:
        return False


def locate_ratios_section(page: Page) -> tuple[Locator, Locator]:
    section = page.locator(RATIOS_SECTION_SELECTOR).first
    try:
        section.wait_for(state="visible", timeout=60_000)
    except PlaywrightTimeoutError as error:
        raise AnnualRatiosExtractionError(
            "Could not find the visible MarketSmith Key Ratios section."
        ) from error

    headings = section.locator("h2")
    matching_headings = [
        headings.nth(index)
        for index in range(headings.count())
        if visible(headings.nth(index))
        and normalize_whitespace(headings.nth(index).inner_text()) == "Key Ratios"
    ]
    if len(matching_headings) != 1:
        raise AnnualRatiosExtractionError(
            "Key Ratios section must contain exactly one visible 'Key Ratios' heading; "
            f"found {len(matching_headings)}."
        )

    annual = section.locator(ANNUAL_RATIOS_SELECTOR).first
    if not visible(annual):
        raise AnnualRatiosExtractionError(
            "Could not find the visible Annual subsection in Key Ratios."
        )
    annual_headings = annual.locator(":scope > h3")
    if annual_headings.count() != 1 or not visible(annual_headings.first):
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios must contain exactly one visible Annual heading."
        )
    annual_heading = normalize_whitespace(annual_headings.first.inner_text())
    if annual_heading != "Annual":
        raise AnnualRatiosExtractionError(
            f"Unexpected Annual Key Ratios heading: {annual_heading!r}."
        )

    table = section.locator(RATIOS_TABLE_SELECTOR).first
    if table.count() != 1:
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios table #keyRatioTable was not found uniquely."
        )
    scroll_wrapper = table.locator("xpath=parent::*[contains(concat(' ', "
                                   "normalize-space(@class), ' '), "
                                   "' scroll-Ratio-Table ')]")
    if scroll_wrapper.count() != 1 or not visible(scroll_wrapper.first):
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios table does not have its expected visible scroll wrapper."
        )
    return section, table


def ratios_mode_controls(page: Page) -> tuple[Locator, Locator]:
    section, _ = locate_ratios_section(page)
    consolidated = section.locator(CONSOLIDATED_SELECTOR).first
    standalone = section.locator(STANDALONE_SELECTOR).first
    if consolidated.count() != 1 or standalone.count() != 1:
        raise AnnualRatiosExtractionError(
            "Could not find unique Consolidated and Standalone Key Ratios controls."
        )
    return consolidated, standalone


def verify_consolidated_ratios_mode(page: Page) -> None:
    consolidated, standalone = ratios_mode_controls(page)
    try:
        expect(consolidated).to_be_checked(timeout=60_000)
        expect(standalone).not_to_be_checked(timeout=60_000)
    except AssertionError as error:
        raise AnnualRatiosExtractionError(
            "Could not verify Consolidated Key Ratios mode; refusing to extract data."
        ) from error
    if not consolidated.is_checked() or standalone.is_checked():
        raise AnnualRatiosExtractionError(
            "Could not verify Consolidated Key Ratios mode; refusing to extract data."
        )


def is_consolidated_finance_response(url: str) -> bool:
    parsed = urlsplit(url)
    if not parsed.path.endswith("/financeDetails.json"):
        return False
    return parse_qs(parsed.query).get("isConsolidated") == ["true"]


def ensure_consolidated_ratios_mode(page: Page) -> None:
    consolidated, standalone = ratios_mode_controls(page)
    if consolidated.is_checked() and not standalone.is_checked():
        verify_consolidated_ratios_mode(page)
        return

    _, table = locate_ratios_section(page)
    old_body = table.locator(RATIOS_BODY_SELECTOR).first
    if old_body.count() != 1:
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios table body #keyRatioTableDesk was not found uniquely."
        )
    old_body_handle = old_body.element_handle()
    if old_body_handle is None:
        raise AnnualRatiosExtractionError(
            "Could not retain the current Annual Key Ratios table for refresh validation."
        )

    label = page.locator(
        f'{RATIOS_SECTION_SELECTOR} label[for="consolidatedRatios"]:visible'
    )
    if label.count() != 1:
        raise AnnualRatiosExtractionError(
            "Could not find the unique visible Consolidated Key Ratios label."
        )

    try:
        with page.expect_response(
            lambda response: is_consolidated_finance_response(response.url),
            timeout=60_000,
        ) as response_info:
            label.click()
        response_info.value.body()
        page.wait_for_function(
            "body => !body.isConnected", arg=old_body_handle, timeout=60_000
        )
    except PlaywrightTimeoutError as error:
        raise AnnualRatiosExtractionError(
            "Timed out waiting for the Consolidated financeDetails.json refresh "
            "to replace Annual Key Ratios."
        ) from error

    # MarketSmith replaces the ratios markup after the response. Reacquire all
    # controls and data locators before verifying or reading any values.
    locate_ratios_section(page)
    verify_consolidated_ratios_mode(page)


def normalized_year_number(label: str) -> int | None:
    normalized = normalize_whitespace(label)
    for pattern in (FOUR_DIGIT_YEAR, FISCAL_YEAR, MARCH_YEAR):
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        displayed_year = match.group(1)
        year = int(displayed_year)
        return year + 2000 if len(displayed_year) == 2 else year
    return None


def validate_ratio_headers(table: Locator) -> list[str]:
    header_rows = table.locator("thead > tr")
    if header_rows.count() != 1:
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios table must contain exactly one header row; found "
            f"{header_rows.count()}."
        )

    cells = header_rows.first.locator(":scope > th")
    headers = [
        normalize_whitespace(cells.nth(index).inner_text())
        for index in range(cells.count())
    ]
    if len(headers) < 4:
        raise AnnualRatiosExtractionError(
            f"Annual Key Ratios has {len(headers)} headers; expected at least 4."
        )
    if headers[0] != "Annual":
        raise AnnualRatiosExtractionError(
            f"Unexpected first Annual Key Ratios header: {headers[0]!r}."
        )

    year_labels = headers[1:]
    if any(not label for label in year_labels):
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios contains an empty year header."
        )
    if len(set(year_labels)) != len(year_labels):
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios contains duplicate year labels."
        )
    if any(normalized_year_number(label) is None for label in year_labels):
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios contains an unsupported year label: "
            + " | ".join(year_labels)
        )
    return year_labels


def march_fiscal_years_confirmed(page: Page, year_labels: list[str]) -> bool:
    ratio_years = [normalized_year_number(label) for label in year_labels]
    if any(year is None for year in ratio_years):
        return False

    for selector in ("#incomestatementTable", "#balancesheetTable"):
        table = page.locator(selector).first
        if table.count() != 1:
            continue
        rows = table.locator("thead > tr")
        if rows.count() != 1:
            continue
        cells = rows.first.locator(":scope > th")
        if cells.count() != len(year_labels) + 1:
            continue
        first_header = normalize_whitespace(cells.first.inner_text())
        if not first_header.casefold().startswith("annual"):
            continue
        statement_labels = [
            normalize_whitespace(cells.nth(index).inner_text())
            for index in range(1, cells.count())
        ]
        if not all(MARCH_YEAR.fullmatch(label) for label in statement_labels):
            continue
        statement_years = [
            normalized_year_number(label) for label in statement_labels
        ]
        if statement_years == ratio_years:
            return True
    return False


def locate_after_tax_margin_row(table: Locator, expected_cells: int) -> Locator:
    body = table.locator(RATIOS_BODY_SELECTOR)
    if body.count() != 1:
        raise AnnualRatiosExtractionError(
            "Annual Key Ratios table must contain exactly one #keyRatioTableDesk body."
        )

    matching_rows: list[Locator] = []
    rows = body.locator(":scope > tr")
    for index in range(rows.count()):
        row = rows.nth(index)
        cells = row.locator(":scope > td")
        if cells.count() == 0:
            continue
        label = normalize_whitespace(cells.first.inner_text())
        if label == AFTER_TAX_MARGIN_LABEL:
            matching_rows.append(row)

    if len(matching_rows) != 1:
        raise AnnualRatiosExtractionError(
            f"Expected exactly one {AFTER_TAX_MARGIN_LABEL!r} row in Annual Key "
            f"Ratios; found {len(matching_rows)}."
        )

    row = matching_rows[0]
    actual_cells = row.locator(":scope > td").count()
    if actual_cells != expected_cells:
        raise AnnualRatiosExtractionError(
            f"Annual {AFTER_TAX_MARGIN_LABEL!r} row has {actual_cells} cells; "
            f"expected {expected_cells} to match the header."
        )
    return row


def extract_annual_ratios(page: Page) -> AnnualRatios:
    """Return all genuine Consolidated annual margin records in source order."""
    ensure_consolidated_ratios_mode(page)
    verify_consolidated_ratios_mode(page)

    # Reacquire after the Consolidated refresh.
    _, table = locate_ratios_section(page)
    year_labels = validate_ratio_headers(table)
    march_fiscal_table = march_fiscal_years_confirmed(page, year_labels)
    row = locate_after_tax_margin_row(table, expected_cells=len(year_labels) + 1)
    cells = row.locator(":scope > td")

    records: list[AnnualRatioRecord] = []
    for index, year_label in enumerate(year_labels, start=1):
        value = normalize_whitespace(cells.nth(index).inner_text())
        if value.casefold() in UNAVAILABLE_VALUES:
            state = "blank" if value == "" else repr(value)
            print(
                f"Unavailable data for year {year_label!r}: "
                f"After Tax Margin % is {state}."
            )

        year = normalized_year_number(year_label)
        fiscal_end = (
            f"{year:04d}-03-31"
            if march_fiscal_table and year is not None
            else None
        )
        records.append(
            AnnualRatioRecord(
                year_label=year_label,
                fiscal_year_end=fiscal_end,
                after_tax_margin_percent=value,
            )
        )

    if len(records) < 3:
        raise AnnualRatiosExtractionError(
            f"Annual After Tax Margin extraction produced {len(records)} genuine "
            "record(s); at least 3 are required."
        )
    return AnnualRatios(records=tuple(records))


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "annual_ratios_failure.png"
    html_path = LOG_DIR / "annual_ratios_failure.html"

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


def print_annual_ratios(ratios: AnnualRatios) -> None:
    print("Annual After Tax Margin:")
    for index, record in enumerate(ratios.records, start=1):
        displayed_date = record.fiscal_year_end or "None"
        displayed_margin = (
            "<blank>"
            if record.after_tax_margin_percent == ""
            else record.after_tax_margin_percent
        )
        print(f"{index}. Year label: {record.year_label}")
        print(f"   Fiscal year end: {displayed_date}")
        print(f"   After Tax Margin %: {displayed_margin}")


def main() -> int:
    if not AUTH_STATE.is_file():
        print(f"Authentication state not found: {AUTH_STATE}")
        print("Run automation/login.py and complete manual login first.")
        return 1

    try:
        csv_path = latest_csv()
        stocks = read_symbols(csv_path)
        symbol, company_name = stocks[0]
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
                ratios = extract_annual_ratios(page)

                print(f"Symbol: {symbol}")
                print(f"Company: {company_name}")
                print(f"Page URL: {page.url}")
                print("Financial mode: Consolidated")
                print_annual_ratios(ratios)
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                SummaryExtractionError,
                AnnualRatiosExtractionError,
                OSError,
            ) as error:
                print(f"Annual ratios extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
