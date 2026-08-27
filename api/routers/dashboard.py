"""Minimal dashboard: watch the system work. Reads exactly what the engine
already writes to disk (capabilities, run logs, screenshots) - computes
nothing new, so the safety/evidence guarantees (redaction, etc.) carry
over automatically.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from api.routers.runs import get_run, list_runs
from api.templating import templates
from artifacts import storage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
def dashboard_home(request: Request):
    capabilities = storage.list_latest_capabilities()
    return templates.TemplateResponse(request, "dashboard.html", {"capabilities": capabilities})


@router.get("/runs", response_class=HTMLResponse)
def dashboard_runs(request: Request, capability_id: str | None = None):
    runs = list_runs(capability_id)
    return templates.TemplateResponse(
        request, "dashboard_runs.html", {"runs": runs, "capability_id": capability_id}
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def dashboard_run_detail(request: Request, run_id: str):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r} found")
    return templates.TemplateResponse(request, "dashboard_run_detail.html", {"run": run})
