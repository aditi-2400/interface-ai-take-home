"""API tests. Catalog/404 behavior is plain unit testing (isolated store,
no browser); invoke is live since it runs a real replay against the mock
app - same reasoning as tests/test_replay_engine.py.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from artifacts import storage
from artifacts.models import Capability, InputParam, Locator, Step

client = TestClient(app)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORE_DIR", tmp_path / "store")
    return storage


def _deposit_capability(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_api_deposit",
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
            Step(action="click", locator=Locator(strategy="role", role="link", value="Continue")),
        ],
        success_checkpoint="text_contains:Confirm Deposit",
        outputs=[],
        risk_level="safe",
        known_business_outcomes={},
    )
    defaults.update(overrides)
    return Capability(**defaults)


def test_list_capabilities_returns_catalog(store):
    store.save(_deposit_capability())
    store.save(_deposit_capability(capability_id="other_capability"))

    response = client.get("/capabilities")

    assert response.status_code == 200
    ids = {c["capability_id"] for c in response.json()}
    assert ids == {"test_api_deposit", "other_capability"}


def test_get_capability_returns_latest_by_default(store):
    store.save(_deposit_capability())
    store.save(_deposit_capability(version=2))

    response = client.get("/capabilities/test_api_deposit")

    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_get_capability_respects_version_query_param(store):
    store.save(_deposit_capability())
    store.save(_deposit_capability(version=2))

    response = client.get("/capabilities/test_api_deposit?version=1")

    assert response.status_code == 200
    assert response.json()["version"] == 1


def test_get_unknown_capability_is_404(store):
    response = client.get("/capabilities/nonexistent")
    assert response.status_code == 404


def test_invoke_unknown_capability_is_404(store):
    response = client.post("/capabilities/nonexistent/invoke", json={"inputs": {}})
    assert response.status_code == 404


def test_invoke_unknown_target_app_is_500(store):
    store.save(_deposit_capability(target_app="some-unmapped-target"))
    response = client.post("/capabilities/test_api_deposit/invoke", json={"inputs": {"amount": "5"}})
    assert response.status_code == 500


@pytest.mark.live
def test_invoke_real_capability_against_mock_app(store):
    store.save(_deposit_capability())

    response = client.post(
        "/capabilities/test_api_deposit/invoke", json={"inputs": {"amount": "5.00"}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
