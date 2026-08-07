"""FastAPI application for the Falcon Stocks local dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from automation.run_pipeline import CandidateCategory
from web.job_manager import AnalysisJobManager
from web.models import RunSnapshot, safe_error_message


WEB_ROOT = Path(__file__).resolve().parent


def _status_payload(snapshot: RunSnapshot) -> dict[str, object]:
    return {
        "status": snapshot.status.value,
        "session_status": snapshot.session_status.value,
        "phase": snapshot.phase.value if snapshot.phase is not None else None,
        "current_symbol": snapshot.current_symbol,
        "processed": snapshot.processed,
        "total": snapshot.total,
        "error": snapshot.error,
        "last_analysis": (
            snapshot.latest_result.completed_at.isoformat()
            if snapshot.latest_result is not None
            else None
        ),
    }


def create_app(job_manager: AnalysisJobManager | None = None) -> FastAPI:
    application = FastAPI(title="Falcon Stocks", version="0.1.0")
    manager = job_manager or AnalysisJobManager()
    templates = Jinja2Templates(directory=WEB_ROOT / "templates")
    application.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    application.state.job_manager = manager

    @application.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        snapshot = manager.snapshot()
        result = snapshot.latest_result
        ranked_by_category = {
            category: tuple(
                item for item in (result.ranked if result else ()) if item.category == category
            )
            for category in CandidateCategory
        }
        won_pass = sum(
            candidate.fundamental_decision == "PASS"
            for candidate in (result.successful if result else ())
        )
        won_minervini = len(ranked_by_category[CandidateCategory.WON_AND_MINERVINI])
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "snapshot": snapshot,
                "result": result,
                "ranked_by_category": ranked_by_category,
                "categories": CandidateCategory,
                "won_pass": won_pass,
                "won_minervini": won_minervini,
                "safe_error": safe_error_message,
            },
        )

    @application.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def start_run() -> JSONResponse:
        if not manager.start():
            raise HTTPException(status_code=409, detail="An analysis is already running.")
        return JSONResponse(_status_payload(manager.snapshot()), status_code=202)

    @application.get("/api/runs/current")
    async def current_run() -> dict[str, object]:
        return _status_payload(manager.snapshot())

    @application.get("/stocks/{symbol}", response_class=HTMLResponse)
    async def stock_detail(request: Request, symbol: str) -> HTMLResponse:
        result = manager.snapshot().latest_result
        candidate = next(
            (
                item
                for item in (result.successful if result else ())
                if item.symbol == symbol.upper()
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="Stock analysis is unavailable.")
        ranked = next(item for item in result.ranked if item.candidate is candidate)
        return templates.TemplateResponse(
            request=request,
            name="stock_detail.html",
            context={"candidate": candidate, "ranked": ranked},
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
