"""Run the five-candidate MarketSmith extraction and C++ evaluation prototype."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright

from automation.compare_screens import CandidateSignal, SignalStatus, compare_screens
from automation.read_csv import MarketSmithCsvError, latest_csv
from extract.json_output import write_stock_json
from extract.stock import extract_stock_data
from extract.summary import AUTH_STATE, EVALUATION_URL, confirm_stock_page


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"


@dataclass(frozen=True)
class CppEvaluationSummary:
    symbol: str
    fundamental_decision: str
    violation_count: int
    total_rules: int
    weighted_positional_score: str
    stdout: str


@dataclass(frozen=True)
class EvaluatedCandidate:
    symbol: str
    company_name: str
    minervini_1_month: SignalStatus
    json_path: Path
    fundamental_decision: str
    violation_count: int
    total_rules: int
    weighted_positional_score: str
    cpp_stdout: str


@dataclass(frozen=True)
class CandidateFailure:
    symbol: str
    company_name: str
    minervini_1_month: SignalStatus
    reason: str
    screenshot_path: Path | None
    html_path: Path | None


PipelineResult = EvaluatedCandidate | CandidateFailure


@dataclass(frozen=True)
class PipelineOutcome:
    requested: int
    results: tuple[PipelineResult, ...]

    @property
    def successful(self) -> tuple[EvaluatedCandidate, ...]:
        return tuple(
            result for result in self.results if isinstance(result, EvaluatedCandidate)
        )

    @property
    def failed(self) -> tuple[CandidateFailure, ...]:
        return tuple(
            result for result in self.results if isinstance(result, CandidateFailure)
        )


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot safely produce a requested result."""


class CppEvaluationError(PipelineError):
    """Raised when stock_reader fails or emits an invalid summary."""


def resolve_latest_inputs(
    primary_directory: Path,
    minervini_directory: Path | None,
) -> tuple[Path, Path | None]:
    """Resolve newest CSVs only inside explicitly supplied directories."""

    primary_csv = latest_csv(Path(primary_directory))
    minervini_csv = (
        latest_csv(Path(minervini_directory))
        if minervini_directory is not None
        else None
    )
    return primary_csv, minervini_csv


def _read_json_symbol(json_path: Path) -> str:
    try:
        with json_path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise CppEvaluationError(
            f"Could not read JSON symbol from {json_path}: {error}"
        ) from error
    symbol = document.get("symbol") if isinstance(document, dict) else None
    if not isinstance(symbol, str) or not symbol:
        raise CppEvaluationError(f"JSON lacks a valid top-level Symbol: {json_path}")
    return symbol


def _completed_process_diagnostics(
    reason: str, completed: subprocess.CompletedProcess[str]
) -> CppEvaluationError:
    return CppEvaluationError(
        f"{reason}\n"
        f"C++ exit status: {completed.returncode}\n"
        f"C++ stdout:\n{completed.stdout}\n"
        f"C++ stderr:\n{completed.stderr}"
    )


def _single_summary_value(
    label: str,
    pattern: re.Pattern[str],
    stdout: str,
    completed: subprocess.CompletedProcess[str],
) -> str:
    summary_lines = re.findall(
        rf"^{re.escape(label)}:", stdout, flags=re.MULTILINE
    )
    if len(summary_lines) > 1:
        raise _completed_process_diagnostics(
            f"C++ output contains duplicate or conflicting {label} summary lines.",
            completed,
        )
    matches = pattern.findall(stdout)
    if not matches:
        raise _completed_process_diagnostics(
            f"C++ output lacks a valid {label} summary line.", completed
        )
    if len(matches) != 1:
        raise _completed_process_diagnostics(
            f"C++ output contains duplicate or conflicting {label} summary lines.",
            completed,
        )
    return matches[0]


