"""Minimal dashboard: watch the system work. Reads exactly what the engine
already writes to disk (capabilities, run logs, screenshots) - computes
nothing new, so the safety/evidence guarantees (redaction, etc.) carry
over automatically.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import replay.engine as replay_engine
from api.routers.runs import get_run, list_runs
from api.templating import templates
from artifacts import storage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _run_screenshots(run_id: str) -> list[str]:
    # failure_detail.screenshot_path only covers a run that ends in
    # hard_failure - a run that gets resolved via escalation and finishes
    # as success has no failure_detail at all, even though real screenshots
    # (resumed_after_step_*, operator_*) were written for it. List whatever
    # is actually on disk instead of relying on one result field.
    screenshots_dir = replay_engine.EVIDENCE_ROOT / run_id / "screenshots"
    if not screenshots_dir.exists():
        return []
    return sorted(str(p) for p in screenshots_dir.glob("*.png"))


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
    screenshots = _run_screenshots(run_id)
    return templates.TemplateResponse(
        request, "dashboard_run_detail.html", {"run": run, "screenshots": screenshots}
    )
