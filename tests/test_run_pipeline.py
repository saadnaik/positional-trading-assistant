from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from automation.compare_screens import CandidateSignal, SignalStatus
from automation.run_pipeline import (
    CandidateFailure,
    CppEvaluationError,
    EvaluatedCandidate,
    PipelineOutcome,
    _parse_args,
    _print_outcome,
    _process_candidates_in_context,
    _run_pipeline_outcome,
    evaluate_with_cpp,
    process_candidate,
    resolve_latest_inputs,
    run_pipeline,
)


class CppEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.executable = self.directory / "stock_reader"
        self.executable.touch()
        self.executable.chmod(0o700)
        self.json_path = self.directory / "AAA.json"
        self.json_path.write_text(json.dumps({"symbol": "AAA"}), encoding="utf-8")

    def completed(
        self,
        stdout: str = "Symbol: AAA\nDecision: PASS\nViolations: 2/12\n",
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> Mock:
        result = Mock()
        result.stdout = stdout
        result.stderr = stderr
        result.returncode = returncode
        return result

    def evaluate(self, completed: Mock) -> tuple[str, int, int]:
        with patch("automation.run_pipeline.subprocess.run", return_value=completed):
            return evaluate_with_cpp(self.executable, self.json_path)

    def test_valid_pass_output(self) -> None:
        self.assertEqual(self.evaluate(self.completed()), ("PASS", 2, 12))

    def test_valid_reject_output(self) -> None:
        output = "Symbol: AAA\nDecision: REJECT\nViolations: 7/12\n"
        self.assertEqual(
            self.evaluate(self.completed(output)), ("REJECT", 7, 12)
        )

    def test_subprocess_uses_argument_list_and_no_shell(self) -> None:
        with patch(
            "automation.run_pipeline.subprocess.run", return_value=self.completed()
        ) as run:
            evaluate_with_cpp(self.executable, self.json_path)
        run.assert_called_once_with(
            [str(self.executable), str(self.json_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_malformed_violations_line_is_rejected(self) -> None:
        output = "Symbol: AAA\nDecision: PASS\nViolations: two of twelve\n"
        with self.assertRaisesRegex(CppEvaluationError, "Violations"):
            self.evaluate(self.completed(output))

    def test_wrong_total_is_rejected(self) -> None:
        output = "Symbol: AAA\nDecision: PASS\nViolations: 2/11\n"
        with self.assertRaisesRegex(CppEvaluationError, "X/12"):
            self.evaluate(self.completed(output))

    def test_missing_summary_line_is_rejected(self) -> None:
        output = "Symbol: AAA\nViolations: 2/12\n"
        with self.assertRaisesRegex(CppEvaluationError, "Decision"):
            self.evaluate(self.completed(output))

    def test_duplicate_summary_line_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nSymbol: AAA\nDecision: PASS\nViolations: 2/12\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "duplicate"):
            self.evaluate(self.completed(output))

    def test_invalid_decision_is_rejected(self) -> None:
        output = "Symbol: AAA\nDecision: MAYBE\nViolations: 2/12\n"
        with self.assertRaisesRegex(CppEvaluationError, "PASS or REJECT"):
            self.evaluate(self.completed(output))

    def test_symbol_mismatch_is_rejected(self) -> None:
        output = "Symbol: BBB\nDecision: PASS\nViolations: 2/12\n"
        with self.assertRaisesRegex(CppEvaluationError, "symbol mismatch"):
            self.evaluate(self.completed(output))

    def test_missing_executable_is_rejected(self) -> None:
        with self.assertRaisesRegex(CppEvaluationError, "does not exist"):
            evaluate_with_cpp(self.directory / "missing", self.json_path)

    def test_non_runnable_executable_is_rejected(self) -> None:
        self.executable.chmod(0o600)
        with self.assertRaisesRegex(CppEvaluationError, "not runnable"):
            evaluate_with_cpp(self.executable, self.json_path)

    def test_nonzero_exit_retains_stdout_and_stderr(self) -> None:
        completed = self.completed(
            "partial stdout", returncode=1, stderr="reader exploded"
        )
        with self.assertRaises(CppEvaluationError) as raised:
            self.evaluate(completed)
        message = str(raised.exception)
        self.assertIn("partial stdout", message)
        self.assertIn("reader exploded", message)
        self.assertIn("exit status: 1", message)

    def test_json_symbol_mismatch_is_rejected_before_subprocess(self) -> None:
        self.json_path.write_text(json.dumps({"symbol": "BBB"}), encoding="utf-8")
        with patch("automation.run_pipeline.subprocess.run") as run:
            with self.assertRaisesRegex(CppEvaluationError, "symbol mismatch"):
                from automation.run_pipeline import _evaluate_with_cpp_for_symbol

                _evaluate_with_cpp_for_symbol(
                    self.executable, self.json_path, "AAA"
                )
        run.assert_not_called()


class PipelineOrchestrationTests(unittest.TestCase):
    def candidate(
        self, symbol: str, status: SignalStatus = SignalStatus.NO
    ) -> CandidateSignal:
        return CandidateSignal(symbol, f"{symbol} Company", status)

    def evaluated(self, candidate: CandidateSignal) -> EvaluatedCandidate:
        return EvaluatedCandidate(
            symbol=candidate.symbol,
            company_name=candidate.company_name,
            minervini_1_month=candidate.minervini_1_month,
            json_path=Path(f"{candidate.symbol}.json"),
            fundamental_decision="PASS",
            violation_count=1,
            total_rules=12,
        )

    def test_fresh_page_per_candidate_preserves_order_and_continues(self) -> None:
        first = self.candidate("FIRST", SignalStatus.YES)
        second = self.candidate("SECOND", SignalStatus.NO)
        first_page = Mock()
        second_page = Mock()
        context = Mock()
        context.new_page.side_effect = [first_page, second_page]

        def process(page: Mock, candidate: CandidateSignal, executable: Path) -> EvaluatedCandidate:
            if candidate.symbol == "FIRST":
                raise RuntimeError("first failed")
            return self.evaluated(candidate)

        with patch("automation.run_pipeline.process_candidate", side_effect=process), patch(
            "automation.run_pipeline._save_candidate_diagnostics",
            return_value=(Path("first.png"), Path("first.html")),
        ):
            outcome = _process_candidates_in_context(
                context, (first, second), Path("stock_reader")
            )

        self.assertEqual([result.symbol for result in outcome.results], ["FIRST", "SECOND"])
        self.assertIsInstance(outcome.results[0], CandidateFailure)
        self.assertIsInstance(outcome.results[1], EvaluatedCandidate)
        self.assertEqual(outcome.results[0].minervini_1_month, SignalStatus.YES)
        first_page.close.assert_called_once_with()
        second_page.close.assert_called_once_with()

    def test_run_pipeline_applies_limit_exactly_and_returns_successes(self) -> None:
        candidates = tuple(self.candidate(name) for name in ("ONE", "TWO", "THREE"))
        outcome = PipelineOutcome(
            requested=2,
            results=(self.evaluated(candidates[0]), self.evaluated(candidates[1])),
        )
        with patch(
            "automation.run_pipeline._run_pipeline_outcome", return_value=outcome
        ) as runner:
            results = run_pipeline(
                Path("primary.csv"), Path("minervini.csv"), Path("reader"), limit=2
            )
        self.assertEqual([result.symbol for result in results], ["ONE", "TWO"])
        runner.assert_called_once_with(
            Path("primary.csv"), Path("minervini.csv"), Path("reader"), limit=2
        )

    def test_internal_runner_selects_only_first_two_in_primary_order(self) -> None:
        candidates = tuple(self.candidate(name) for name in ("ONE", "TWO", "THREE"))
        captured: list[CandidateSignal] = []
        fake_outcome = PipelineOutcome(requested=2, results=())
        playwright = Mock()
        browser = playwright.chromium.launch.return_value
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)

        def process(context: object, selected: tuple[CandidateSignal, ...], executable: Path) -> PipelineOutcome:
            captured.extend(selected)
            return fake_outcome

        with patch("automation.run_pipeline.compare_screens", return_value=candidates), patch(
            "automation.run_pipeline.AUTH_STATE"
        ) as auth_state, patch(
            "automation.run_pipeline.sync_playwright", return_value=manager
        ), patch(
            "automation.run_pipeline._process_candidates_in_context", side_effect=process
        ):
            auth_state.is_file.return_value = True
            _run_pipeline_outcome(
                Path("primary.csv"), None, Path("reader"), limit=2
            )
        self.assertEqual([candidate.symbol for candidate in captured], ["ONE", "TWO"])
        playwright.chromium.launch.assert_called_once_with(headless=False)
        browser.new_context.assert_called_once_with(storage_state=auth_state)
        browser.close.assert_called_once_with()

    def test_limit_zero_and_negative_are_rejected(self) -> None:
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "greater than zero"):
                    _run_pipeline_outcome(
                        Path("primary.csv"), None, Path("reader"), limit=limit
                    )
                with self.assertRaises(SystemExit):
                    _parse_args(
                        [
                            "--primary", "primary.csv",
                            "--cpp-executable", "reader",
                            "--limit", str(limit),
                        ]
                    )

    def test_process_candidate_carries_signal_without_altering_cpp_result(self) -> None:
        page = Mock()
        stock = Mock(symbol="AAA")
        json_path = Path("AAA.json")
        for status in (SignalStatus.YES, SignalStatus.NO, SignalStatus.UNKNOWN):
            with self.subTest(status=status), patch(
                "automation.run_pipeline.confirm_stock_page"
            ), patch(
                "automation.run_pipeline.extract_stock_data", return_value=stock
            ), patch(
                "automation.run_pipeline.write_stock_json", return_value=json_path
            ), patch(
                "automation.run_pipeline._evaluate_with_cpp_for_symbol",
                return_value=("REJECT", 7, 12),
            ):
                result = process_candidate(
                    page, self.candidate("AAA", status), Path("reader")
                )
            self.assertEqual(result.minervini_1_month, status)
            self.assertEqual(
                (result.fundamental_decision, result.violation_count),
                ("REJECT", 7),
            )
        page.wait_for_function.assert_called_with(
            "document.title !== 'Stock Share Price - MarketSmith India'",
            timeout=60_000,
        )

    def test_summary_counts(self) -> None:
        success = self.evaluated(self.candidate("GOOD"))
        failure = CandidateFailure(
            "BAD", "Bad Company", SignalStatus.YES, "failed", None, None
        )
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(2, (success, failure)))
        text = output.getvalue()
        self.assertIn("Candidates requested: 2", text)
        self.assertIn("Successful: 1", text)
        self.assertIn("Failed: 1", text)
        self.assertIn("Status: FAILED", text)

    def test_resolve_latest_inputs_only_uses_explicit_directories(self) -> None:
        primary_directory = Path("dedicated-primary")
        minervini_directory = Path("dedicated-minervini")
        with patch(
            "automation.run_pipeline.latest_csv",
            side_effect=[Path("primary.csv"), Path("minervini.csv")],
        ) as latest:
            result = resolve_latest_inputs(primary_directory, minervini_directory)
        self.assertEqual(result, (Path("primary.csv"), Path("minervini.csv")))
        self.assertEqual(
            [call.args[0] for call in latest.call_args_list],
            [primary_directory, minervini_directory],
        )

    def test_no_shared_root_scan_when_explicit_files_are_used(self) -> None:
        from automation.run_pipeline import _resolve_cli_inputs

        args = _parse_args(
            [
                "--primary", "primary.csv",
                "--minervini", "minervini.csv",
                "--cpp-executable", "reader",
            ]
        )
        with patch("automation.run_pipeline.latest_csv") as latest:
            result = _resolve_cli_inputs(args)
        self.assertEqual(result, (Path("primary.csv"), Path("minervini.csv")))
        latest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