def _evaluate_with_cpp_for_symbol(
    executable: Path,
    json_path: Path,
    expected_symbol: str,
) -> CppEvaluationSummary:
    executable = Path(executable)
    json_path = Path(json_path)
    if not executable.is_file():
        raise CppEvaluationError(f"C++ executable does not exist: {executable}")
    if not os.access(executable, os.X_OK):
        raise CppEvaluationError(f"C++ executable is not runnable: {executable}")

    json_symbol = _read_json_symbol(json_path)
    if json_symbol != expected_symbol:
        raise CppEvaluationError(
            f"JSON symbol mismatch: expected {expected_symbol!r}, found {json_symbol!r}."
        )

    try:
        completed = subprocess.run(
            [str(executable), str(json_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise CppEvaluationError(
            f"Could not run C++ executable {executable}: {error}"
        ) from error
    if completed.returncode != 0:
        raise _completed_process_diagnostics(
            "C++ evaluator exited non-zero.", completed
        )

    symbol = _single_summary_value(
        "Symbol",
        re.compile(r"^Symbol:\s*(\S+)\s*$", re.MULTILINE),
        completed.stdout,
        completed,
    )
    decision = _single_summary_value(
        "Fundamental Evaluation",
        re.compile(
            r"^Fundamental Evaluation:\s*(PASS|REJECT)\s*$", re.MULTILINE
        ),
        completed.stdout,
        completed,
    )
    violations = _single_summary_value(
        "Violations",
        re.compile(r"^Violations:\s*(\S+)\s*$", re.MULTILINE),
        completed.stdout,
        completed,
    )
    weighted_score = _single_summary_value(
        "Weighted Positional Score",
        re.compile(
            r"^Weighted Positional Score:\s*(\d+\.\d+)/100\s*$",
            re.MULTILINE,
        ),
        completed.stdout,
        completed,
    )

    if symbol != expected_symbol:
        raise _completed_process_diagnostics(
            f"C++ symbol mismatch: expected {expected_symbol!r}, found {symbol!r}.",
            completed,
        )
    violation_match = re.fullmatch(r"(\d+)/12", violations)
    if violation_match is None:
        raise _completed_process_diagnostics(
            f"C++ Violations must have the form X/12, found {violations!r}.",
            completed,
        )
    violation_count = int(violation_match.group(1))
    if not 0 <= violation_count <= 12:
        raise _completed_process_diagnostics(
            f"C++ violation count is outside 0..12: {violation_count}.", completed
        )
    score_value = Decimal(weighted_score)
    if not Decimal("0") <= score_value <= Decimal("100"):
        raise _completed_process_diagnostics(
            "C++ Weighted Positional Score must be within 0..100, "
            f"found {weighted_score!r}.",
            completed,
        )
    return CppEvaluationSummary(
        symbol=symbol,
        fundamental_decision=decision,
        violation_count=violation_count,
        total_rules=12,
        weighted_positional_score=weighted_score,
        stdout=completed.stdout,
    )


def evaluate_with_cpp(
    executable: Path,
    json_path: Path,
) -> CppEvaluationSummary:
    """Run stock_reader and return its validated high-level WON summary."""

    expected_symbol = _read_json_symbol(Path(json_path))
    return _evaluate_with_cpp_for_symbol(executable, json_path, expected_symbol)


def process_candidate(
    page: Page,
    candidate: CandidateSignal,
    cpp_executable: Path,
) -> EvaluatedCandidate:
    """Extract, serialize, and evaluate one already selected primary candidate."""

    page.goto(
        EVALUATION_URL.format(symbol=candidate.symbol),
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.wait_for_function(
        "document.title !== 'Stock Share Price - MarketSmith India'",
        timeout=60_000,
    )
    confirm_stock_page(page, candidate.symbol, candidate.company_name)
    stock = extract_stock_data(page, candidate.symbol, candidate.company_name)
    if stock.symbol != candidate.symbol:
        raise PipelineError(
            f"Extracted symbol mismatch: expected {candidate.symbol!r}, "
            f"found {stock.symbol!r}."
        )
    json_path = write_stock_json(stock)
    summary = _evaluate_with_cpp_for_symbol(
        cpp_executable, json_path, candidate.symbol
    )
    return EvaluatedCandidate(
        symbol=candidate.symbol,
        company_name=candidate.company_name,
        minervini_1_month=candidate.minervini_1_month,
        json_path=json_path,
        fundamental_decision=summary.fundamental_decision,
        violation_count=summary.violation_count,
        total_rules=summary.total_rules,
        weighted_positional_score=summary.weighted_positional_score,
        cpp_stdout=summary.stdout,
    )


def _safe_symbol_for_filename(symbol: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", symbol).strip("._")
    return cleaned or "unknown_symbol"


def _save_candidate_diagnostics(page: Page, symbol: str) -> tuple[Path | None, Path | None]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_symbol = _safe_symbol_for_filename(symbol)
    screenshot_path = LOG_DIR / f"pipeline_{safe_symbol}_failure.png"
    html_path = LOG_DIR / f"pipeline_{safe_symbol}_failure.html"
    saved_screenshot: Path | None = None
    saved_html: Path | None = None
    try:
        page.screenshot(path=screenshot_path, full_page=True)
        saved_screenshot = screenshot_path
    except (OSError, PlaywrightError) as error:
        print(f"Could not save {symbol} failure screenshot: {error}")
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        saved_html = html_path
    except (OSError, PlaywrightError) as error:
        print(f"Could not save {symbol} failure HTML: {error}")
    return saved_screenshot, saved_html


def _process_candidates_in_context(
    context: BrowserContext,
    candidates: tuple[CandidateSignal, ...],
    cpp_executable: Path,
) -> PipelineOutcome:
    results: list[PipelineResult] = []
    for candidate in candidates:
        page = context.new_page()
        try:
            results.append(process_candidate(page, candidate, cpp_executable))
        except Exception as error:
            screenshot_path, html_path = _save_candidate_diagnostics(
                page, candidate.symbol
            )
            results.append(
                CandidateFailure(
                    symbol=candidate.symbol,
                    company_name=candidate.company_name,
                    minervini_1_month=candidate.minervini_1_month,
                    reason=str(error),
                    screenshot_path=screenshot_path,
                    html_path=html_path,
                )
            )
        finally:
            page.close()
    return PipelineOutcome(requested=len(candidates), results=tuple(results))


def _run_pipeline_outcome(
    primary_csv: Path,
    minervini_csv: Path | None,
    cpp_executable: Path,
    *,
    limit: int = 5,
) -> PipelineOutcome:
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    candidates = compare_screens(primary_csv, minervini_csv)
    if len(candidates) < limit:
        raise PipelineError(
            f"Requested {limit} candidates, but the primary CSV contains only "
            f"{len(candidates)}."
        )
    selected = candidates[:limit]
    if not AUTH_STATE.is_file():
        raise PipelineError(
            f"Authentication state not found: {AUTH_STATE}. "
            "Run automation/login.py first."
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context(storage_state=AUTH_STATE)
            return _process_candidates_in_context(context, selected, cpp_executable)
        finally:
            browser.close()


def run_pipeline(
    primary_csv: Path,
    minervini_csv: Path | None,
    cpp_executable: Path,
    *,
    limit: int = 5,
) -> tuple[EvaluatedCandidate, ...]:
    """Run the prototype and return successful evaluations in primary order."""

    return _run_pipeline_outcome(
        primary_csv, minervini_csv, cpp_executable, limit=limit
    ).successful


def _positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    if limit <= 0:
        raise argparse.ArgumentTypeError("limit must be greater than zero")
    return limit


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    primary = parser.add_mutually_exclusive_group(required=True)
    primary.add_argument("--primary", type=Path, metavar="FILE")
    primary.add_argument("--primary-latest-from", type=Path, metavar="DIRECTORY")
    minervini = parser.add_mutually_exclusive_group()
    minervini.add_argument("--minervini", type=Path, metavar="FILE")
    minervini.add_argument(
        "--minervini-latest-from", type=Path, metavar="DIRECTORY"
    )
    parser.add_argument("--cpp-executable", type=Path, required=True)
    parser.add_argument("--limit", type=_positive_limit, default=5)
    return parser.parse_args(argv)


def _resolve_cli_inputs(args: argparse.Namespace) -> tuple[Path, Path | None]:
    primary_csv = (
        latest_csv(args.primary_latest_from)
        if args.primary_latest_from is not None
        else args.primary
    )
    minervini_csv = (
        latest_csv(args.minervini_latest_from)
        if args.minervini_latest_from is not None
        else args.minervini
    )
    return primary_csv, minervini_csv


def _print_outcome(outcome: PipelineOutcome) -> None:
    for result in outcome.results:
        print(f"Symbol: {result.symbol}")
        if isinstance(result, CandidateFailure):
            print("Status: FAILED")
            print(f"Reason: {result.reason}")
        else:
            print(f"Company: {result.company_name}")
            print(f"Minervini 1-Month: {result.minervini_1_month.value}")
            print(f"Fundamental Evaluation: {result.fundamental_decision}")
            print(f"Violations: {result.violation_count}/{result.total_rules}")
            print(
                "Weighted Positional Score: "
                f"{result.weighted_positional_score}/100"
            )
            print(f"JSON output: {result.json_path}")
        print()

    successful = outcome.successful
    print(
        f"{'Symbol':<12} {'Minervini':<11} {'Fundamental':<13} "
        f"{'Violations':<12} Positional Score"
    )
    print("-" * 65)
    for result in successful:
        violations = f"{result.violation_count}/{result.total_rules}"
        print(
            f"{result.symbol:<12} {result.minervini_1_month.value:<11} "
            f"{result.fundamental_decision:<13} "
            f"{violations:<12} "
            f"{result.weighted_positional_score}"
        )

    for result in successful:
        print()
        print("=" * 40)
        print(f"{result.symbol} — {result.company_name}")
        print(f"Minervini 1-Month: {result.minervini_1_month.value}")
        print("=" * 40)
        print()
        print(result.cpp_stdout, end="" if result.cpp_stdout.endswith("\n") else "\n")

    print()
    print(f"Candidates requested: {outcome.requested}")
    print(f"Successful: {len(outcome.successful)}")
    print(f"Failed: {len(outcome.failed)}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        primary_csv, minervini_csv = _resolve_cli_inputs(args)
        outcome = _run_pipeline_outcome(
            primary_csv,
            minervini_csv,
            args.cpp_executable,
            limit=args.limit,
        )
    except (MarketSmithCsvError, PipelineError, OSError, ValueError) as error:
        print(f"Pipeline failed: {error}")
        return 1
    _print_outcome(outcome)
    return 0 if not outcome.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
