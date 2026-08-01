"""Validate the latest incoming MarketSmith CSV and print unique symbols."""

from pathlib import Path
import re

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INCOMING_DIR = PROJECT_ROOT / "data" / "incoming"
SYMBOL_HEADERS = {
    "symbol",
    "stocksymbol",
    "nsesymbol",
    "ticker",
    "tickersymbol",
}
COMPANY_NAME_HEADERS = {"companyname"}
VALID_SYMBOL = re.compile(r"^(?=.*[A-Z])[A-Z0-9][A-Z0-9&.\-]*$")


def normalized_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def latest_csv() -> Path:
    files = [path for path in INCOMING_DIR.glob("*.csv") if path.is_file()]
    if not files:
        raise ValueError(f"No CSV files found in {INCOMING_DIR}")
    return max(files, key=lambda path: path.stat().st_mtime)


def read_symbols(csv_path: Path) -> list[tuple[str, str]]:
    if csv_path.stat().st_size == 0:
        raise ValueError(f"CSV is empty: {csv_path}")

    prefix = csv_path.read_bytes()[:4096].lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise ValueError(f"Expected CSV but received an HTML file: {csv_path}")

    try:
        frame = pd.read_csv(csv_path, dtype=str, index_col=False)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as error:
        raise ValueError(f"Could not parse {csv_path.name}: {error}") from error

    if frame.empty:
        raise ValueError(f"CSV contains headers but no rows: {csv_path}")

    unnamed_columns = [
        column
        for column in frame.columns
        if normalized_header(column).startswith("unnamed")
    ]
    for column in unnamed_columns:
        values = frame[column].dropna().astype(str).str.strip()
        if not values.empty and values.ne("").any():
            raise ValueError(f"Unnamed column {column!r} contains unexpected data.")
    frame = frame.drop(columns=unnamed_columns)

    if frame.columns.empty:
        raise ValueError("CSV has missing or unnamed column headers.")

    symbol_column = next(
        (column for column in frame.columns if normalized_header(column) in SYMBOL_HEADERS),
        None,
    )
    if symbol_column is None:
        available = ", ".join(map(str, frame.columns))
        raise ValueError(f"No symbol column found. Available columns: {available}")

    company_name_column = next(
        (
            column
            for column in frame.columns
            if normalized_header(column) in COMPANY_NAME_HEADERS
        ),
        None,
    )
    if company_name_column is None:
        available = ", ".join(map(str, frame.columns))
        raise ValueError(f"No CompanyName column found. Available columns: {available}")

    records: list[tuple[str, str]] = []
    seen_symbols: set[str] = set()
    for symbol_value, company_value in zip(
        frame[symbol_column], frame[company_name_column], strict=True
    ):
        symbol = "" if pd.isna(symbol_value) else str(symbol_value).strip().upper()
        company_name = "" if pd.isna(company_value) else str(company_value).strip()
        if not symbol:
            continue
        if not company_name:
            raise ValueError(f"CompanyName is empty for symbol {symbol!r}.")
        if symbol not in seen_symbols:
            records.append((symbol, company_name))
            seen_symbols.add(symbol)

    if not records:
        raise ValueError(f"Symbol column {symbol_column!r} contains no symbols.")

    invalid = [
        symbol for symbol, _ in records if not VALID_SYMBOL.fullmatch(symbol)
    ]
    if invalid:
        preview = ", ".join(invalid[:5])
        raise ValueError(f"Invalid symbol value(s) found: {preview}")
    return records


def main() -> int:
    try:
        csv_path = latest_csv()
        records = read_symbols(csv_path)
    except (ValueError, OSError) as error:
        print(f"CSV validation failed: {error}")
        return 1

    print(f"Validated {csv_path.name}: {len(records)} unique symbols")
    for symbol, company_name in records:
        print(f"{symbol}\t{company_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
