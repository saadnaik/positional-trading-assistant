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
    minervini_5_months: SignalStatus
    minervini_overall: SignalStatus


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


def combine_minervini_statuses(
    one_month: SignalStatus,
    five_months: SignalStatus,
) -> SignalStatus:
    """Combine independent timeframe signals without affecting stock selection."""

    if SignalStatus.YES in (one_month, five_months):
        return SignalStatus.YES
    if one_month == SignalStatus.NO and five_months == SignalStatus.NO:
        return SignalStatus.NO
    return SignalStatus.UNKNOWN


def _membership_status(
    symbol: str,
    companies: dict[str, str] | None,
) -> SignalStatus:
    if companies is None:
        return SignalStatus.UNKNOWN
    return SignalStatus.YES if symbol in companies else SignalStatus.NO


def _compare_symbol_rows(
    primary_symbols: tuple[tuple[str, str], ...],
    one_month_symbols: tuple[tuple[str, str], ...] | None,
    five_month_symbols: tuple[tuple[str, str], ...] | None = None,
) -> tuple[CandidateSignal, ...]:
    one_month_companies = dict(one_month_symbols) if one_month_symbols is not None else None
    five_month_companies = dict(five_month_symbols) if five_month_symbols is not None else None
    candidates: list[CandidateSignal] = []
    for symbol, company_name in primary_symbols:
        one_month = _membership_status(symbol, one_month_companies)
        five_months = _membership_status(symbol, five_month_companies)
        for timeframe, companies in (
            ("1-Month", one_month_companies),
            ("5-Month", five_month_companies),
        ):
            minervini_company = companies.get(symbol) if companies is not None else None
            if minervini_company is not None and _company_names_substantially_different(
                company_name, minervini_company
            ):
                warnings.warn(
                    f"Company-name mismatch for symbol {symbol}: primary has "
                    f"{company_name!r} but Minervini {timeframe} has "
                    f"{minervini_company!r}; membership remains YES",
                    CompanyNameMismatchWarning,
                    stacklevel=2,
                )
        candidates.append(
            CandidateSignal(
                symbol,
                company_name,
                one_month,
                five_months,
                combine_minervini_statuses(one_month, five_months),
            )
        )
    return tuple(candidates)


def compare_screens(
    primary_csv: Path,
    minervini_1_month_csv: Path | None,
    minervini_5_months_csv: Path | None = None,
) -> tuple[CandidateSignal, ...]:
    """Attach Minervini membership without changing the primary candidate list."""

    primary_symbols = read_symbols(Path(primary_csv))
    one_month_symbols = (
        read_symbols(Path(minervini_1_month_csv))
        if minervini_1_month_csv is not None
        else None
    )
    five_month_symbols = (
        read_symbols(Path(minervini_5_months_csv))
        if minervini_5_months_csv is not None
        else None
    )
    return _compare_symbol_rows(primary_symbols, one_month_symbols, five_month_symbols)


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
        dest="minervini_1_month",
        type=Path,
        metavar="FILE",
        help="explicit Minervini 1-Month CSV",
    )
    minervini.add_argument(
        "--minervini-latest-from",
        dest="minervini_1_month_latest_from",
        type=Path,
        metavar="DIRECTORY",
        help="directory containing Minervini 1-Month CSVs",
    )
    five_months = parser.add_mutually_exclusive_group()
    five_months.add_argument(
        "--minervini-5-months",
        type=Path,
        metavar="FILE",
        help="explicit Minervini 5-Month CSV",
    )
    five_months.add_argument(
        "--minervini-5-months-latest-from",
        type=Path,
        metavar="DIRECTORY",
        help="directory containing Minervini 5-Month CSVs",
    )
    return parser.parse_args(argv)


def _resolve_sources(
    args: argparse.Namespace,
) -> tuple[Path, Path | None, Path | None]:
    primary_csv = (
        latest_csv(args.primary_latest_from)
        if args.primary_latest_from is not None
        else args.primary
    )
    if args.minervini_1_month_latest_from is not None:
        one_month_csv = latest_csv(args.minervini_1_month_latest_from)
    else:
        one_month_csv = args.minervini_1_month
    if args.minervini_5_months_latest_from is not None:
        five_month_csv = latest_csv(args.minervini_5_months_latest_from)
    else:
        five_month_csv = args.minervini_5_months
    return primary_csv, one_month_csv, five_month_csv


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        primary_csv, one_month_csv, five_month_csv = _resolve_sources(args)
        primary_symbols = read_symbols(primary_csv)
        one_month_symbols = (
            read_symbols(one_month_csv) if one_month_csv is not None else None
        )
        five_month_symbols = (
            read_symbols(five_month_csv) if five_month_csv is not None else None
        )
        candidates = _compare_symbol_rows(
            primary_symbols, one_month_symbols, five_month_symbols
        )
    except (MarketSmithCsvError, OSError) as error:
        print(f"Screen comparison failed: {error}")
        return 1

    print(f"Primary candidates: {len(primary_symbols)}")
    print(
        "Minervini 1-Month symbols: "
        + (str(len(one_month_symbols)) if one_month_symbols is not None else "UNKNOWN")
    )
    print(
        "Minervini 5-Month symbols: "
        + (str(len(five_month_symbols)) if five_month_symbols is not None else "UNKNOWN")
    )
    print()
    for candidate in candidates:
        print(
            f"{candidate.symbol:<12} "
            f"1M: {candidate.minervini_1_month.value} "
            f"5M: {candidate.minervini_5_months.value} "
            f"Overall: {candidate.minervini_overall.value}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
