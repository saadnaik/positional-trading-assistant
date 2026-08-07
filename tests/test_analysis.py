from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from automation.analysis import AnalysisPhase, run_complete_analysis
from automation.export_screen import BUILD_YOUR_SCREEN, MINERVINI_1_MONTH
from automation.run_pipeline import PipelineOutcome


class CompleteAnalysisTests(unittest.TestCase):
    def test_fresh_exports_feed_exact_paths_and_all_candidates(self) -> None:
        primary = Path("fresh-primary.csv")
        minervini = Path("fresh-minervini.csv")
        exporter = Mock(side_effect=[primary, minervini])
        progress = []
        outcome = PipelineOutcome(requested=3, results=())
        with patch("automation.analysis.run_pipeline_outcome", return_value=outcome) as pipeline:
            result = run_complete_analysis(
                Path("reader"), exporter=exporter, progress_callback=progress.append
            )

        self.assertEqual(
            exporter.call_args_list,
            [
                unittest.mock.call(BUILD_YOUR_SCREEN, pause_on_failure=False),
                unittest.mock.call(MINERVINI_1_MONTH, pause_on_failure=False),
            ],
        )
        args, kwargs = pipeline.call_args
        self.assertEqual(args, (primary, minervini, Path("reader")))
        self.assertIsNone(kwargs["limit"])
        self.assertTrue(callable(kwargs["progress_callback"]))
        self.assertEqual(result.primary_csv, primary)
        self.assertEqual(result.minervini_csv, minervini)
        self.assertEqual(result.candidates_requested, 3)
        self.assertTrue(result.started_at.tzinfo)
        self.assertTrue(result.completed_at.tzinfo)
        self.assertEqual(progress[0].phase, AnalysisPhase.EXPORTING_PRIMARY)
        self.assertEqual(progress[-1].phase, AnalysisPhase.COMPLETED)

    def test_primary_export_failure_stops_without_stale_fallback(self) -> None:
        exporter = Mock(side_effect=RuntimeError("primary export failed"))
        with patch("automation.analysis.run_pipeline_outcome") as pipeline:
            with self.assertRaisesRegex(RuntimeError, "primary export failed"):
                run_complete_analysis(exporter=exporter)
        exporter.assert_called_once_with(BUILD_YOUR_SCREEN, pause_on_failure=False)
        pipeline.assert_not_called()

    def test_minervini_export_failure_stops_without_pipeline(self) -> None:
        exporter = Mock(side_effect=[Path("primary.csv"), RuntimeError("signal failed")])
        with patch("automation.analysis.run_pipeline_outcome") as pipeline:
            with self.assertRaisesRegex(RuntimeError, "signal failed"):
                run_complete_analysis(exporter=exporter)
        self.assertEqual(exporter.call_count, 2)
        pipeline.assert_not_called()

    def test_candidate_progress_is_forwarded_without_interpretation(self) -> None:
        exporter = Mock(side_effect=[Path("a.csv"), Path("b.csv")])
        observed = []

        def pipeline(*args, **kwargs):
            kwargs["progress_callback"]("AAA", 0, 2)
            kwargs["progress_callback"]("AAA", 1, 2)
            kwargs["progress_callback"]("BBB", 2, 2)
            return PipelineOutcome(2, ())

        with patch("automation.analysis.run_pipeline_outcome", side_effect=pipeline):
            run_complete_analysis(exporter=exporter, progress_callback=observed.append)
        candidate_updates = [p for p in observed if p.phase == AnalysisPhase.ANALYZING_CANDIDATES]
        self.assertEqual(
            [(p.current_symbol, p.processed, p.total) for p in candidate_updates],
            [("AAA", 0, 2), ("AAA", 1, 2), ("BBB", 2, 2)],
        )


if __name__ == "__main__":
    unittest.main()
