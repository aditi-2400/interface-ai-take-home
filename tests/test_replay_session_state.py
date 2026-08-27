"""Live tests for the storage_state save/load plumbing added to replay_capability().

The mock app has no login/session concept at all, so these tests can't prove
the *semantic* point (skip re-authenticating) the way a real MERIDIAN run
will - there's nothing to authenticate against here. What they do prove,
against a real Playwright browser: saving actually produces a well-formed
storage_state file, and loading one back doesn't break a real replay run.
That's the real risk in wiring two new optional kwargs through - the
semantic "you stay logged in" proof happens once this same mechanism is
pointed at MERIDIAN's real cookie-based session.
"""

import json

import pytest

import replay.engine
from artifacts.models import Capability, InputParam, Locator, Step
from replay.engine import replay_capability

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def isolated_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path)
    return tmp_path


def _deposit_capability(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_deposit_session",
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


@pytest.mark.asyncio
async def test_save_storage_state_writes_a_well_formed_playwright_file(tmp_path):
    session_path = tmp_path / "session.json"
    cap = _deposit_capability()

    result = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, save_storage_state_to=session_path
    )

    assert result.status == "success"
    assert session_path.exists()
    data = json.loads(session_path.read_text())
    # Playwright's own storage_state() output shape - present even when the
    # site sets zero cookies, which the mock app does today.
    assert "cookies" in data
    assert "origins" in data


@pytest.mark.asyncio
async def test_load_storage_state_does_not_break_a_real_replay(tmp_path):
    session_path = tmp_path / "session.json"
    cap = _deposit_capability()

    first = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, save_storage_state_to=session_path
    )
    assert first.status == "success"

    # A genuinely separate call - new browser, new context - loading the
    # file the first call produced. Proves the load path is wired correctly
    # and doesn't interfere with a normal run, even with no real cookies to
    # carry over yet.
    second = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, load_storage_state_from=session_path
    )
    assert second.status == "success"
