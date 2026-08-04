"""Strictly parse and validate MarketSmith India screen-export CSV files."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import warnings


EXPECTED_HEADERS = (
    "Sno",
    "Symbol",
    "CompanyName",
    "Cur_Price",
    "Price_Change",
    "Price_Percentage_chg",
    "Master_Score",
    "EPS_Rating",
    "RS_Rating",
    "AD_Rating",
    "Group_Rank",
)


class MarketSmithCsvError(ValueError):
    """Raised when a MarketSmith CSV does not match the required format."""


class DuplicateSymbolWarning(UserWarning):
    """Reported when a duplicate symbol/company row is ignored."""


@dataclass(frozen=True)
class MarketSmithCsvRow:
    sno: str
    symbol: str
    company_name: str
    current_price: str
    price_change: str
    price_percentage_change: str
    master_score: str
    eps_rating: str
    rs_rating: str
    ad_rating: str
    group_rank: str


def _logical_data_row(path: Path, row: list[str], row_number: int) -> list[str]:
    field_count = len(row)
    if field_count == len(EXPECTED_HEADERS):
        return row
    if field_count == len(EXPECTED_HEADERS) + 1 and not row[-1].strip():
        return row[:-1]

    if field_count < len(EXPECTED_HEADERS):
        detail = f"missing fields: expected 11, found {field_count}"
    elif field_count == len(EXPECTED_HEADERS) + 1:
        detail = "extra final field must be empty"
    else:
        detail = f"too many fields: expected 11, found {field_count}"
    raise MarketSmithCsvError(f"{path.name}: row {row_number}: {detail}")


def read_marketsmith_csv(path: Path) -> tuple[MarketSmithCsvRow, ...]:
    """Read one MarketSmith export without inferring or repairing its structure."""

    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise MarketSmithCsvError(f"{path.name}: CSV is not valid UTF-8") from error

    if not content.strip():
        raise MarketSmithCsvError(f"CSV is empty: {path}")

    prefix = content.lstrip().lower()
    if prefix.startswith("<!doctype html") or prefix.startswith("<html"):
        raise MarketSmithCsvError(f"Expected CSV but received HTML: {path}")

    try:
        csv_rows = csv.reader(StringIO(content, newline=""), strict=True)
        header = next(csv_rows)
        normalized_header = tuple(value.strip() for value in header)

        duplicate_headers = sorted(
            {value for value in normalized_header if normalized_header.count(value) > 1}
        )
        if duplicate_headers:
            names = ", ".join(repr(value) for value in duplicate_headers)
            raise MarketSmithCsvError(f"{path.name}: duplicate header(s): {names}")
        if normalized_header != EXPECTED_HEADERS:
            raise MarketSmithCsvError(
                f"{path.name}: unexpected headers: {normalized_header!r}; "
                f"expected {EXPECTED_HEADERS!r}"
            )

        records: list[MarketSmithCsvRow] = []
        seen: dict[str, tuple[str, int]] = {}
        for row_number, physical_row in enumerate(csv_rows, start=2):
            fields = [
                value.strip()
                for value in _logical_data_row(path, physical_row, row_number)
            ]
            fields[1] = fields[1].upper()
            symbol = fields[1]
            company_name = fields[2]

            if not symbol:
                raise MarketSmithCsvError(
                    f"{path.name}: row {row_number}: Symbol may not be empty"
                )
            if not company_name:
                raise MarketSmithCsvError(
                    f"{path.name}: row {row_number}: CompanyName may not be empty"
                )

            previous = seen.get(symbol)
            if previous is not None:
                previous_company, previous_row = previous
                if company_name != previous_company:
                    raise MarketSmithCsvError(
                        f"{path.name}: row {row_number}: conflicting company names "
                        f"for symbol {symbol}: {previous_company!r} at row "
                        f"{previous_row} != {company_name!r}"
                    )
                warnings.warn(
                    f"{path.name}: row {row_number}: duplicate symbol {symbol} "
                    f"with company {company_name!r}; keeping first occurrence "
                    f"from row {previous_row}",
                    DuplicateSymbolWarning,
                    stacklevel=2,
                )
                continue

            seen[symbol] = (company_name, row_number)
            records.append(MarketSmithCsvRow(*fields))
    except csv.Error as error:
        raise MarketSmithCsvError(f"{path.name}: malformed CSV: {error}") from error

    if not records:
        raise MarketSmithCsvError(f"CSV contains headers but no data rows: {path}")
    return tuple(records)


def read_symbols(path: Path) -> tuple[tuple[str, str], ...]:
    """Return normalized symbols and company names in source-file order."""

    return tuple(
        (row.symbol, row.company_name) for row in read_marketsmith_csv(path)
    )


def latest_csv(directory: Path) -> Path:
    """Return the newest CSV directly inside an explicitly supplied directory."""

    directory = Path(directory)
    if not directory.is_dir():
        raise MarketSmithCsvError(f"CSV directory does not exist: {directory}")
    files = tuple(path for path in directory.glob("*.csv") if path.is_file())
    if not files:
        raise MarketSmithCsvError(f"No CSV files found in {directory}")
    return max(files, key=lambda path: path.stat().st_mtime)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("csv_path", nargs="?", type=Path, help="CSV file to validate")
    source.add_argument(
        "--latest-from",
        type=Path,
        metavar="DIRECTORY",
        help="validate the newest CSV in this explicit directory",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        csv_path = latest_csv(args.latest_from) if args.latest_from else args.csv_path
        records = read_marketsmith_csv(csv_path)
    except (MarketSmithCsvError, OSError) as error:
        print(f"CSV validation failed: {error}")
        return 1

    print(f"Validated: {csv_path.name}")
    print(f"Rows: {len(records)}")
    print()
    for row in records:
        print(f"{row.symbol:<12} {row.company_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
