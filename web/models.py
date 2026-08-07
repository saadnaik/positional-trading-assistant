"""Web-facing state models with deliberately limited diagnostic detail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re

from automation.analysis import AnalysisPhase, AnalysisRunResult


class RunStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MarketSmithSessionStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SAVED = "SAVED"
    VALID = "VALID"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class RunSnapshot:
    status: RunStatus
    session_status: MarketSmithSessionStatus
    phase: AnalysisPhase | None
    current_symbol: str | None
    processed: int
    total: int | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    latest_result: AnalysisRunResult | None


def safe_error_message(error: BaseException | str) -> str:
    """Return useful browser-safe failure text without raw diagnostics."""

    text = str(error)
    lowered = text.casefold()
    if "expired" in lowered and ("session" in lowered or "login" in lowered):
        return "MarketSmith authentication has expired. Refresh the saved login session."
    if "export" in lowered or "download" in lowered:
        return "MarketSmith screen export failed. Check the local diagnostics."
    if "c++" in lowered or "stock_reader" in lowered or "subprocess" in lowered:
        return "C++ stock evaluation failed. Check the local diagnostics."
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first_line = re.sub(r"(?:[A-Za-z]:)?[/\\][^\s:]+", "[local path]", first_line)
    first_line = re.sub(
        r"(?i)(password|cookie|authorization|token|storage[_ -]?state)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        first_line,
    )
    return (first_line[:240] or "Analysis failed unexpectedly.")
