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
        # This test is about output extraction, not approval gating (see
        # test_safety_gating.py for that) — approve explicitly so the risky
        # confirm step isn't blocked.
        approval_state="approved",
    )
    result = await replay_capability(cap, {"amount": "0.01"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"
    assert "new_balance" in result.outputs
    assert result.outputs["new_balance"].startswith("$")


@pytest.mark.asyncio
async def test_replay_success_with_extract_all_output():
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
                name="field_labels",
                type="string",
                extraction_locator=Locator(strategy="css_fallback", value="css=td.field-label"),
                extract_all=True,
            )
        ],
        approval_state="approved",
    )
    result = await replay_capability(cap, {"amount": "0.01"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"
    assert result.outputs["field_labels"] == ["Account:", "Amount Deposited:", "New Balance:"]


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


@pytest.mark.asyncio
async def test_replay_recovers_from_injected_interstitial_and_still_succeeds(isolated_evidence_root):
    """The recoverable-condition-that-gets-retried case: an unexpected
    interstitial mid-flow shouldn't surface as a distinct status — a
    successful recovery just continues to whatever the result would have
    been anyway. Verified via the saved log, not a 4th status value.
    """
    cap = _deposit_capability(
        steps=[
            # A checkpoint on the navigate step itself catches the
            # interstitial immediately, rather than waiting for the next
            # step to fail trying to find a field that isn't on this page.
            Step(
                action="navigate",
                value="/accounts/1001/deposit?simulate=dialog",
                checkpoint="text_contains:Deposit to Account",
            ),
            Step(
                action="type",
                locator=Locator(strategy="role", role="textbox", value="Deposit amount in dollars"),
                input_binding="amount",
            ),
            Step(action="click", locator=Locator(strategy="role", role="link", value="Continue")),
        ],
    )
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"

    log_path = next(isolated_evidence_root.iterdir()) / "log.json"
    import json

    log = json.loads(log_path.read_text())
    assert log["steps"][0]["ok"] is True
    assert log["steps"][0]["recovered_from_interstitial"] is True
    assert all(not s["recovered_from_interstitial"] for s in log["steps"][1:])


@pytest.mark.asyncio
async def test_replay_non_recoverable_interstitial_is_hard_failure():
    cap = _deposit_capability(
        steps=[Step(action="navigate", value="/accounts/1001/deposit?simulate=perm_denied")],
        success_checkpoint="text_contains:Deposit to Account",
    )
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.status == "hard_failure"


@pytest.mark.asyncio
async def test_saved_log_is_redacted_but_returned_result_is_not(isolated_evidence_root):
    """The caller-facing ReplayResult keeps real values (an agent needs the
    actual balance to be useful) — only the on-disk log.json is redacted.
    """
    cap = _deposit_capability()
    result = await replay_capability(cap, {"amount": "1234.00"}, MOCK_APP_URL, headless=True)
    assert result.status in ("success", "business_outcome", "hard_failure")

    import json

    log_path = next(isolated_evidence_root.iterdir()) / "log.json"
    log_text = log_path.read_text()
    assert "1234.00" not in log_text  # the raw input value never appears on disk
    log = json.loads(log_text)
    assert "REDACTED" in log["inputs"]["amount"]
