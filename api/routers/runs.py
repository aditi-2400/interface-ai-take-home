"""Run history: reads back what replay already writes to disk (log.json per
run) - no new persistence system. list_runs is a plain function, not just a
route handler, so the dashboard can reuse it directly for its own page.
"""

import json

from fastapi import APIRouter, HTTPException

import replay.engine as replay_engine

router = APIRouter(prefix="/runs", tags=["runs"])


def list_runs(capability_id: str | None = None) -> list[dict]:
    # Reads replay.engine.EVIDENCE_ROOT through the module (not a direct
    # value import) so tests can monkeypatch it, same as tests/test_replay_engine.py.
    evidence_root = replay_engine.EVIDENCE_ROOT
    if not evidence_root.exists():
        return []
    runs = []
    for run_dir in evidence_root.iterdir():
        log_path = run_dir / "log.json"
        if not log_path.exists():
            continue
        log = json.loads(log_path.read_text())
        if capability_id and log.get("capability_id") != capability_id:
            continue
        runs.append({"run_id": run_dir.name, **log})
    # Sort by the run's own started_at, not the directory name - run_id is
    # "{capability_id}_{timestamp}", and capability_id varies in length and
    # content, so sorting the name string groups by capability first and
    # only orders by time within each one, not across the whole list.
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


def get_run(run_id: str) -> dict | None:
    log_path = replay_engine.EVIDENCE_ROOT / run_id / "log.json"
    if not log_path.exists():
        return None
    return {"run_id": run_id, **json.loads(log_path.read_text())}


@router.get("")
def get_runs(capability_id: str | None = None) -> list[dict]:
    return list_runs(capability_id)


@router.get("/{run_id}")
def get_run_route(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r} found")
    return run
