from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from extract.json_output import stock_to_json_document
from extract.summary import validate_numeric, validate_percentage


class SummaryUnavailableValueTests(unittest.TestCase):
    def test_eps_growth_rate_accepts_and_preserves_unavailable_values(self) -> None:
        for raw in ("", "N/A", "NA", "-"):
            with self.subTest(raw=raw):
                self.assertEqual(validate_percentage("EPS Growth Rate", raw), raw)

    def test_earnings_stability_accepts_and_preserves_unavailable_values(self) -> None:
        for raw in ("", "N/A", "NA", "-"):
            with self.subTest(raw=raw):
                self.assertEqual(validate_numeric("Earnings Stability", raw), raw)

    def test_return_on_equity_accepts_and_preserves_unavailable_values(self) -> None:
        for raw in ("", "N/A", "NA", "-"):
            with self.subTest(raw=raw):
                self.assertEqual(validate_percentage("Return on Equity", raw), raw)

    def test_json_serialization_preserves_distinct_raw_summary_spellings(self) -> None:
        stock = SimpleNamespace(
            symbol="RAW",
            company="Raw Values Ltd",
            page_url="https://example.test/RAW",
            financial_mode="Consolidated",
            summary=SimpleNamespace(
                eps_growth_rate="N/A",
                earnings_stability="NA",
                return_on_equity="-",
            ),
            quarterly=SimpleNamespace(records=()),
            annual_eps=SimpleNamespace(records=()),
            annual_ratios=SimpleNamespace(records=()),
        )
        document = stock_to_json_document(
            stock, datetime(2026, 8, 7, tzinfo=timezone.utc)
        )
        self.assertEqual(
            document["summary"],
            {
                "eps_growth_rate": "N/A",
                "earnings_stability": "NA",
                "return_on_equity": "-",
            },
        )


if __name__ == "__main__":
    unittest.main()
