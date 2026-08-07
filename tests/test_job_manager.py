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
from web.persistence import PersistenceError


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


class MemoryStore:
    def __init__(self, restored: AnalysisRunResult | None = None) -> None:
        self.restored = restored
        self.saved: list[AnalysisRunResult] = []

    def load(self) -> AnalysisRunResult | None:
        return self.restored

    def save(self, completed: AnalysisRunResult) -> None:
        self.saved.append(completed)
        self.restored = completed


class JobManagerTests(unittest.TestCase):
    def test_initial_state_reflects_saved_auth_without_claiming_valid(self) -> None:
        manager = AnalysisJobManager(
            lambda **kwargs: result(), auth_state=Path(__file__), result_store=MemoryStore()
        )
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

        store = MemoryStore()
        manager = AnalysisJobManager(
            runner, auth_state=Path("missing"), result_store=store
        )
        self.assertTrue(manager.start())
        wait_until(lambda: manager.snapshot().current_symbol == "AAA")
        self.assertFalse(manager.start())
        self.assertEqual(calls, 1)
        self.assertEqual(manager.snapshot().processed, 1)
        release.set()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)
        self.assertEqual(manager.snapshot().session_status, MarketSmithSessionStatus.VALID)
        self.assertEqual(store.saved, [manager.snapshot().latest_result])

    def test_failed_run_keeps_previous_successful_result(self) -> None:
        calls = 0

        def runner(*, progress_callback):
            nonlocal calls
            calls += 1
            if calls == 1:
                return result()
            raise RuntimeError("export failed at /secret/location")

        store = MemoryStore()
        manager = AnalysisJobManager(
            runner, auth_state=Path("missing"), result_store=store
        )
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)
        previous = manager.snapshot().latest_result
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.FAILED)
        snapshot = manager.snapshot()
        self.assertIs(snapshot.latest_result, previous)
        self.assertEqual(store.saved, [previous])
        self.assertNotIn("/secret", snapshot.error or "")

    def test_session_expiry_is_classified_without_exposing_details(self) -> None:
        def runner(*, progress_callback):
            raise SessionExpiredError("login session expired cookie=secret")

        manager = AnalysisJobManager(
            runner, auth_state=Path(__file__), result_store=MemoryStore()
        )
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.FAILED)
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.session_status, MarketSmithSessionStatus.EXPIRED)
        self.assertNotIn("cookie", snapshot.error.casefold())
        self.assertNotIn("secret", snapshot.error.casefold())

    def test_restored_result_starts_completed_without_claiming_valid_session(self) -> None:
        restored = result()
        manager = AnalysisJobManager(
            lambda **kwargs: result(),
            auth_state=Path(__file__),
            result_store=MemoryStore(restored),
        )
        snapshot = manager.snapshot()
        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        self.assertIs(snapshot.latest_result, restored)
        self.assertEqual(snapshot.completed_at, restored.completed_at)
        self.assertEqual(snapshot.session_status, MarketSmithSessionStatus.SAVED)

    def test_running_new_analysis_keeps_restored_result(self) -> None:
        release = Event()
        restored = result()

        def runner(*, progress_callback):
            release.wait(2)
            return result()

        manager = AnalysisJobManager(
            runner,
            auth_state=Path("missing"),
            result_store=MemoryStore(restored),
        )
        self.assertTrue(manager.start())
        self.assertEqual(manager.snapshot().status, RunStatus.RUNNING)
        self.assertIs(manager.snapshot().latest_result, restored)
        release.set()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)

    def test_later_success_replaces_restored_result_and_snapshot(self) -> None:
        restored = result()
        later = result()
        store = MemoryStore(restored)
        manager = AnalysisJobManager(
            lambda **kwargs: later,
            auth_state=Path("missing"),
            result_store=store,
        )
        manager.start()
        wait_until(lambda: manager.snapshot().status == RunStatus.COMPLETED)
        self.assertIs(manager.snapshot().latest_result, later)
        self.assertIs(store.restored, later)

    def test_invalid_restore_logs_and_starts_idle(self) -> None:
        class BrokenStore(MemoryStore):
            def load(self):
                raise PersistenceError("invalid snapshot at /secret/path")

        with self.assertLogs("web.job_manager", level="WARNING") as captured:
            manager = AnalysisJobManager(
                lambda **kwargs: result(),
                auth_state=Path("missing"),
                result_store=BrokenStore(),
            )
        self.assertEqual(manager.snapshot().status, RunStatus.IDLE)
        self.assertNotIn("/secret", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
