"""Small environment-backed runtime settings for Falcon Stocks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STOCK_READER = PROJECT_ROOT / "cpp" / "build" / "stock_reader"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def browser_headless(environment: Mapping[str, str] | None = None) -> bool:
    """Return Falcon's browser mode from a strict, case-insensitive boolean."""

    source = os.environ if environment is None else environment
    raw = source.get("FALCON_BROWSER_HEADLESS")
    if raw is None:
        return False
    normalized = raw.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    accepted = "1, true, yes, on, 0, false, no, off"
    raise ValueError(
        "FALCON_BROWSER_HEADLESS must be one of: " + accepted
    )


def launch_falcon_chromium(playwright):
    """Launch Chromium for a Falcon production workflow."""

    return playwright.chromium.launch(headless=browser_headless())


def stock_reader_path(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve the configured C++ evaluator without machine-specific defaults."""

    source = os.environ if environment is None else environment
    raw = source.get("FALCON_STOCK_READER")
    if raw is None:
        return DEFAULT_STOCK_READER
    configured = raw.strip()
    if not configured:
        raise ValueError("FALCON_STOCK_READER must not be blank")
    return Path(configured).expanduser().resolve()
