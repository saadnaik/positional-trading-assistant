from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from automation.compare_screens import CandidateSignal, SignalStatus, combine_minervini_statuses
from automation.run_pipeline import (
    CandidateCategory,
    CandidateFailure,
    CppEvaluationSummary,
    CppEvaluationError,
    EvaluatedCandidate,
    PipelineOutcome,
    ViolationSummary,
    _parse_args,
    _print_outcome,
    _process_candidates_in_context,
    _run_pipeline_outcome,
    evaluate_with_cpp,
    parse_violation_summaries,
    process_candidate,
    rank_candidates,
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
        stdout: str = (
            "Symbol: AAA\n\n"
            "Fundamental Evaluation: PASS\n"
            "Violations: 2/12\n\n"
            "Weighted Positional Score: 77.2/100\n"
            "\nR1 PASS\nfull detailed output\n"
        ),
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> Mock:
        result = Mock()
        result.stdout = stdout
        result.stderr = stderr
        result.returncode = returncode
        return result

    def evaluate(self, completed: Mock) -> CppEvaluationSummary:
        with patch("automation.run_pipeline.subprocess.run", return_value=completed):
            return evaluate_with_cpp(self.executable, self.json_path)

    def test_valid_pass_output(self) -> None:
        summary = self.evaluate(self.completed())
        self.assertEqual(summary.symbol, "AAA")
        self.assertEqual(summary.fundamental_decision, "PASS")
        self.assertEqual((summary.violation_count, summary.total_rules), (2, 12))
        self.assertEqual(summary.weighted_positional_score, "77.2")

    def test_valid_reject_output(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: REJECT\n"
            "Violations: 7/12\nWeighted Positional Score: 52.4/100\n"
        )
        summary = self.evaluate(self.completed(output))
        self.assertEqual(summary.fundamental_decision, "REJECT")
        self.assertEqual(summary.violation_count, 7)
        self.assertEqual(summary.weighted_positional_score, "52.4")

    def test_zero_score_is_accepted(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: REJECT\n"
            "Violations: 12/12\nWeighted Positional Score: 0.0/100\n"
        )
        self.assertEqual(
            self.evaluate(self.completed(output)).weighted_positional_score, "0.0"
        )

    def test_one_hundred_score_is_accepted(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: PASS\n"
            "Violations: 0/12\nWeighted Positional Score: 100.0/100\n"
        )
        self.assertEqual(
            self.evaluate(self.completed(output)).weighted_positional_score, "100.0"
        )

    def test_score_above_one_hundred_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: PASS\n"
            "Violations: 0/12\nWeighted Positional Score: 100.1/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "within 0..100"):
            self.evaluate(self.completed(output))

    def test_negative_score_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: REJECT\n"
            "Violations: 12/12\nWeighted Positional Score: -0.1/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "Weighted Positional Score"):
            self.evaluate(self.completed(output))

    def test_missing_score_is_rejected(self) -> None:
        output = "Symbol: AAA\nFundamental Evaluation: PASS\nViolations: 2/12\n"
        with self.assertRaisesRegex(CppEvaluationError, "Weighted Positional Score"):
            self.evaluate(self.completed(output))

    def test_malformed_and_integer_only_scores_are_rejected(self) -> None:
        for score in ("77x2", "77", "77.", ".2"):
            with self.subTest(score=score):
                output = (
                    "Symbol: AAA\nFundamental Evaluation: PASS\n"
                    f"Violations: 2/12\nWeighted Positional Score: {score}/100\n"
                )
                with self.assertRaisesRegex(
                    CppEvaluationError, "Weighted Positional Score"
                ):
                    self.evaluate(self.completed(output))

    def test_duplicate_score_line_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: PASS\nViolations: 2/12\n"
            "Weighted Positional Score: 77.2/100\n"
            "Weighted Positional Score: malformed/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "duplicate"):
            self.evaluate(self.completed(output))

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
        output = (
            "Symbol: AAA\nFundamental Evaluation: PASS\n"
            "Violations: two of twelve\nWeighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "Violations"):
            self.evaluate(self.completed(output))

    def test_wrong_total_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: PASS\n"
            "Violations: 2/11\nWeighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "X/12"):
            self.evaluate(self.completed(output))

    def test_missing_fundamental_evaluation_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nViolations: 2/12\n"
            "Weighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "Fundamental Evaluation"):
            self.evaluate(self.completed(output))

    def test_obsolete_decision_only_output_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nDecision: PASS\nViolations: 2/12\n"
            "Weighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "Fundamental Evaluation"):
            self.evaluate(self.completed(output))

    def test_duplicate_summary_line_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nSymbol: AAA\nFundamental Evaluation: PASS\n"
            "Violations: 2/12\nWeighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "duplicate"):
            self.evaluate(self.completed(output))

    def test_invalid_fundamental_evaluation_is_rejected(self) -> None:
        output = (
            "Symbol: AAA\nFundamental Evaluation: MAYBE\n"
            "Violations: 2/12\nWeighted Positional Score: 77.2/100\n"
        )
        with self.assertRaisesRegex(CppEvaluationError, "Fundamental Evaluation"):
            self.evaluate(self.completed(output))

    def test_symbol_mismatch_is_rejected(self) -> None:
        output = (
            "Symbol: BBB\nFundamental Evaluation: PASS\n"
            "Violations: 2/12\nWeighted Positional Score: 77.2/100\n"
        )
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

    def test_success_retains_full_stdout_verbatim(self) -> None:
        stdout = self.completed().stdout
        self.assertEqual(self.evaluate(self.completed(stdout)).stdout, stdout)

    def test_json_symbol_mismatch_is_rejected_before_subprocess(self) -> None:
        self.json_path.write_text(json.dumps({"symbol": "BBB"}), encoding="utf-8")
        with patch("automation.run_pipeline.subprocess.run") as run:
            with self.assertRaisesRegex(CppEvaluationError, "symbol mismatch"):
                from automation.run_pipeline import _evaluate_with_cpp_for_symbol

                _evaluate_with_cpp_for_symbol(
                    self.executable, self.json_path, "AAA"
                )
        run.assert_not_called()


class ViolationSummaryTests(unittest.TestCase):
    def report(self) -> str:
        return (
            "Symbol: AAA\nFundamental Evaluation: REJECT\nViolations: 2/12\n"
            "Weighted Positional Score: 77.2/100\n"
            "\n--------------------------------------------------\n"
            "R1 VIOLATION\nQuarterly EPS growth trend\nWeight: 10\n\n"
            "Actual values:\nOld: +100%\nNew: +50%\n"
            "Requirement:\noldest <= latest\n"
            "Closeness / distance:\nLeg 1: FAIL\n"
            "Scoring calculation:\nCredit copied from C++\n"
            "Weighted credit: 0.500 (50.0%)\n"
            "Contribution: 5.000 / 10\n"
            "Reason:\nC++ says the trend violates.\n"
            "\n--------------------------------------------------\n"
            "R2 PASS\nQuarterly sales growth trend\nWeight: 8\n"
            "\n--------------------------------------------------\n"
            "R10 VIOLATION\nLatest annual EPS highest\nWeight: 7\n\n"
            "Actual values:\nOld: 10\nLatest: 8\n"
            "Requirement:\nlatest >= oldest\n"
            "Weighted credit: 0.800 (80.0%)\n"
            "Contribution: 5.600 / 7\n"
            "Reason:\nC++ says latest is not highest.\n"
        )

    def test_violation_blocks_are_selected_in_cpp_order_and_pass_is_excluded(self) -> None:
        summaries = parse_violation_summaries(self.report())
        self.assertEqual([item.rule_id for item in summaries], ["R1", "R10"])

    def test_structured_fields_and_raw_blocks_are_retained(self) -> None:
        first = parse_violation_summaries(self.report())[0]
        self.assertEqual(first.description, "Quarterly EPS growth trend")
        self.assertEqual(first.weight, "10")
        self.assertEqual(first.actual, "Old: +100%\nNew: +50%")
        self.assertEqual(first.requirement, "oldest <= latest")
        self.assertEqual(first.distance, "Leg 1: FAIL")
        self.assertEqual(first.credit, "0.500 (50.0%)")
        self.assertEqual(first.contribution, "5.000 / 10")
        self.assertEqual(first.reason, "C++ says the trend violates.")
        self.assertTrue(first.raw_block.startswith("R1 VIOLATION\n"))
        self.assertIn("Credit copied from C++", first.raw_block)

    def test_missing_optional_fields_retains_violation_without_failure(self) -> None:
        stdout = (
            "summary\n--------------------------------------------------\n"
            "R4 VIOLATION\nTruncated C++ details\n"
        )
        summaries = parse_violation_summaries(stdout)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].rule_id, "R4")
        self.assertIsNone(summaries[0].weight)
        self.assertEqual(summaries[0].raw_block, "R4 VIOLATION\nTruncated C++ details\n")

    def test_unrecognized_or_malformed_headers_are_ignored(self) -> None:
        stdout = (
            "summary\n--------------------------------------------------\n"
            "R13 VIOLATION\nOut of range\n"
            "\n--------------------------------------------------\n"
            "R4 MAYBE\nNot a C++ status\n"
        )
        self.assertEqual(parse_violation_summaries(stdout), ())


