"""Single-process, single-run background manager for Falcon Stocks v0.1."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Callable

from automation.analysis import AnalysisProgress, AnalysisRunResult, run_complete_analysis
from automation.export_screen import AUTH_STATE, SessionExpiredError
from web.models import MarketSmithSessionStatus, RunSnapshot, RunStatus, safe_error_message


AnalysisRunner = Callable[..., AnalysisRunResult]


class AnalysisJobManager:
    def __init__(
        self,
        runner: AnalysisRunner = run_complete_analysis,
        *,
        auth_state: Path = AUTH_STATE,
    ) -> None:
        self._runner = runner
        self._lock = Lock()
        self._status = RunStatus.IDLE
        self._session_status = (
            MarketSmithSessionStatus.SAVED
            if auth_state.is_file()
            else MarketSmithSessionStatus.NOT_CONFIGURED
        )
        self._phase = None
        self._current_symbol = None
        self._processed = 0
        self._total = None
        self._error = None
        self._started_at = None
        self._completed_at = None
        self._latest_result: AnalysisRunResult | None = None
        self._thread: Thread | None = None

    def start(self) -> bool:
        with self._lock:
            if self._status == RunStatus.RUNNING:
                return False
            self._status = RunStatus.RUNNING
            self._phase = None
            self._current_symbol = None
            self._processed = 0
            self._total = None
            self._error = None
            self._started_at = datetime.now(timezone.utc)
            self._completed_at = None
            self._thread = Thread(target=self._run, name="falcon-analysis", daemon=True)
            self._thread.start()
            return True

    def _on_progress(self, progress: AnalysisProgress) -> None:
        with self._lock:
            self._phase = progress.phase
            self._current_symbol = progress.current_symbol
            self._processed = progress.processed
            self._total = progress.total

    def _run(self) -> None:
        try:
            result = self._runner(progress_callback=self._on_progress)
        except Exception as error:
            with self._lock:
                self._status = RunStatus.FAILED
                self._error = safe_error_message(error)
                self._completed_at = datetime.now(timezone.utc)
                if isinstance(error, SessionExpiredError) or "expired" in str(error).casefold():
                    self._session_status = MarketSmithSessionStatus.EXPIRED
            return
        with self._lock:
            self._status = RunStatus.COMPLETED
            self._session_status = MarketSmithSessionStatus.VALID
            self._started_at = result.started_at
            self._completed_at = result.completed_at
            self._latest_result = result
            self._error = None

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return RunSnapshot(
                status=self._status,
                session_status=self._session_status,
                phase=self._phase,
                current_symbol=self._current_symbol,
                processed=self._processed,
                total=self._total,
                error=self._error,
                started_at=self._started_at,
                completed_at=self._completed_at,
                latest_result=self._latest_result,
            )
