"""Structured orchestration for a complete fresh Falcon Stocks analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from automation.export_screen import (
    BUILD_YOUR_SCREEN,
    MINERVINI_1_MONTH,
    ScreenConfig,
    export_screen,
)
from automation.run_pipeline import (
    CandidateFailure,
    EvaluatedCandidate,
    RankedCandidate,
    rank_candidates,
    run_pipeline_outcome,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CPP_EXECUTABLE = PROJECT_ROOT / "cpp" / "build" / "stock_reader"


class AnalysisPhase(str, Enum):
    EXPORTING_PRIMARY = "EXPORTING_PRIMARY"
    EXPORTING_MINERVINI = "EXPORTING_MINERVINI"
    COMPARING_SCREENS = "COMPARING_SCREENS"
    ANALYZING_CANDIDATES = "ANALYZING_CANDIDATES"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class AnalysisProgress:
    phase: AnalysisPhase
    current_symbol: str | None = None
    processed: int = 0
    total: int | None = None


@dataclass(frozen=True)
class AnalysisRunResult:
    started_at: datetime
    completed_at: datetime
    primary_csv: Path
    minervini_csv: Path
    candidates_requested: int
    successful: tuple[EvaluatedCandidate, ...]
    failed: tuple[CandidateFailure, ...]
    ranked: tuple[RankedCandidate, ...]


ProgressCallback = Callable[[AnalysisProgress], None]
Exporter = Callable[..., Path]


def run_complete_analysis(
    cpp_executable: Path = DEFAULT_CPP_EXECUTABLE,
    *,
    progress_callback: ProgressCallback | None = None,
    exporter: Exporter | None = None,
) -> AnalysisRunResult:
    """Export both fresh screens, evaluate every primary candidate, and rank."""

    started_at = datetime.now(timezone.utc)
    export = exporter or export_screen

    def report(progress: AnalysisProgress) -> None:
        if progress_callback is not None:
            progress_callback(progress)

    report(AnalysisProgress(AnalysisPhase.EXPORTING_PRIMARY))
    primary_csv = export(BUILD_YOUR_SCREEN, pause_on_failure=False)

    report(AnalysisProgress(AnalysisPhase.EXPORTING_MINERVINI))
    minervini_csv = export(MINERVINI_1_MONTH, pause_on_failure=False)

    report(AnalysisProgress(AnalysisPhase.COMPARING_SCREENS))

    def candidate_progress(symbol: str | None, processed: int, total: int) -> None:
        report(
            AnalysisProgress(
                AnalysisPhase.ANALYZING_CANDIDATES,
                current_symbol=symbol,
                processed=processed,
                total=total,
            )
        )

    outcome = run_pipeline_outcome(
        primary_csv,
        minervini_csv,
        Path(cpp_executable),
        limit=None,
        progress_callback=candidate_progress,
    )
    completed_at = datetime.now(timezone.utc)
    report(
        AnalysisProgress(
            AnalysisPhase.COMPLETED,
            processed=outcome.requested,
            total=outcome.requested,
        )
    )
    return AnalysisRunResult(
        started_at=started_at,
        completed_at=completed_at,
        primary_csv=primary_csv,
        minervini_csv=minervini_csv,
        candidates_requested=outcome.requested,
        successful=outcome.successful,
        failed=outcome.failed,
        ranked=rank_candidates(outcome.successful),
    )
