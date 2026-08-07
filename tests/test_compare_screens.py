from __future__ import annotations

import csv
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings

from automation.compare_screens import (
    CandidateSignal,
    CompanyNameMismatchWarning,
    SignalStatus,
    combine_minervini_statuses,
    _parse_args,
    compare_screens,
    main,
)
from automation.read_csv import (
    DuplicateSymbolWarning,
    EXPECTED_HEADERS,
    MarketSmithCsvError,
    read_symbols,
)


class CompareScreensTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)

    def row(self, sno: int, symbol: str, company_name: str) -> list[str]:
        return [
            str(sno), symbol, company_name, "100", "1", "1", "80", "80",
            "80", "A", "20",
        ]

    def write_csv(
        self,
        name: str,
        rows: list[list[str]],
        *,
        directory: Path | None = None,
    ) -> Path:
        target_directory = directory or self.directory
        target_directory.mkdir(parents=True, exist_ok=True)
        path = target_directory / name
        with path.open("w", encoding="utf-8", newline="") as output:
            csv.writer(output).writerows([list(EXPECTED_HEADERS), *rows])
        return path

    def test_overlap_is_yes_and_primary_only_is_no(self) -> None:
        primary = self.write_csv(
            "primary.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, "BBB", "Beta")],
        )
        minervini = self.write_csv(
            "minervini.csv", [self.row(1, "BBB", "Beta")]
        )

        candidates = compare_screens(primary, minervini)

        self.assertEqual(
            [candidate.minervini_1_month for candidate in candidates],
            [SignalStatus.NO, SignalStatus.YES],
        )

    def test_minervini_only_symbols_are_not_added(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        minervini = self.write_csv(
            "minervini.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, "ONLY", "Only Signal")],
        )
        self.assertEqual(
            compare_screens(primary, minervini),
            (CandidateSignal("AAA", "Alpha", SignalStatus.YES, SignalStatus.UNKNOWN, SignalStatus.YES),),
        )

    def test_missing_minervini_input_is_unknown(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        self.assertEqual(
            compare_screens(primary, None)[0].minervini_1_month,
            SignalStatus.UNKNOWN,
        )

    def test_all_minervini_status_combinations(self) -> None:
        cases = {
            (SignalStatus.YES, SignalStatus.YES): SignalStatus.YES,
            (SignalStatus.YES, SignalStatus.NO): SignalStatus.YES,
            (SignalStatus.NO, SignalStatus.YES): SignalStatus.YES,
            (SignalStatus.NO, SignalStatus.NO): SignalStatus.NO,
            (SignalStatus.YES, SignalStatus.UNKNOWN): SignalStatus.YES,
            (SignalStatus.UNKNOWN, SignalStatus.YES): SignalStatus.YES,
            (SignalStatus.NO, SignalStatus.UNKNOWN): SignalStatus.UNKNOWN,
            (SignalStatus.UNKNOWN, SignalStatus.NO): SignalStatus.UNKNOWN,
            (SignalStatus.UNKNOWN, SignalStatus.UNKNOWN): SignalStatus.UNKNOWN,
        }
        for inputs, expected in cases.items():
            with self.subTest(inputs=inputs):
                self.assertEqual(combine_minervini_statuses(*inputs), expected)

    def test_timeframe_memberships_are_independent(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "ONE", "One"), self.row(2, "FIVE", "Five")])
        one = self.write_csv("one.csv", [self.row(1, "ONE", "One"), self.row(2, "EXTRA", "Extra")])
        five = self.write_csv("five.csv", [self.row(1, "FIVE", "Five")])
        candidates = compare_screens(primary, one, five)
        self.assertEqual([c.symbol for c in candidates], ["ONE", "FIVE"])
        self.assertEqual(
            [(c.minervini_1_month, c.minervini_5_months, c.minervini_overall) for c in candidates],
            [(SignalStatus.YES, SignalStatus.NO, SignalStatus.YES), (SignalStatus.NO, SignalStatus.YES, SignalStatus.YES)],
        )

    def test_primary_order_is_preserved(self) -> None:
        primary = self.write_csv(
            "primary.csv",
            [
                self.row(1, "THIRD", "Third"),
                self.row(2, "FIRST", "First"),
                self.row(3, "SECOND", "Second"),
            ],
        )
        minervini = self.write_csv("minervini.csv", [self.row(1, "FIRST", "First")])
        self.assertEqual(
            [candidate.symbol for candidate in compare_screens(primary, minervini)],
            ["THIRD", "FIRST", "SECOND"],
        )

    def test_symbol_comparison_uses_strict_uppercase_normalization(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, " abc ", "Alpha")])
        minervini = self.write_csv("minervini.csv", [self.row(1, "AbC", "Alpha")])
        candidate = compare_screens(primary, minervini)[0]
        self.assertEqual((candidate.symbol, candidate.minervini_1_month), ("ABC", SignalStatus.YES))

    def test_valid_duplicate_behavior_is_inherited(self) -> None:
        primary = self.write_csv(
            "primary.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, " aaa ", " Alpha ")],
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            candidates = compare_screens(primary, None)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(
            any(item.category is DuplicateSymbolWarning for item in caught)
        )

    def test_conflicting_malformed_primary_fails(self) -> None:
        primary = self.write_csv(
            "primary.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, "AAA", "Different")],
        )
        with self.assertRaises(MarketSmithCsvError):
            compare_screens(primary, None)

    def test_conflicting_malformed_minervini_fails(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        minervini = self.write_csv(
            "minervini.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, "AAA", "Different")],
        )
        with self.assertRaises(MarketSmithCsvError):
            compare_screens(primary, minervini)

    def test_supplied_missing_minervini_file_fails(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        with self.assertRaises(FileNotFoundError):
            compare_screens(primary, self.directory / "missing.csv")

    def test_company_name_mismatch_warns_but_membership_is_yes(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha Industries")])
        minervini = self.write_csv(
            "minervini.csv", [self.row(1, "AAA", "Completely Different Holdings")]
        )
        with self.assertWarns(CompanyNameMismatchWarning):
            candidate = compare_screens(primary, minervini)[0]
        self.assertEqual(candidate.minervini_1_month, SignalStatus.YES)

    def test_cosmetic_company_name_differences_do_not_warn(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha Industries Limited")])
        minervini = self.write_csv(
            "minervini.csv", [self.row(1, "AAA", "alpha industries (NSE) ltd.")]
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            candidate = compare_screens(primary, minervini)[0]
        self.assertEqual(candidate.minervini_1_month, SignalStatus.YES)
        self.assertFalse(
            any(item.category is CompanyNameMismatchWarning for item in caught)
        )

    def test_empty_primary_list_fails_through_strict_parser(self) -> None:
        primary = self.write_csv("primary.csv", [])
        with self.assertRaisesRegex(MarketSmithCsvError, "headers but no data rows"):
            compare_screens(primary, None)

    def test_returned_count_always_equals_strict_primary_count(self) -> None:
        primary = self.write_csv(
            "primary.csv",
            [self.row(1, "AAA", "Alpha"), self.row(2, "BBB", "Beta")],
        )
        minervini = self.write_csv(
            "minervini.csv",
            [self.row(1, "BBB", "Beta"), self.row(2, "CCC", "Gamma")],
        )
        self.assertEqual(
            len(compare_screens(primary, minervini)), len(read_symbols(primary))
        )

    def test_explicit_path_cli(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        minervini = self.write_csv("minervini.csv", [self.row(1, "AAA", "Alpha")])
        output = StringIO()
        with patch("sys.stdout", output):
            status = main(["--primary", str(primary), "--minervini", str(minervini)])
        self.assertEqual(status, 0)
        self.assertIn("Primary candidates: 1", output.getvalue())
        self.assertIn("Minervini 1-Month symbols: 1", output.getvalue())
        self.assertIn("AAA          1M: YES 5M: UNKNOWN Overall: YES", output.getvalue())

    def test_latest_directory_cli(self) -> None:
        primary_directory = self.directory / "primary"
        minervini_directory = self.directory / "minervini"
        old_primary = self.write_csv("old.csv", [self.row(1, "OLD", "Old")], directory=primary_directory)
        new_primary = self.write_csv("new.csv", [self.row(1, "NEW", "New")], directory=primary_directory)
        self.write_csv("signal.csv", [self.row(1, "NEW", "New")], directory=minervini_directory)
        os.utime(old_primary, (1, 1))
        os.utime(new_primary, (2, 2))

        output = StringIO()
        with patch("sys.stdout", output):
            status = main(
                [
                    "--primary-latest-from", str(primary_directory),
                    "--minervini-latest-from", str(minervini_directory),
                ]
            )
        self.assertEqual(status, 0)
        self.assertIn("NEW          1M: YES 5M: UNKNOWN Overall: YES", output.getvalue())
        self.assertNotIn("OLD          1M:", output.getvalue())

    def test_no_minervini_cli_prints_unknown(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        output = StringIO()
        with patch("sys.stdout", output):
            status = main(["--primary", str(primary)])
        self.assertEqual(status, 0)
        self.assertIn("Minervini 1-Month symbols: UNKNOWN", output.getvalue())
        self.assertIn("Minervini 5-Month symbols: UNKNOWN", output.getvalue())
        self.assertIn("AAA          1M: UNKNOWN 5M: UNKNOWN Overall: UNKNOWN", output.getvalue())

    def test_malformed_supplied_minervini_cli_fails_clearly(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        malformed = self.directory / "malformed.csv"
        malformed.write_text("not,a,marketsmith,csv\n", encoding="utf-8")
        output = StringIO()
        with patch("sys.stdout", output):
            status = main(["--primary", str(primary), "--minervini", str(malformed)])
        self.assertEqual(status, 1)
        self.assertIn("Screen comparison failed:", output.getvalue())

    def test_no_shared_root_is_inferred_or_scanned(self) -> None:
        primary = self.write_csv("primary.csv", [self.row(1, "AAA", "Alpha")])
        with patch("automation.compare_screens.latest_csv") as mocked_latest:
            output = StringIO()
            with patch("sys.stdout", output):
                status = main(["--primary", str(primary)])
        self.assertEqual(status, 0)
        mocked_latest.assert_not_called()

    def test_primary_source_is_required_and_sources_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args([])
        with self.assertRaises(SystemExit):
            _parse_args(["--primary", "one.csv", "--primary-latest-from", "somewhere"])
        with self.assertRaises(SystemExit):
            _parse_args(["--primary", "one.csv", "--minervini", "two.csv", "--minervini-latest-from", "somewhere"])


if __name__ == "__main__":
    unittest.main()
