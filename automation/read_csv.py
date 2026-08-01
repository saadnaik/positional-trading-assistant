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
VALID_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&.\-]*$")


def normalized_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def latest_csv() -> Path:
    files = [path for path in INCOMING_DIR.glob("*.csv") if path.is_file()]
    if not files:
        raise ValueError(f"No CSV files found in {INCOMING_DIR}")
    return max(files, key=lambda path: path.stat().st_mtime)


def read_symbols(csv_path: Path) -> list[str]:
    if csv_path.stat().st_size == 0:
        raise ValueError(f"CSV is empty: {csv_path}")

    try:
        frame = pd.read_csv(csv_path, dtype=str)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as error:
        raise ValueError(f"Could not parse {csv_path.name}: {error}") from error

    if frame.empty:
        raise ValueError(f"CSV contains headers but no rows: {csv_path}")
    if frame.columns.empty or any(normalized_header(column).startswith("unnamed") for column in frame.columns):
        raise ValueError("CSV has missing or unnamed column headers.")

    symbol_column = next(
        (column for column in frame.columns if normalized_header(column) in SYMBOL_HEADERS),
        None,
    )
    if symbol_column is None:
        available = ", ".join(map(str, frame.columns))
        raise ValueError(f"No symbol column found. Available columns: {available}")

    values = frame[symbol_column].dropna().astype(str).str.strip().str.upper()
    symbols = list(dict.fromkeys(value for value in values if value))
    if not symbols:
        raise ValueError(f"Symbol column {symbol_column!r} contains no symbols.")

    invalid = [symbol for symbol in symbols if not VALID_SYMBOL.fullmatch(symbol)]
    if invalid:
        preview = ", ".join(invalid[:5])
        raise ValueError(f"Invalid symbol value(s) found: {preview}")
    return symbols


def main() -> int:
    try:
        csv_path = latest_csv()
        symbols = read_symbols(csv_path)
    except (ValueError, OSError) as error:
        print(f"CSV validation failed: {error}")
        return 1

    print(f"Validated {csv_path.name}: {len(symbols)} unique symbols")
    for symbol in symbols:
        print(symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
