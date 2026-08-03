"""Extract all currently supported MarketSmith data for one stock."""

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from automation.read_csv import latest_csv, read_symbols
from extract.annual_eps import (
    AnnualEpsExtractionError,
    AnnualEpsHistory,
    extract_annual_eps,
    print_annual_eps,
)
from extract.annual_ratios import (
    AnnualRatios,
    AnnualRatiosExtractionError,
    extract_annual_ratios,
    print_annual_ratios,
)
from extract.quarterly import (
    QuarterlyEarnings,
    QuarterlyEarningsExtractionError,
    extract_quarterly_earnings,
    print_quarterly_earnings,
)
from extract.json_output import JsonOutputError, write_stock_json
from extract.summary import (
    AUTH_STATE,
    EVALUATION_URL,
    SummaryExtractionError,
    SummaryMetrics,
    confirm_stock_page,
    extract_summary_metrics,
    pause_before_close,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"


@dataclass(frozen=True)
class ExtractedStockData:
    symbol: str
    company: str
    page_url: str
    financial_mode: str
    summary: SummaryMetrics
    quarterly: QuarterlyEarnings
    annual_eps: AnnualEpsHistory
    annual_ratios: AnnualRatios


def extract_stock_data(
    page: Page, symbol: str, company: str
) -> ExtractedStockData:
    """Run every existing extractor against one already verified stock page."""
    summary = extract_summary_metrics(page)
    quarterly = extract_quarterly_earnings(page)
    annual_eps = extract_annual_eps(page)
    annual_ratios = extract_annual_ratios(page)

    return ExtractedStockData(
        symbol=symbol,
        company=company,
        page_url=page.url,
        financial_mode="Consolidated",
        summary=summary,
        quarterly=quarterly,
        annual_eps=annual_eps,
        annual_ratios=annual_ratios,
    )


def save_failure_diagnostics(page: Page) -> None:
    screenshot_path = LOG_DIR / "stock_failure.png"
    html_path = LOG_DIR / "stock_failure.html"

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


def print_stock_data(stock: ExtractedStockData) -> None:
    print(f"Symbol: {stock.symbol}")
    print(f"Company: {stock.company}")
    print(f"Page URL: {stock.page_url}")
    print(f"Financial mode: {stock.financial_mode}")
    print("Summary Metrics:")
    print(f"EPS Growth Rate: {stock.summary.eps_growth_rate}")
    print(f"Earnings Stability: {stock.summary.earnings_stability}")
    print(f"Return on Equity: {stock.summary.return_on_equity}")
    print_quarterly_earnings(stock.quarterly)
    print_annual_eps(stock.annual_eps)
    print_annual_ratios(stock.annual_ratios)


def main() -> int:
    if not AUTH_STATE.is_file():
        print(f"Authentication state not found: {AUTH_STATE}")
        print("Run automation/login.py and complete manual login first.")
        return 1

    try:
        csv_path = latest_csv()
        stocks = read_symbols(csv_path)
        symbol, company = stocks[0]
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
                confirm_stock_page(page, symbol, company)
                stock = extract_stock_data(page, symbol, company)
                output_path = write_stock_json(stock)

                print_stock_data(stock)
                print(f"JSON output: {output_path}")
                pause_before_close()
                browser.close()
                return 0
            except (
                PlaywrightTimeoutError,
                PlaywrightError,
                SummaryExtractionError,
                QuarterlyEarningsExtractionError,
                AnnualEpsExtractionError,
                AnnualRatiosExtractionError,
                JsonOutputError,
                OSError,
            ) as error:
                print(f"Stock extraction failed: {error}")
                save_failure_diagnostics(page)
                pause_before_close()
                browser.close()
                return 1
    except PlaywrightError as error:
        print(f"Could not start MarketSmith browser automation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
