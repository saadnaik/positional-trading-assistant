from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Event
import time
import unittest

from automation.analysis import AnalysisPhase, AnalysisProgress, AnalysisRunResult
from automation.export_screen import SessionExpiredError
from web.job_manager import AnalysisJobManager
from web.models import MarketSmithSessionStatus, RunStatus


def result() -> AnalysisRunResult:
    now = datetime.now(timezone.utc)
    return AnalysisRunResult(now, now, Path("a.csv"), Path("b.csv"), Path("c.csv"), 0, (), (), ())


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached")


class JobManagerTests(unittest.TestCase):
    def test_initial_state_reflects_saved_auth_without_claiming_valid(self) -> None:
        manager = AnalysisJobManager(lambda **kwargs: result(), auth_state=Path(__file__))
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.status, RunStatus.IDLE)
        self.assertEqual(snapshot.session_status, MarketSmithSessionStatus.SAVED)

    def test_duplicate_start_is_rejected_and_progress_completes(self) -> None:
        release = Event()
        calls = 0

        def runner(*, progress_callback):
            nonlocal calls
            calls += 1
            progress_callback(AnalysisProgress(AnalysisPhase.ANALYZING_CANDIDATES, "AAA", 1, 3))
            release.wait(2)
            return result()

        manager = AnalysisJobManager(runner, auth_state=Path("missing"))
        self.assertTrue(manager.start())
        wait_until(lambda: manager.snapshot().current_symbol == "AAA")
        self.assertFalse(manager.start())
        self.assertEqual(calls, 1)
        self.assertEqual(manager.snapshot().processed, 1)
        release.set()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)
        self.assertEqual(manager.snapshot().session_status, MarketSmithSessionStatus.VALID)

    def test_failed_run_keeps_previous_successful_result(self) -> None:
        calls = 0

        def runner(*, progress_callback):
            nonlocal calls
            calls += 1
            if calls == 1:
                return result()
            raise RuntimeError("export failed at /secret/location")

        manager = AnalysisJobManager(runner, auth_state=Path("missing"))
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)
        previous = manager.snapshot().latest_result
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.FAILED)
        snapshot = manager.snapshot()
        self.assertIs(snapshot.latest_result, previous)
        self.assertNotIn("/secret", snapshot.error or "")

    def test_session_expiry_is_classified_without_exposing_details(self) -> None:
        def runner(*, progress_callback):
            raise SessionExpiredError("login session expired cookie=secret")

        manager = AnalysisJobManager(runner, auth_state=Path(__file__))
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.FAILED)
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.session_status, MarketSmithSessionStatus.EXPIRED)
        self.assertNotIn("cookie", snapshot.error.casefold())
        self.assertNotIn("secret", snapshot.error.casefold())


if __name__ == "__main__":
    unittest.main()
