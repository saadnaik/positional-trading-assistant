"""Attach an independent Minervini signal to primary MarketSmith candidates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
import re
import warnings

from automation.read_csv import MarketSmithCsvError, latest_csv, read_symbols


class SignalStatus(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CandidateSignal:
    symbol: str
    company_name: str
    minervini_1_month: SignalStatus


class CompanyNameMismatchWarning(UserWarning):
    """Reported when one symbol has materially different company names."""


_ROUTINE_COMPANY_TOKENS = frozenset({"nse", "ltd", "limited"})


def _normalized_company_name(company_name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", company_name.casefold()).split()
    return " ".join(word for word in words if word not in _ROUTINE_COMPANY_TOKENS)


def _company_names_substantially_different(left: str, right: str) -> bool:
    normalized_left = _normalized_company_name(left)
    normalized_right = _normalized_company_name(right)
    if normalized_left == normalized_right:
        return False
    return SequenceMatcher(None, normalized_left, normalized_right).ratio() < 0.6


def _compare_symbol_rows(
    primary_symbols: tuple[tuple[str, str], ...],
    minervini_symbols: tuple[tuple[str, str], ...] | None,
) -> tuple[CandidateSignal, ...]:
    if minervini_symbols is None:
        return tuple(
            CandidateSignal(symbol, company_name, SignalStatus.UNKNOWN)
            for symbol, company_name in primary_symbols
        )

    minervini_companies = dict(minervini_symbols)
    candidates: list[CandidateSignal] = []
    for symbol, company_name in primary_symbols:
        minervini_company = minervini_companies.get(symbol)
        status = (
            SignalStatus.YES
            if minervini_company is not None
            else SignalStatus.NO
        )
        if minervini_company is not None and _company_names_substantially_different(
            company_name, minervini_company
        ):
            warnings.warn(
                f"Company-name mismatch for symbol {symbol}: primary has "
                f"{company_name!r} but Minervini has {minervini_company!r}; "
                "membership remains YES",
                CompanyNameMismatchWarning,
                stacklevel=2,
            )
        candidates.append(CandidateSignal(symbol, company_name, status))
    return tuple(candidates)


def compare_screens(
    primary_csv: Path,
    minervini_csv: Path | None,
) -> tuple[CandidateSignal, ...]:
    """Attach Minervini membership without changing the primary candidate list."""

    primary_symbols = read_symbols(Path(primary_csv))
    minervini_symbols = (
        read_symbols(Path(minervini_csv)) if minervini_csv is not None else None
    )
    return _compare_symbol_rows(primary_symbols, minervini_symbols)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    primary = parser.add_mutually_exclusive_group(required=True)
    primary.add_argument(
        "--primary",
        type=Path,
        metavar="FILE",
        help="explicit Build Your Screen CSV",
    )
    primary.add_argument(
        "--primary-latest-from",
        type=Path,
        metavar="DIRECTORY",
        help="directory containing Build Your Screen CSVs",
    )

    minervini = parser.add_mutually_exclusive_group()
    minervini.add_argument(
        "--minervini",
        type=Path,
        metavar="FILE",
        help="explicit Minervini 1-Month CSV",
    )
    minervini.add_argument(
        "--minervini-latest-from",
        type=Path,
        metavar="DIRECTORY",
        help="directory containing Minervini 1-Month CSVs",
    )
    return parser.parse_args(argv)


def _resolve_sources(args: argparse.Namespace) -> tuple[Path, Path | None]:
    primary_csv = (
        latest_csv(args.primary_latest_from)
        if args.primary_latest_from is not None
        else args.primary
    )
    if args.minervini_latest_from is not None:
        minervini_csv = latest_csv(args.minervini_latest_from)
    else:
        minervini_csv = args.minervini
    return primary_csv, minervini_csv


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        primary_csv, minervini_csv = _resolve_sources(args)
        primary_symbols = read_symbols(primary_csv)
        minervini_symbols = (
            read_symbols(minervini_csv) if minervini_csv is not None else None
        )
        candidates = _compare_symbol_rows(primary_symbols, minervini_symbols)
    except (MarketSmithCsvError, OSError) as error:
        print(f"Screen comparison failed: {error}")
        return 1

    print(f"Primary candidates: {len(primary_symbols)}")
    if minervini_symbols is None:
        print("Minervini signal: UNKNOWN")
    else:
        print(f"Minervini symbols: {len(minervini_symbols)}")
    print()
    for candidate in candidates:
        print(
            f"{candidate.symbol:<12} "
            f"Minervini: {candidate.minervini_1_month.value}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
