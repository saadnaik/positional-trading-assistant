"""Serialize one extracted MarketSmith stock to the versioned JSON schema."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extract.stock import ExtractedStockData


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACTED_DIR = PROJECT_ROOT / "data" / "extracted"
SCHEMA_VERSION = "1.0"


class JsonOutputError(RuntimeError):
    """Raised when extracted stock JSON cannot be written and verified safely."""


def stock_to_json_document(
    stock: "ExtractedStockData", captured_at: datetime
) -> dict[str, object]:
    """Map extracted data explicitly without converting or reordering values."""
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise JsonOutputError("captured_at must include timezone information.")

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at.isoformat(),
        "symbol": stock.symbol,
        "company": stock.company,
        "page_url": stock.page_url,
        "financial_mode": stock.financial_mode,
        "summary": {
            "eps_growth_rate": stock.summary.eps_growth_rate,
            "earnings_stability": stock.summary.earnings_stability,
            "return_on_equity": stock.summary.return_on_equity,
        },
        "quarterly_earnings": [
            {
                "quarter_label": record.quarter_label,
                "quarter_end_date": record.quarter_end_date,
                "eps": record.eps,
                "eps_change_percent": record.eps_change_percent,
                "sales": record.sales,
                "sales_change_percent": record.sales_change_percent,
            }
            for record in stock.quarterly.records
        ],
        "annual_eps": [
            {
                "year_label": record.year_label,
                "fiscal_year_end": record.fiscal_year_end,
                "eps": record.eps,
                "high": record.high,
                "low": record.low,
            }
            for record in stock.annual_eps.records
        ],
        "annual_ratios": [
            {
                "year_label": record.year_label,
                "fiscal_year_end": record.fiscal_year_end,
                "after_tax_margin_percent": record.after_tax_margin_percent,
            }
            for record in stock.annual_ratios.records
        ],
    }


def output_filename(captured_at: datetime, symbol: str) -> str:
    utc_timestamp = captured_at.astimezone(timezone.utc)
    timestamp_text = utc_timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    if not symbol or Path(symbol).name != symbol or symbol in {".", ".."}:
        raise JsonOutputError(
            f"Stock symbol cannot be used safely in an output filename: {symbol!r}."
        )
    return f"{timestamp_text}_{symbol}.json"


def read_and_verify_json(path: Path, expected: dict[str, object]) -> None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            parsed = json.load(stream)
    except (json.JSONDecodeError, OSError) as error:
        raise JsonOutputError(
            f"Could not read completed JSON output {path}: {error}"
        ) from error
    if parsed != expected:
        raise JsonOutputError(
            f"Completed JSON output did not match the in-memory document: {path}"
        )


def write_stock_json(
    stock: "ExtractedStockData",
    output_dir: Path = EXTRACTED_DIR,
    captured_at: datetime | None = None,
) -> Path:
    """Atomically write and verify one extracted stock JSON document."""
    timestamp = captured_at or datetime.now(timezone.utc)
    document = stock_to_json_document(stock, timestamp)
    output_path = output_dir / output_filename(timestamp, stock.symbol)
    temporary_path: Path | None = None
    renamed = False

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise JsonOutputError(
                f"Refusing to overwrite existing JSON output: {output_path}"
            )

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=f".{output_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        read_and_verify_json(temporary_path, document)
        if output_path.exists():
            raise JsonOutputError(
                f"Refusing to overwrite existing JSON output: {output_path}"
            )
        temporary_path.rename(output_path)
        renamed = True
        read_and_verify_json(output_path, document)
        return output_path
    except JsonOutputError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise JsonOutputError(f"Could not write JSON output {output_path}: {error}") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass
        if renamed:
            try:
                read_and_verify_json(output_path, document)
            except JsonOutputError:
                try:
                    output_path.unlink()
                except OSError:
                    pass

