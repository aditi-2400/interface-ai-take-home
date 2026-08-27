import json

import pytest
from fastapi.testclient import TestClient

import api.config
import replay.engine
from api.main import app
from artifacts import storage
from artifacts.models import Capability, InputParam, Locator, Step

client = TestClient(app)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORE_DIR", tmp_path / "store")
    return storage


@pytest.fixture(autouse=True)
def isolated_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path / "replay")
    monkeypatch.setattr(api.config, "EVIDENCE_DIR", tmp_path)
    return tmp_path / "replay"


def _deposit_capability(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_dashboard_cap",
        version=1,
        target_app="meridian-trust-core-banking",
        description="test fixture",
        inputs=[InputParam(name="amount", type="decimal", required=True, description="x")],
        steps=[
            Step(action="navigate", value="/accounts/1001/deposit"),
            Step(
                action="type",
                locator=Locator(strategy="role", role="textbox", value="Deposit amount in dollars"),
                input_binding="amount",
            ),
        ],
        success_checkpoint="text_contains:Confirm Deposit",
        outputs=[],
        risk_level="safe",
        known_business_outcomes={},
    )
    defaults.update(overrides)
    return Capability(**defaults)


def _write_run(evidence_root, run_id: str, capability_id: str, status: str, screenshot_path=None):
    run_dir = evidence_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    failure_detail = None
    if screenshot_path:
        failure_detail = {
            "step_index": 1,
            "expected": "x",
            "observed": "y",
            "screenshot_path": str(screenshot_path),
        }
    log = {
        "capability_id": capability_id,
        "version": 1,
        "inputs": {},
        "base_url": "http://example.test",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
        "steps": [{"step_index": 0, "action": "navigate", "ok": True, "error": None, "duration_seconds": 0.1, "recovered_from_interstitial": False}],
        "result": {"status": status, "outputs": {}, "outcome_code": None, "failure_detail": failure_detail},
        "escalations": [],
    }
    (run_dir / "log.json").write_text(json.dumps(log))


def test_dashboard_home_lists_capabilities(store):
    store.save(_deposit_capability())

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "test_dashboard_cap" in response.text


def test_dashboard_runs_lists_runs(isolated_evidence_root):
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "success")

    response = client.get("/dashboard/runs")

    assert response.status_code == 200
    assert "cap_a_20260101T000000Z" in response.text


def test_dashboard_run_detail_renders(isolated_evidence_root):
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "success")

    response = client.get("/dashboard/runs/cap_a_20260101T000000Z")

    assert response.status_code == 200
    assert "cap_a" in response.text


def test_dashboard_run_detail_unknown_run_is_404(isolated_evidence_root):
    response = client.get("/dashboard/runs/nonexistent")
    assert response.status_code == 404


def test_dashboard_run_detail_links_real_screenshot(isolated_evidence_root):
    shot = isolated_evidence_root / "cap_a_20260101T000000Z" / "screenshots" / "failure_step_1.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"fake png bytes")
    _write_run(isolated_evidence_root, "cap_a_20260101T000000Z", "cap_a", "hard_failure", screenshot_path=shot)

    response = client.get("/dashboard/runs/cap_a_20260101T000000Z")

    assert response.status_code == 200
    assert "/evidence-files/" in response.text
