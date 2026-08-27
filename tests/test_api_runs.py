import json

import pytest
from fastapi.testclient import TestClient

import replay.engine
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path)
    return tmp_path


def _write_run(evidence_root, run_id: str, capability_id: str, status: str, started_at: str = "2026-01-01T00:00:00+00:00"):
    run_dir = evidence_root / run_id
    run_dir.mkdir(parents=True)
    log = {
        "capability_id": capability_id,
        "version": 1,
        "inputs": {},
        "base_url": "http://example.test",
        "started_at": started_at,
        "finished_at": "2026-01-01T00:00:01+00:00",
        "steps": [],
        "result": {"status": status, "outputs": {}, "outcome_code": None, "failure_detail": None},
        "escalations": [],
    }
    (run_dir / "log.json").write_text(json.dumps(log))


def test_get_runs_empty_when_no_evidence(isolated_evidence_root):
    response = client.get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_get_runs_lists_all_runs(isolated_evidence_root):
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "success")
    _write_run(isolated_evidence_root, "cap_b_20260101T000001Z", "cap_b", "business_outcome")

    response = client.get("/runs")

    assert response.status_code == 200
    run_ids = {r["run_id"] for r in response.json()}
    assert run_ids == {"cap_a_20260101T000000Z", "cap_b_20260101T000001Z"}


def test_get_runs_orders_by_actual_time_not_directory_name(isolated_evidence_root):
    # "aaa" sorts alphabetically before "zzz", but its run happened later -
    # a name-based sort would get this backwards.
    _write_run(isolated_evidence_root, "zzz_cap_20260101T000000Z", "zzz_cap", "success", started_at="2026-01-01T00:00:00+00:00")
    _write_run(isolated_evidence_root, "aaa_cap_20260102T000000Z", "aaa_cap", "success", started_at="2026-01-02T00:00:00+00:00")

    response = client.get("/runs")

    run_ids = [r["run_id"] for r in response.json()]
    assert run_ids == ["aaa_cap_20260102T000000Z", "zzz_cap_20260101T000000Z"]


def test_get_runs_filters_by_capability_id(isolated_evidence_root):
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "success")
    _write_run(isolated_evidence_root, "cap_b_20260101T000001Z", "cap_b", "success")

    response = client.get("/runs?capability_id=cap_a")

    assert response.status_code == 200
    assert [r["run_id"] for r in response.json()] == ["cap_a_20260101T000000Z"]


def test_get_single_run(isolated_evidence_root):
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "success")

    response = client.get("/runs/cap_a_20260101T000000Z")

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "success"


def test_get_unknown_run_is_404(isolated_evidence_root):
    response = client.get("/runs/nonexistent_run")
    assert response.status_code == 404
