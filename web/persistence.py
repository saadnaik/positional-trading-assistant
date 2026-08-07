"""Versioned, atomic persistence for the latest completed Falcon analysis."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from automation.analysis import AnalysisRunResult
from automation.compare_screens import SignalStatus
from automation.run_pipeline import (
    CandidateFailure,
    EvaluatedCandidate,
    ViolationSummary,
    rank_candidates,
)
from web.models import safe_error_message


SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "state" / "latest_analysis.json"
_SCORE_PATTERN = re.compile(r"\d+\.\d+")


class PersistenceError(RuntimeError):
    """Raised when a snapshot cannot be safely serialized or restored."""


class AnalysisResultStore:
    """Store the latest completed result without persisting runtime objects."""

    def __init__(
        self,
        snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
        *,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.project_root = Path(project_root).resolve()

    def load(self) -> AnalysisRunResult | None:
        if not self.snapshot_path.exists():
            return None
        try:
            with self.snapshot_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            return self._decode_result(payload)
        except PersistenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise PersistenceError("The persisted analysis snapshot is unreadable.") from error

    def save(self, result: AnalysisRunResult) -> None:
        payload = self._encode_result(result)
        directory = self.snapshot_path.parent
        temporary_path: Path | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".latest_analysis.", suffix=".tmp", dir=directory
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.snapshot_path)
            temporary_path = None
            self._sync_directory(directory)
        except (OSError, TypeError, ValueError) as error:
            raise PersistenceError("The completed analysis snapshot could not be saved.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _encode_path(self, value: Path | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (self.project_root / path).resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as error:
            raise PersistenceError("A result path is outside the Falcon project.") from error
        return relative.as_posix()

    def _decode_path(self, value: Any, *, optional: bool = False) -> Path | None:
        if value is None and optional:
            return None
        if not isinstance(value, str) or not value:
            raise PersistenceError("A persisted result path is missing or invalid.")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise PersistenceError("A persisted result path is unsafe.")
        resolved = (self.project_root / relative).resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as error:
            raise PersistenceError("A persisted result path escapes the Falcon project.") from error
        return resolved

    @staticmethod
    def _encode_datetime(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PersistenceError("Analysis timestamps must be timezone-aware.")
        return value.isoformat()

    @staticmethod
    def _decode_datetime(value: Any) -> datetime:
        if not isinstance(value, str):
            raise PersistenceError("A persisted analysis timestamp is invalid.")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise PersistenceError("A persisted analysis timestamp is invalid.") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PersistenceError("A persisted analysis timestamp lacks a timezone.")
        return parsed

    @staticmethod
    def _require_dict(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise PersistenceError(f"Persisted {name} data is invalid.")
        return value

    @staticmethod
    def _require_string(value: Any, name: str, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str) or (not allow_empty and not value):
            raise PersistenceError(f"Persisted {name} is invalid.")
        return value

    @staticmethod
    def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise PersistenceError(f"Persisted {name} is invalid.")
        return value

    @staticmethod
    def _signal(value: Any) -> SignalStatus:
        try:
            return SignalStatus(value)
        except (TypeError, ValueError) as error:
            raise PersistenceError("A persisted Minervini status is invalid.") from error

    @staticmethod
    def _optional_text(item: dict[str, Any], name: str) -> str | None:
        text = item.get(name)
        if text is not None and not isinstance(text, str):
            raise PersistenceError(f"Persisted violation {name} is invalid.")
        return text

    def _encode_violation(self, item: ViolationSummary) -> dict[str, Any]:
        return {
            "rule_id": item.rule_id,
            "description": item.description,
            "raw_block": item.raw_block,
            "weight": item.weight,
            "actual": item.actual,
            "requirement": item.requirement,
            "distance": item.distance,
            "credit": item.credit,
            "contribution": item.contribution,
            "reason": item.reason,
        }

    def _decode_violation(self, value: Any) -> ViolationSummary:
        item = self._require_dict(value, "violation")
        return ViolationSummary(
            rule_id=self._require_string(item.get("rule_id"), "violation rule ID"),
            description=self._require_string(
                item.get("description"), "violation description", allow_empty=True
            ),
            raw_block=self._require_string(
                item.get("raw_block"), "violation raw block", allow_empty=True
            ),
            weight=self._optional_text(item, "weight"),
            actual=self._optional_text(item, "actual"),
            requirement=self._optional_text(item, "requirement"),
            distance=self._optional_text(item, "distance"),
            credit=self._optional_text(item, "credit"),
            contribution=self._optional_text(item, "contribution"),
            reason=self._optional_text(item, "reason"),
        )

    def _encode_candidate(self, item: EvaluatedCandidate) -> dict[str, Any]:
        return {
            "symbol": item.symbol,
            "company_name": item.company_name,
            "minervini_1_month": item.minervini_1_month.value,
            "minervini_5_months": item.minervini_5_months.value,
            "minervini_overall": item.minervini_overall.value,
            "json_path": self._encode_path(item.json_path),
            "fundamental_decision": item.fundamental_decision,
            "violation_count": item.violation_count,
            "total_rules": item.total_rules,
            "weighted_positional_score": item.weighted_positional_score,
            "cpp_stdout": item.cpp_stdout,
            "violations_summary": [
                self._encode_violation(violation) for violation in item.violations_summary
            ],
        }

    def _decode_candidate(self, value: Any) -> EvaluatedCandidate:
        item = self._require_dict(value, "candidate")
        decision = self._require_string(
            item.get("fundamental_decision"), "fundamental decision"
        )
        if decision not in {"PASS", "REJECT"}:
            raise PersistenceError("A persisted fundamental decision is invalid.")
        total_rules = self._require_int(item.get("total_rules"), "total rules", minimum=1)
        violation_count = self._require_int(
            item.get("violation_count"), "violation count"
        )
        if violation_count > total_rules:
            raise PersistenceError("A persisted violation count exceeds total rules.")
        score = self._require_string(
            item.get("weighted_positional_score"), "weighted positional score"
        )
        try:
            numeric_score = Decimal(score)
        except InvalidOperation as error:
            raise PersistenceError("A persisted weighted score is invalid.") from error
        if not _SCORE_PATTERN.fullmatch(score) or not Decimal(0) <= numeric_score <= Decimal(100):
            raise PersistenceError("A persisted weighted score is invalid.")
        violations = item.get("violations_summary")
        if not isinstance(violations, list):
            raise PersistenceError("Persisted violation summaries are invalid.")
        return EvaluatedCandidate(
            symbol=self._require_string(item.get("symbol"), "candidate symbol"),
            company_name=self._require_string(item.get("company_name"), "company name"),
            minervini_1_month=self._signal(item.get("minervini_1_month")),
            minervini_5_months=self._signal(item.get("minervini_5_months")),
            minervini_overall=self._signal(item.get("minervini_overall")),
            json_path=self._decode_path(item.get("json_path")),
            fundamental_decision=decision,
            violation_count=violation_count,
            total_rules=total_rules,
            weighted_positional_score=score,
            cpp_stdout=self._require_string(
                item.get("cpp_stdout"), "C++ report", allow_empty=True
            ),
            violations_summary=tuple(self._decode_violation(v) for v in violations),
        )

    def _encode_failure(self, item: CandidateFailure) -> dict[str, Any]:
        return {
            "symbol": item.symbol,
            "company_name": item.company_name,
            "minervini_1_month": item.minervini_1_month.value,
            "minervini_5_months": item.minervini_5_months.value,
            "minervini_overall": item.minervini_overall.value,
            "reason": safe_error_message(item.reason),
            "screenshot_path": self._encode_path(item.screenshot_path),
            "html_path": self._encode_path(item.html_path),
        }

    def _decode_failure(self, value: Any) -> CandidateFailure:
        item = self._require_dict(value, "candidate failure")
        return CandidateFailure(
            symbol=self._require_string(item.get("symbol"), "failed candidate symbol"),
            company_name=self._require_string(
                item.get("company_name"), "failed candidate company"
            ),
            minervini_1_month=self._signal(item.get("minervini_1_month")),
            minervini_5_months=self._signal(item.get("minervini_5_months")),
            minervini_overall=self._signal(item.get("minervini_overall")),
            reason=self._require_string(item.get("reason"), "candidate failure reason"),
            screenshot_path=self._decode_path(item.get("screenshot_path"), optional=True),
            html_path=self._decode_path(item.get("html_path"), optional=True),
        )

    def _encode_result(self, result: AnalysisRunResult) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "analysis": {
                "started_at": self._encode_datetime(result.started_at),
                "completed_at": self._encode_datetime(result.completed_at),
                "primary_csv": self._encode_path(result.primary_csv),
                "minervini_1_month_csv": self._encode_path(
                    result.minervini_1_month_csv
                ),
                "minervini_5_months_csv": self._encode_path(
                    result.minervini_5_months_csv
                ),
                "candidates_requested": result.candidates_requested,
                "successful": [
                    self._encode_candidate(candidate) for candidate in result.successful
                ],
                "failed": [self._encode_failure(failure) for failure in result.failed],
            },
        }

    def _decode_result(self, value: Any) -> AnalysisRunResult:
        root = self._require_dict(value, "snapshot")
        if root.get("schema_version") != SCHEMA_VERSION:
            raise PersistenceError("The persisted analysis schema version is unsupported.")
        analysis = self._require_dict(root.get("analysis"), "analysis")
        successful_data = analysis.get("successful")
        failed_data = analysis.get("failed")
        if not isinstance(successful_data, list) or not isinstance(failed_data, list):
            raise PersistenceError("Persisted candidate collections are invalid.")
        successful = tuple(self._decode_candidate(item) for item in successful_data)
        failed = tuple(self._decode_failure(item) for item in failed_data)
        requested = self._require_int(
            analysis.get("candidates_requested"), "requested candidate count"
        )
        if requested != len(successful) + len(failed):
            raise PersistenceError("Persisted candidate totals are inconsistent.")
        return AnalysisRunResult(
            started_at=self._decode_datetime(analysis.get("started_at")),
            completed_at=self._decode_datetime(analysis.get("completed_at")),
            primary_csv=self._decode_path(analysis.get("primary_csv")),
            minervini_1_month_csv=self._decode_path(
                analysis.get("minervini_1_month_csv")
            ),
            minervini_5_months_csv=self._decode_path(
                analysis.get("minervini_5_months_csv")
            ),
            candidates_requested=requested,
            successful=successful,
            failed=failed,
            ranked=rank_candidates(successful),
        )
