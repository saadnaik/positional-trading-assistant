from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import unittest

import httpx

from automation.analysis import AnalysisPhase, AnalysisRunResult
from automation.compare_screens import SignalStatus
from automation.run_pipeline import (
    CandidateCategory,
    CandidateFailure,
    EvaluatedCandidate,
    ViolationSummary,
    rank_candidates,
)
from web.app import create_app
from web.models import MarketSmithSessionStatus, RunSnapshot, RunStatus


def candidate(
    symbol: str,
    decision: str,
    signal: SignalStatus,
    score: str,
    violations: int,
) -> EvaluatedCandidate:
    return EvaluatedCandidate(
        symbol=symbol,
        company_name=f"{symbol} Research Industries",
        minervini_1_month=signal,
        json_path=Path("private") / f"{symbol}.json",
        fundamental_decision=decision,
        violation_count=violations,
        total_rules=12,
        weighted_positional_score=score,
        cpp_stdout=f"Symbol: {symbol}\nSECRET-FREE FULL RULE REPORT\n",
        violations_summary=(
            ViolationSummary(
                rule_id="R4",
                description="Latest quarterly sales growth threshold",
                raw_block="raw",
                weight="8",
                actual="Latest Sales growth: +23%",
                requirement=">= +25%",
                distance="Shortfall: 2 percentage points",
                credit="0.920 (92.0%)",
                contribution="7.360 / 8",
                reason="Ratio credit measures the threshold shortfall.",
            ),
        ),
    )


def completed_result() -> AnalysisRunResult:
    stocks = (
        candidate("REJECT", "REJECT", SignalStatus.YES, "99.0", 8),
        candidate("WONONLY", "PASS", SignalStatus.UNKNOWN, "75.0", 3),
        candidate("GOLDIAM", "PASS", SignalStatus.YES, "89.6", 5),
    )
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    return AnalysisRunResult(
        started_at=now,
        completed_at=now,
        primary_csv=Path("/private/primary.csv"),
        minervini_csv=Path("/private/minervini.csv"),
        candidates_requested=4,
        successful=stocks,
        failed=(
            CandidateFailure(
                "FAILED",
                "Failed Research Ltd",
                SignalStatus.NO,
                "C++ stdout: token=secret /private/report.json",
                Path("/private/failure.png"),
                Path("/private/failure.html"),
            ),
        ),
        ranked=rank_candidates(stocks),
    )


class FakeManager:
    def __init__(self, snapshot: RunSnapshot, start_result: bool = True) -> None:
        self.value = snapshot
        self.start_result = start_result
        self.starts = 0

    def snapshot(self) -> RunSnapshot:
        return self.value

    def start(self) -> bool:
        self.starts += 1
        if not self.start_result:
            return False
        self.value = replace(
            self.value,
            status=RunStatus.RUNNING,
            phase=AnalysisPhase.EXPORTING_PRIMARY,
        )
        return True


def snapshot(
    status: RunStatus = RunStatus.IDLE,
    result: AnalysisRunResult | None = None,
    error: str | None = None,
) -> RunSnapshot:
    return RunSnapshot(
        status=status,
        session_status=MarketSmithSessionStatus.SAVED,
        phase=None,
        current_symbol=None,
        processed=0,
        total=None,
        error=error,
        started_at=None,
        completed_at=None,
        latest_result=result,
    )


class FalconWebTests(unittest.IsolatedAsyncioTestCase):
    def client(self, manager: FakeManager) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=create_app(manager))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def test_health_and_initial_mobile_dashboard(self) -> None:
        async with self.client(FakeManager(snapshot())) as client:
            self.assertEqual((await client.get("/health")).json(), {"status": "ok"})
            response = await client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Falcon Stocks", response.text)
        self.assertIn("Positional Trading Research Dashboard", response.text)
        self.assertIn("No analysis has been completed yet", response.text)
        self.assertIn('name="viewport"', response.text)
        self.assertNotIn("BUY", response.text.upper())
        self.assertNotIn("SELL", response.text.upper())

    async def test_start_returns_202_and_duplicate_returns_409(self) -> None:
        manager = FakeManager(snapshot())
        async with self.client(manager) as client:
            response = await client.post("/api/runs")
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "RUNNING")
            manager.start_result = False
            self.assertEqual((await client.post("/api/runs")).status_code, 409)
        self.assertEqual(manager.starts, 2)

    async def test_safe_running_completed_and_failed_status(self) -> None:
        manager = FakeManager(
            replace(
                snapshot(RunStatus.RUNNING),
                phase=AnalysisPhase.ANALYZING_CANDIDATES,
                current_symbol="NETWEB",
                processed=3,
                total=13,
            )
        )
        async with self.client(manager) as client:
            payload = (await client.get("/api/runs/current")).json()
            self.assertEqual(payload["current_symbol"], "NETWEB")
            self.assertEqual((payload["processed"], payload["total"]), (3, 13))
            self.assertNotIn("primary_csv", payload)
            self.assertNotIn("auth", str(payload).casefold())

            manager.value = snapshot(RunStatus.COMPLETED, completed_result())
            self.assertEqual((await client.get("/api/runs/current")).json()["status"], "COMPLETED")
            manager.value = snapshot(RunStatus.FAILED, completed_result(), "Export failed safely.")
            failed = (await client.get("/api/runs/current")).json()
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["error"], "Export failed safely.")

    async def test_ranked_dashboard_and_failures_are_safe(self) -> None:
        async with self.client(FakeManager(snapshot(RunStatus.COMPLETED, completed_result()))) as client:
            response = await client.get("/")
        text = response.text
        self.assertLess(text.index("1. WON + Minervini"), text.index("2. WON Only"))
        self.assertLess(text.index("2. WON Only"), text.index("3. WON Rejected"))
        self.assertLess(text.index("GOLDIAM"), text.index("WONONLY"))
        self.assertLess(text.index("WONONLY"), text.index("REJECT"))
        self.assertIn("Analysis Failures", text)
        self.assertIn("C++ stock evaluation failed", text)
        self.assertNotIn("token=secret", text)
        self.assertNotIn("/private", text)

    async def test_stock_detail_renders_compact_and_verbatim_analysis(self) -> None:
        async with self.client(FakeManager(snapshot(RunStatus.COMPLETED, completed_result()))) as client:
            response = await client.get("/stocks/GOLDIAM")
        self.assertEqual(response.status_code, 200)
        for expected in (
            "GOLDIAM",
            "Fundamental Evaluation",
            "89.6",
            "Latest quarterly sales growth threshold",
            "Shortfall: 2 percentage points",
            "7.360 / 8",
            "Full 12-Rule Analysis",
            "View full analysis",
            "SECRET-FREE FULL RULE REPORT",
        ):
            self.assertIn(expected, response.text)
        self.assertIn("<details>", response.text)

    async def test_unknown_or_failed_stock_is_404(self) -> None:
        async with self.client(FakeManager(snapshot(RunStatus.COMPLETED, completed_result()))) as client:
            self.assertEqual((await client.get("/stocks/UNKNOWN")).status_code, 404)
            self.assertEqual((await client.get("/stocks/FAILED")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