class PipelineOrchestrationTests(unittest.TestCase):
    def candidate(
        self, symbol: str, status: SignalStatus = SignalStatus.NO,
        five_months: SignalStatus = SignalStatus.NO,
    ) -> CandidateSignal:
        return CandidateSignal(
            symbol, f"{symbol} Company", status, five_months,
            combine_minervini_statuses(status, five_months),
        )

    def evaluated(self, candidate: CandidateSignal) -> EvaluatedCandidate:
        cpp_stdout = (
            f"Symbol: {candidate.symbol}\n"
            "Fundamental Evaluation: PASS\n"
            "Violations: 1/12\n"
            "Weighted Positional Score: 88.8/100\n"
            "R1 PASS\nverbatim details\n"
        )
        return EvaluatedCandidate(
            symbol=candidate.symbol,
            company_name=candidate.company_name,
            minervini_1_month=candidate.minervini_1_month,
            minervini_5_months=candidate.minervini_5_months,
            minervini_overall=candidate.minervini_overall,
            json_path=Path(f"{candidate.symbol}.json"),
            fundamental_decision="PASS",
            violation_count=1,
            total_rules=12,
            weighted_positional_score="88.8",
            cpp_stdout=cpp_stdout,
            violations_summary=(
                ViolationSummary(
                    rule_id="R1",
                    description="Test violation",
                    raw_block="R1 VIOLATION\nTest violation\n",
                ),
            ),
        )

    def ranked_candidate(
        self,
        symbol: str,
        decision: str,
        status: SignalStatus,
        score: str,
        violations: int = 1,
    ) -> EvaluatedCandidate:
        candidate = self.evaluated(self.candidate(symbol, status))
        return EvaluatedCandidate(
            **{
                **candidate.__dict__,
                "fundamental_decision": decision,
                "weighted_positional_score": score,
                "violation_count": violations,
            }
        )

    def test_ranking_categories_scores_ties_and_global_ranks(self) -> None:
        candidates = (
            self.ranked_candidate("REJECT_HIGH", "REJECT", SignalStatus.YES, "99.9"),
            self.ranked_candidate("WON_NO_LOW", "PASS", SignalStatus.NO, "10.0"),
            self.ranked_candidate("WON_YES_LOW", "PASS", SignalStatus.YES, "1.0"),
            self.ranked_candidate("WON_YES_HIGH", "PASS", SignalStatus.YES, "2.0"),
            self.ranked_candidate("TIE_FIRST", "PASS", SignalStatus.NO, "50.0", 12),
            self.ranked_candidate("TIE_SECOND", "PASS", SignalStatus.UNKNOWN, "50.0", 0),
            self.ranked_candidate("REJECT_LOW", "REJECT", SignalStatus.NO, "20.0"),
        )
        original = tuple(candidates)

        ranked = rank_candidates(candidates)

        self.assertEqual(
            [item.candidate.symbol for item in ranked],
            [
                "WON_YES_HIGH",
                "WON_YES_LOW",
                "TIE_FIRST",
                "TIE_SECOND",
                "WON_NO_LOW",
                "REJECT_HIGH",
                "REJECT_LOW",
            ],
        )
        self.assertEqual([item.rank for item in ranked], list(range(1, 8)))
        self.assertEqual(
            [item.category for item in ranked],
            [
                CandidateCategory.WON_AND_MINERVINI,
                CandidateCategory.WON_AND_MINERVINI,
                CandidateCategory.WON_ONLY,
                CandidateCategory.WON_ONLY,
                CandidateCategory.WON_ONLY,
                CandidateCategory.WON_REJECTED,
                CandidateCategory.WON_REJECTED,
            ],
        )
        self.assertEqual(ranked[3].candidate.minervini_1_month, SignalStatus.UNKNOWN)
        self.assertEqual(candidates, original)

    def test_violation_count_does_not_affect_equal_score_tie(self) -> None:
        first = self.ranked_candidate("FIRST", "REJECT", SignalStatus.NO, "70.0", 12)
        second = self.ranked_candidate("SECOND", "REJECT", SignalStatus.NO, "70.0", 1)
        self.assertEqual(
            [item.candidate.symbol for item in rank_candidates((first, second))],
            ["FIRST", "SECOND"],
        )

    def test_reject_with_minervini_yes_remains_rejected_category(self) -> None:
        candidate = self.ranked_candidate("REJECT", "REJECT", SignalStatus.YES, "100.0")
        ranked = rank_candidates((candidate,))
        self.assertEqual(ranked[0].category, CandidateCategory.WON_REJECTED)

    def test_five_month_yes_places_pass_in_minervini_category(self) -> None:
        candidate = self.evaluated(
            self.candidate("FIVE_MONTH", SignalStatus.NO, SignalStatus.YES)
        )
        ranked = rank_candidates((candidate,))
        self.assertEqual(candidate.minervini_overall, SignalStatus.YES)
        self.assertEqual(ranked[0].category, CandidateCategory.WON_AND_MINERVINI)

    def test_ranked_output_handles_empty_categories(self) -> None:
        candidate = self.ranked_candidate("ONLY", "REJECT", SignalStatus.NO, "50.0")
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (candidate,)))
        text = output.getvalue()
        self.assertEqual(text.count("No candidates."), 2)

    def test_detailed_reports_follow_ranked_order_but_source_table_does_not(self) -> None:
        rejected = self.ranked_candidate("SOURCE_FIRST", "REJECT", SignalStatus.NO, "99.0")
        passed = self.ranked_candidate("SOURCE_SECOND", "PASS", SignalStatus.NO, "10.0")
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(2, (rejected, passed)))
        text = output.getvalue()
        source_table_start = text.index("Symbol       1M")
        ranked_start = text.index("## Ranked Potential Candidates")
        source_table = text[source_table_start:ranked_start]
        self.assertLess(source_table.index("SOURCE_FIRST"), source_table.index("SOURCE_SECOND"))
        details_start = text.index("SOURCE_SECOND — SOURCE_SECOND Company")
        self.assertLess(
            details_start,
            text.index("SOURCE_FIRST — SOURCE_FIRST Company", details_start),
        )
        self.assertIn(passed.cpp_stdout, text)
        self.assertIn(rejected.cpp_stdout, text)

    def test_failed_candidates_do_not_enter_ranking(self) -> None:
        success = self.ranked_candidate("GOOD", "PASS", SignalStatus.NO, "50.0")
        failure = CandidateFailure("BAD", "Bad", SignalStatus.YES, SignalStatus.NO, SignalStatus.YES, "failed", None, None)
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(2, (failure, success)))
        ranked_section = output.getvalue().split("## Ranked Potential Candidates", 1)[1]
        self.assertIn("GOOD", ranked_section)
        self.assertNotIn("BAD", ranked_section.split("Candidate failures:", 1)[0])

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
        candidates = tuple(
            self.candidate(name)
            for name in ("ONE", "TWO", "THREE", "FOUR", "FIVE")
        )
        outcome = PipelineOutcome(
            requested=5,
            results=tuple(self.evaluated(candidate) for candidate in candidates),
        )
        with patch(
            "automation.run_pipeline._run_pipeline_outcome", return_value=outcome
        ) as runner:
            results = run_pipeline(
                Path("primary.csv"), Path("minervini.csv"), Path("five.csv"), Path("reader"), limit=5
            )
        self.assertEqual(
            [result.symbol for result in results],
            ["ONE", "TWO", "THREE", "FOUR", "FIVE"],
        )
        runner.assert_called_once_with(
            Path("primary.csv"), Path("minervini.csv"), Path("five.csv"), Path("reader"), limit=5
        )

    def test_internal_runner_selects_only_first_five_in_primary_order(self) -> None:
        candidates = tuple(
            self.candidate(name)
            for name in ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX")
        )
        captured: list[CandidateSignal] = []
        fake_outcome = PipelineOutcome(requested=5, results=())
        playwright = Mock()
        browser = playwright.chromium.launch.return_value
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)

        def process(context: object, selected: tuple[CandidateSignal, ...], executable: Path) -> PipelineOutcome:
            captured.extend(selected)
            return fake_outcome

        with patch.dict("os.environ", {"FALCON_BROWSER_HEADLESS": "true"}, clear=False), patch("automation.run_pipeline.compare_screens", return_value=candidates), patch(
            "automation.run_pipeline.AUTH_STATE"
        ) as auth_state, patch(
            "automation.run_pipeline.sync_playwright", return_value=manager
        ), patch(
            "automation.run_pipeline._process_candidates_in_context", side_effect=process
        ):
            auth_state.is_file.return_value = True
            _run_pipeline_outcome(
                Path("primary.csv"), None, None, Path("reader"), limit=5
            )
        self.assertEqual(
            [candidate.symbol for candidate in captured],
            ["ONE", "TWO", "THREE", "FOUR", "FIVE"],
        )
        playwright.chromium.launch.assert_called_once_with(headless=True)
        browser.new_context.assert_called_once_with(storage_state=auth_state)
        browser.close.assert_called_once_with()

    def test_all_selects_every_dynamic_primary_candidate_in_order(self) -> None:
        candidates = tuple(
            self.candidate(name)
            for name in ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX")
        )
        captured: list[CandidateSignal] = []
        playwright = Mock()
        browser = playwright.chromium.launch.return_value
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)

        def process(
            context: object,
            selected: tuple[CandidateSignal, ...],
            executable: Path,
        ) -> PipelineOutcome:
            captured.extend(selected)
            return PipelineOutcome(requested=len(selected), results=())

        with patch(
            "automation.run_pipeline.compare_screens", return_value=candidates
        ), patch("automation.run_pipeline.AUTH_STATE") as auth_state, patch(
            "automation.run_pipeline.sync_playwright", return_value=manager
        ), patch(
            "automation.run_pipeline._process_candidates_in_context",
            side_effect=process,
        ):
            auth_state.is_file.return_value = True
            outcome = _run_pipeline_outcome(
                Path("primary.csv"), None, None, Path("reader"), limit=None
            )
        self.assertEqual(outcome.requested, 6)
        self.assertEqual(
            [candidate.symbol for candidate in captured],
            ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX"],
        )

    def test_limit_zero_and_negative_are_rejected(self) -> None:
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "greater than zero"):
                    _run_pipeline_outcome(
                        Path("primary.csv"), None, None, Path("reader"), limit=limit
                    )
                with self.assertRaises(SystemExit):
                    _parse_args(
                        [
                            "--primary", "primary.csv",
                            "--cpp-executable", "reader",
                            "--limit", str(limit),
                        ]
                    )

    def test_default_cli_limit_is_five(self) -> None:
        args = _parse_args(
            ["--primary", "primary.csv", "--cpp-executable", "reader"]
        )
        self.assertEqual(args.limit, 5)
        self.assertFalse(args.all)

    def test_all_cli_uses_none_limit_and_is_mutually_exclusive(self) -> None:
        args = _parse_args(
            ["--primary", "primary.csv", "--cpp-executable", "reader", "--all"]
        )
        self.assertTrue(args.all)
        self.assertIsNone(args.limit)
        with self.assertRaises(SystemExit):
            _parse_args(
                [
                    "--primary", "primary.csv",
                    "--cpp-executable", "reader",
                    "--all",
                    "--limit", "5",
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
                return_value=CppEvaluationSummary(
                    symbol="AAA",
                    fundamental_decision="REJECT",
                    violation_count=7,
                    total_rules=12,
                    weighted_positional_score="77.2",
                    stdout="complete C++ report\n",
                    violations_summary=(),
                ),
            ):
                result = process_candidate(
                    page, self.candidate("AAA", status), Path("reader")
                )
            self.assertEqual(result.minervini_1_month, status)
            self.assertEqual(result.minervini_5_months, SignalStatus.NO)
            self.assertEqual(result.minervini_overall, combine_minervini_statuses(status, SignalStatus.NO))
            self.assertEqual(
                (result.fundamental_decision, result.violation_count),
                ("REJECT", 7),
            )
            self.assertEqual(result.weighted_positional_score, "77.2")
            self.assertEqual(result.cpp_stdout, "complete C++ report\n")
        page.wait_for_function.assert_called_with(
            "document.title !== 'Stock Share Price - MarketSmith India'",
            timeout=60_000,
        )

    def test_summary_counts(self) -> None:
        success = self.evaluated(self.candidate("GOOD"))
        failure = CandidateFailure(
            "BAD", "Bad Company", SignalStatus.YES, SignalStatus.NO, SignalStatus.YES, "failed", None, None
        )
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(2, (success, failure)))
        text = output.getvalue()
        self.assertIn("Candidates requested: 2", text)
        self.assertIn("Successful: 1", text)
        self.assertIn("Failed: 1", text)
        self.assertIn("Candidate failures:", text)
        self.assertIn("BAD: failed", text)

    def test_table_contains_only_successes_in_result_order(self) -> None:
        first = self.evaluated(self.candidate("FIRST", SignalStatus.YES))
        failure = CandidateFailure(
            "FAILED", "Failed Company", SignalStatus.NO, SignalStatus.NO, SignalStatus.NO, "failed", None, None
        )
        third = self.evaluated(self.candidate("THIRD", SignalStatus.UNKNOWN))
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(3, (first, failure, third)))
        text = output.getvalue()
        table_start = text.index("Symbol       1M")
        table_end = text.index("\n\n", table_start)
        table = text[table_start:table_end]
        self.assertLess(table.index("FIRST"), table.index("THIRD"))
        self.assertNotIn("FAILED", table)
        self.assertIn("88.8", table)

    def test_detailed_reports_use_retained_stdout_verbatim(self) -> None:
        success = self.evaluated(self.candidate("FULL", SignalStatus.NO))
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (success,)))
        text = output.getvalue()
        self.assertIn("FULL — FULL Company", text)
        self.assertIn("Minervini 1-Month: NO", text)
        self.assertIn("Minervini 5-Month: NO", text)
        self.assertIn("Minervini Overall: NO", text)
        self.assertIn(success.cpp_stdout, text)

    def test_compact_summary_precedes_verbatim_full_report(self) -> None:
        success = self.evaluated(self.candidate("ORDERED"))
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (success,)))
        text = output.getvalue()
        self.assertLess(text.index("Violations:\n"), text.index("Full C++ report"))
        self.assertLess(text.index("Full C++ report"), text.index(success.cpp_stdout))

    def test_zero_violation_summary_prints_none(self) -> None:
        candidate = self.evaluated(self.candidate("CLEAN"))
        candidate = EvaluatedCandidate(
            **{
                **candidate.__dict__,
                "violation_count": 0,
                "violations_summary": (),
            }
        )
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (candidate,)))
        self.assertIn("Violations:\n\nNone", output.getvalue())

    def test_missing_optional_fields_prints_fallback(self) -> None:
        candidate = self.evaluated(self.candidate("FALLBACK"))
        candidate = EvaluatedCandidate(
            **{
                **candidate.__dict__,
                "violations_summary": (
                    ViolationSummary("R4", "Truncated", "R4 VIOLATION\nTruncated\n"),
                ),
            }
        )
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (candidate,)))
        self.assertIn("Details available in full report", output.getvalue())

    def test_unparseable_details_do_not_claim_zero_violations(self) -> None:
        candidate = self.evaluated(self.candidate("UNPARSED"))
        candidate = EvaluatedCandidate(
            **{
                **candidate.__dict__,
                "violations_summary": (),
            }
        )
        output = StringIO()
        with redirect_stdout(output):
            _print_outcome(PipelineOutcome(1, (candidate,)))
        text = output.getvalue()
        self.assertIn("Violations:\n\nDetails available in full report", text)

    def test_resolve_latest_inputs_only_uses_explicit_directories(self) -> None:
        primary_directory = Path("dedicated-primary")
        minervini_directory = Path("dedicated-minervini")
        with patch(
            "automation.run_pipeline.latest_csv",
            side_effect=[Path("primary.csv"), Path("minervini.csv"), Path("five.csv")],
        ) as latest:
            result = resolve_latest_inputs(primary_directory, minervini_directory, Path("dedicated-five"))
        self.assertEqual(result, (Path("primary.csv"), Path("minervini.csv"), Path("five.csv")))
        self.assertEqual(
            [call.args[0] for call in latest.call_args_list],
            [primary_directory, minervini_directory, Path("dedicated-five")],
        )

    def test_no_shared_root_scan_when_explicit_files_are_used(self) -> None:
        from automation.run_pipeline import _resolve_cli_inputs

        args = _parse_args(
            [
                "--primary", "primary.csv",
                "--minervini", "minervini.csv",
                "--minervini-5-months", "five.csv",
                "--cpp-executable", "reader",
            ]
        )
        with patch("automation.run_pipeline.latest_csv") as latest:
            result = _resolve_cli_inputs(args)
        self.assertEqual(result, (Path("primary.csv"), Path("minervini.csv"), Path("five.csv")))
        latest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
