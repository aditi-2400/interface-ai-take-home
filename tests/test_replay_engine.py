"""Engine-level replay tests against the live mock app.

Uses hand-crafted Capability fixtures (not the real saved artifacts) so each
test is self-contained and doesn't depend on what discovery happened to
record. Business-outcome and hard-failure paths are exercised via the mock
app's real validation logic and a deliberately unresolvable locator,
respectively — no mocking of the replay engine itself. EVIDENCE_ROOT is
isolated to a tmp dir for every test here (autouse), since replay_capability
writes real evidence on every call and the real evidence/replay/ tree is
meant to hold curated demo runs, not test byproducts.
"""

from pathlib import Path

import pytest

import replay.engine
from artifacts.models import Capability, InputParam, Locator, OutputField, Step
from replay.engine import replay_capability

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def isolated_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path)
    return tmp_path


def _deposit_capability(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_deposit",
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
async def test_replay_business_outcome_from_real_validation_error():
    cap = _deposit_capability(
        known_business_outcomes={
            "text_contains:must be a positive dollar amount": "validation_error"
        }
    )
    result = await replay_capability(cap, {"amount": "-10"}, MOCK_APP_URL, headless=True)
    assert result.status == "business_outcome"
    assert result.outcome_code == "validation_error"
    assert result.failure_detail is None


@pytest.mark.asyncio
async def test_replay_hard_failure_saves_screenshot():
    cap = _deposit_capability(
        steps=[
            Step(action="navigate", value="/accounts/1001/deposit"),
            Step(
                action="click",
                locator=Locator(strategy="role", role="link", value="This Button Does Not Exist"),
            ),
        ],
    )
    result = await replay_capability(cap, {"amount": "10"}, MOCK_APP_URL, headless=True)
    assert result.status == "hard_failure"
    assert result.failure_detail.step_index == 1
    assert result.failure_detail.screenshot_path is not None
    assert Path(result.failure_detail.screenshot_path).exists()


@pytest.mark.asyncio
async def test_replay_success_with_output_extraction():
    cap = _deposit_capability(
        steps=[
            Step(action="navigate", value="/accounts/1001/deposit"),
            Step(
                action="type",
                locator=Locator(strategy="role", role="textbox", value="Deposit amount in dollars"),
                input_binding="amount",
            ),
            Step(action="click", locator=Locator(strategy="role", role="link", value="Continue")),
            Step(
                action="click",
                locator=Locator(strategy="role", role="link", value="Confirm Deposit"),
                risky=True,
            ),
        ],
        success_checkpoint="text_contains:Deposit Complete",
        outputs=[
            OutputField(
                name="new_balance",
                type="decimal",
                extraction_locator=Locator(
                    strategy="css_fallback",
                    value="xpath=//td[normalize-space(text())='New Balance:']/following-sibling::td[1]",
                ),
            )
        ],
    )
    result = await replay_capability(cap, {"amount": "0.01"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"
    assert "new_balance" in result.outputs
    assert result.outputs["new_balance"].startswith("$")


@pytest.mark.asyncio
async def test_replay_input_validation_failure_short_circuits_before_browser():
    cap = _deposit_capability()
    result = await replay_capability(cap, {}, MOCK_APP_URL, headless=True)
    assert result.status == "hard_failure"
    assert "missing required input" in result.failure_detail.observed


@pytest.mark.asyncio
async def test_replay_writes_evidence_log(isolated_evidence_root):
    cap = _deposit_capability(
        known_business_outcomes={
            "text_contains:must be a positive dollar amount": "validation_error"
        }
    )
    await replay_capability(cap, {"amount": "-5"}, MOCK_APP_URL, headless=True)
    matches = list(isolated_evidence_root.iterdir())
    assert len(matches) == 1
    assert (matches[0] / "log.json").exists()
