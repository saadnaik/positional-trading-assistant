from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from extract.json_output import stock_to_json_document
from extract.summary import SummaryExtractionError, extract_summary_metrics, extract_value


class SummaryUnavailableValueTests(unittest.TestCase):
    def extracted(self, values: tuple[str, str, str]):
        page = Mock()
        with patch("extract.summary.ensure_consolidated_mode"), patch(
            "extract.summary.verify_consolidated_mode"
        ), patch("extract.summary.extract_value", side_effect=values):
            return extract_summary_metrics(page)

    def test_present_malformed_summary_values_are_preserved(self) -> None:
        metrics = self.extracted(("1.55", "not numeric", "abc"))
        self.assertEqual(metrics.eps_growth_rate, "1.55")
        self.assertEqual(metrics.earnings_stability, "not numeric")
        self.assertEqual(metrics.return_on_equity, "abc")

    def test_unavailable_and_valid_values_are_preserved(self) -> None:
        for eps, stability, roe in (
            ("", "N/A", "NA"),
            ("-", "25", "+17%"),
        ):
            with self.subTest(values=(eps, stability, roe)):
                metrics = self.extracted((eps, stability, roe))
                self.assertEqual(
                    (metrics.eps_growth_rate, metrics.earnings_stability, metrics.return_on_equity),
                    (eps, stability, roe),
                )

    def test_missing_expected_label_or_value_still_fails(self) -> None:
        summary = Mock()
        with patch("extract.summary.desktop_value", return_value=None), patch(
            "extract.summary.alternate_value", return_value=None
        ):
            with self.assertRaisesRegex(SummaryExtractionError, "Could not find visible summary field"):
                extract_value(summary, "EPS Growth Rate")

    def test_structural_container_failure_is_not_suppressed(self) -> None:
        page = Mock()
        structural_error = RuntimeError("summary container missing")
        page.locator.return_value.wait_for.side_effect = structural_error
        with patch("extract.summary.ensure_consolidated_mode"), patch(
            "extract.summary.verify_consolidated_mode"
        ):
            with self.assertRaisesRegex(RuntimeError, "summary container missing"):
                extract_summary_metrics(page)

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

    def test_json_serialization_preserves_malformed_summary_strings(self) -> None:
        stock = SimpleNamespace(
            symbol="RAW",
            company="Raw Values Ltd",
            page_url="https://example.test/RAW",
            financial_mode="Consolidated",
            summary=SimpleNamespace(
                eps_growth_rate="1.55",
                earnings_stability="not numeric",
                return_on_equity="abc",
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
                "eps_growth_rate": "1.55",
                "earnings_stability": "not numeric",
                "return_on_equity": "abc",
            },
        )


if __name__ == "__main__":
    unittest.main()
