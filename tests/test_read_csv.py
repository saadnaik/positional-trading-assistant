from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import warnings

from automation.read_csv import (
    DuplicateSymbolWarning,
    EXPECTED_HEADERS,
    MarketSmithCsvError,
    read_marketsmith_csv,
    read_symbols,
)


class MarketSmithCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)

    def write_rows(self, rows: list[list[str]], name: str = "screen.csv") -> Path:
        path = self.directory / name
        with path.open("w", encoding="utf-8", newline="") as output:
            csv.writer(output).writerows(rows)
        return path

    def valid_row(
        self,
        symbol: str = "IIFL",
        company_name: str = "Iifl Finance",
    ) -> list[str]:
        return [
            "1", symbol, company_name, "605.20", "-12.65", "-2.05",
            "81", "75", "86", "A-", "74",
        ]

    def test_valid_normal_eleven_field_csv(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row()])
        rows = read_marketsmith_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].current_price, "605.20")

    def test_valid_marketsmith_trailing_empty_field(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + [""]])
        self.assertEqual(read_marketsmith_csv(path)[0].group_rank, "74")

    def test_symbol_is_not_shifted_to_company_name(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + [""]])
        self.assertEqual(read_marketsmith_csv(path)[0].symbol, "IIFL")

    def test_company_name_is_not_shifted_to_price(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + [""]])
        self.assertEqual(read_marketsmith_csv(path)[0].company_name, "Iifl Finance")

    def test_multiple_rows_preserve_order(self) -> None:
        second = self.valid_row("ADVAIT", "Advait Energy Transitions Ltd")
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row(), second])
        self.assertEqual([row.symbol for row in read_marketsmith_csv(path)], ["IIFL", "ADVAIT"])

    def test_whitespace_only_trailing_field_is_accepted(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + ["  "]])
        self.assertEqual(len(read_marketsmith_csv(path)), 1)

    def test_nonempty_trailing_field_is_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + ["extra"]])
        with self.assertRaisesRegex(MarketSmithCsvError, "extra final field must be empty"):
            read_marketsmith_csv(path)

    def test_too_few_fields_are_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row()[:-1]])
        with self.assertRaisesRegex(MarketSmithCsvError, "missing fields"):
            read_marketsmith_csv(path)

    def test_too_many_fields_are_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + ["", ""]])
        with self.assertRaisesRegex(MarketSmithCsvError, "too many fields"):
            read_marketsmith_csv(path)

    def test_wrong_header_is_rejected(self) -> None:
        header = list(EXPECTED_HEADERS)
        header[1] = "Ticker"
        path = self.write_rows([header, self.valid_row()])
        with self.assertRaisesRegex(MarketSmithCsvError, "unexpected headers"):
            read_marketsmith_csv(path)

    def test_duplicate_header_is_rejected_specifically(self) -> None:
        header = list(EXPECTED_HEADERS)
        header[2] = "Symbol"
        path = self.write_rows([header, self.valid_row()])
        with self.assertRaisesRegex(MarketSmithCsvError, "duplicate header"):
            read_marketsmith_csv(path)

    def test_empty_symbol_is_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row("", "Iifl Finance")])
        with self.assertRaisesRegex(MarketSmithCsvError, "Symbol may not be empty"):
            read_marketsmith_csv(path)

    def test_empty_company_name_is_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row("IIFL", "")])
        with self.assertRaisesRegex(MarketSmithCsvError, "CompanyName may not be empty"):
            read_marketsmith_csv(path)

    def test_exact_duplicate_keeps_first_and_warns_with_rows(self) -> None:
        duplicate = self.valid_row(" iifl ", " Iifl Finance ")
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row(), duplicate])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rows = read_marketsmith_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(caught[0].category, DuplicateSymbolWarning)
        self.assertIn("row 3", str(caught[0].message))
        self.assertIn("row 2", str(caught[0].message))

    def test_conflicting_duplicate_company_fails(self) -> None:
        conflict = self.valid_row("iifl", "IIFL Finance Limited")
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row(), conflict])
        with self.assertRaisesRegex(
            MarketSmithCsvError,
            "row 3: conflicting company names for symbol IIFL.*Iifl Finance.*IIFL Finance Limited",
        ):
            read_marketsmith_csv(path)

    def test_html_file_is_rejected(self) -> None:
        path = self.directory / "response.csv"
        path.write_text("  <!DOCTYPE html><html></html>", encoding="utf-8")
        with self.assertRaisesRegex(MarketSmithCsvError, "received HTML"):
            read_marketsmith_csv(path)

    def test_empty_file_is_rejected(self) -> None:
        path = self.directory / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(MarketSmithCsvError, "CSV is empty"):
            read_marketsmith_csv(path)

    def test_build_your_screen_sample_schema(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS), self.valid_row() + [""]])
        row = read_marketsmith_csv(path)[0]
        self.assertEqual((row.symbol, row.company_name, row.group_rank), ("IIFL", "Iifl Finance", "74"))

    def test_minervini_sample_schema(self) -> None:
        row = ["1", "ADVAIT", "Advait Energy Transitions Ltd", "2310.60", "132.30", "6.07", "74", "96", "83", "B+", "66", ""]
        path = self.write_rows([list(EXPECTED_HEADERS), row])
        parsed = read_marketsmith_csv(path)[0]
        self.assertEqual((parsed.symbol, parsed.company_name, parsed.group_rank), ("ADVAIT", "Advait Energy Transitions Ltd", "66"))

    def test_read_symbols_returns_uppercase_symbols(self) -> None:
        row = self.valid_row(" iifl ", " Iifl Finance ")
        path = self.write_rows([list(EXPECTED_HEADERS), row])
        self.assertEqual(read_symbols(path), (("IIFL", "Iifl Finance"),))

    def test_header_only_csv_is_rejected(self) -> None:
        path = self.write_rows([list(EXPECTED_HEADERS)])
        with self.assertRaisesRegex(MarketSmithCsvError, "headers but no data rows"):
            read_marketsmith_csv(path)


if __name__ == "__main__":
    unittest.main()
