from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from automation.analysis import AnalysisRunResult
from automation.compare_screens import SignalStatus
from automation.run_pipeline import (
    CandidateFailure,
    EvaluatedCandidate,
    ViolationSummary,
    rank_candidates,
)
from web.persistence import AnalysisResultStore, PersistenceError


def analysis_result() -> AnalysisRunResult:
    first = EvaluatedCandidate(
        symbol="GOLDIAM",
        company_name="Goldiam International",
        minervini_1_month=SignalStatus.YES,
        minervini_5_months=SignalStatus.NO,
        minervini_overall=SignalStatus.YES,
        json_path=Path("data/extracted/GOLDIAM.json"),
        fundamental_decision="PASS",
        violation_count=5,
        total_rules=12,
        weighted_positional_score="89.6",
        cpp_stdout="Symbol: GOLDIAM\nFull C++ report\n\x00-preserved\n",
        violations_summary=(
            ViolationSummary(
                rule_id="R4",
                description="Sales threshold",
                raw_block="R4 VIOLATION\nRaw block exactly retained\n",
                weight="8",
                actual="23%",
                requirement=">= 25%",
                distance="Shortfall: 2 percentage points",
                credit="0.920",
                contribution="7.360 / 8",
                reason="Near miss.",
            ),
        ),
    )
    second = EvaluatedCandidate(
        symbol="NETWEB",
        company_name="Netweb Technologies",
        minervini_1_month=SignalStatus.UNKNOWN,
        minervini_5_months=SignalStatus.NO,
        minervini_overall=SignalStatus.UNKNOWN,
        json_path=Path("data/extracted/NETWEB.json"),
        fundamental_decision="PASS",
        violation_count=4,
        total_rules=12,
        weighted_positional_score="81.3",
        cpp_stdout="Symbol: NETWEB\n",
        violations_summary=(),
    )
    failed = CandidateFailure(
        symbol="FAILED",
        company_name="Failed Industries",
        minervini_1_month=SignalStatus.NO,
        minervini_5_months=SignalStatus.UNKNOWN,
        minervini_overall=SignalStatus.UNKNOWN,
        reason="export failed cookie=do-not-persist at /private/path",
        screenshot_path=Path("logs/FAILED.png"),
        html_path=Path("logs/FAILED.html"),
    )
    started = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 8, 7, 10, 15, tzinfo=timezone.utc)
    successful = (first, second)
    return AnalysisRunResult(
        started_at=started,
        completed_at=completed,
        primary_csv=Path("data/incoming/build_your_screen/primary.csv"),
        minervini_1_month_csv=Path("data/incoming/minervini_1_month/one.csv"),
        minervini_5_months_csv=Path("data/incoming/minervini_5_months/five.csv"),
        candidates_requested=3,
        successful=successful,
        failed=(failed,),
        ranked=rank_candidates(successful),
    )


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "data" / "state" / "latest_analysis.json"
        self.store = AnalysisResultStore(self.path, project_root=self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def payload(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_payload(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_absent_snapshot_returns_none(self) -> None:
        self.assertIsNone(self.store.load())

    def test_round_trip_restores_dashboard_and_stock_detail_data(self) -> None:
        original = analysis_result()
        self.store.save(original)
        restored = self.store.load()
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.completed_at, original.completed_at)
        self.assertEqual(restored.candidates_requested, 3)
        self.assertEqual([item.symbol for item in restored.successful], ["GOLDIAM", "NETWEB"])
        self.assertEqual(
            [item.candidate.symbol for item in restored.ranked],
            [item.candidate.symbol for item in original.ranked],
        )
        self.assertIs(restored.ranked[0].candidate, restored.successful[0])
        stock = restored.successful[0]
        self.assertEqual(stock.cpp_stdout, original.successful[0].cpp_stdout)
        self.assertEqual(
            stock.violations_summary[0].raw_block,
            original.successful[0].violations_summary[0].raw_block,
        )
        self.assertEqual(stock.weighted_positional_score, "89.6")
        self.assertEqual(stock.minervini_1_month, SignalStatus.YES)
        self.assertEqual(stock.minervini_5_months, SignalStatus.NO)
        self.assertEqual(stock.minervini_overall, SignalStatus.YES)

    def test_paths_are_serialized_relative_and_restored_inside_project(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        self.assertEqual(
            payload["analysis"]["primary_csv"],
            "data/incoming/build_your_screen/primary.csv",
        )
        restored = self.store.load()
        assert restored is not None
        self.assertEqual(
            restored.primary_csv,
            self.root / "data" / "incoming" / "build_your_screen" / "primary.csv",
        )

    def test_snapshot_is_restrictive_and_restart_store_restores_it(self) -> None:
        self.store.save(analysis_result())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        restarted = AnalysisResultStore(self.path, project_root=self.root)
        self.assertEqual(restarted.load().successful[0].symbol, "GOLDIAM")

    def test_persisted_json_contains_no_auth_cookie_or_raw_failure_secret(self) -> None:
        self.store.save(analysis_result())
        serialized = self.path.read_text(encoding="utf-8")
        lowered = serialized.casefold()
        self.assertNotIn("do-not-persist", lowered)
        self.assertNotIn("marketsmith_state", lowered)
        self.assertNotIn('"cookie"', lowered)
        self.assertNotIn('"auth"', lowered)

    def test_malformed_json_is_rejected_without_deletion(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(PersistenceError):
            self.store.load()
        self.assertTrue(self.path.exists())

    def test_unsupported_schema_is_rejected(self) -> None:
        self.write_payload({"schema_version": 99, "analysis": {}})
        with self.assertRaises(PersistenceError):
            self.store.load()

    def test_missing_required_field_is_rejected(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        del payload["analysis"]["completed_at"]
        self.write_payload(payload)
        with self.assertRaises(PersistenceError):
            self.store.load()

    def test_semantically_inconsistent_candidate_total_is_rejected(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        payload["analysis"]["candidates_requested"] = 99
        self.write_payload(payload)
        with self.assertRaises(PersistenceError):
            self.store.load()

    def test_absolute_restored_path_is_rejected(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        payload["analysis"]["primary_csv"] = "/etc/passwd"
        self.write_payload(payload)
        with self.assertRaises(PersistenceError):
            self.store.load()

    def test_parent_traversal_is_rejected(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        payload["analysis"]["successful"][0]["json_path"] = "../secret.json"
        self.write_payload(payload)
        with self.assertRaises(PersistenceError):
            self.store.load()

    def test_encoding_path_outside_project_is_rejected(self) -> None:
        original = analysis_result()
        unsafe = AnalysisRunResult(
            original.started_at,
            original.completed_at,
            Path("/outside/primary.csv"),
            original.minervini_1_month_csv,
            original.minervini_5_months_csv,
            original.candidates_requested,
            original.successful,
            original.failed,
            original.ranked,
        )
        with self.assertRaises(PersistenceError):
            self.store.save(unsafe)

    def test_atomic_replace_failure_preserves_previous_snapshot(self) -> None:
        self.store.save(analysis_result())
        previous = self.path.read_bytes()
        with patch("web.persistence.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(PersistenceError):
                self.store.save(analysis_result())
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob(".latest_analysis.*.tmp")), [])

    def test_rank_is_recomputed_not_serialized(self) -> None:
        self.store.save(analysis_result())
        payload = self.payload()
        self.assertNotIn("ranked", payload["analysis"])
        restored = self.store.load()
        assert restored is not None
        self.assertEqual(restored.ranked, rank_candidates(restored.successful))


if __name__ == "__main__":
    unittest.main()
