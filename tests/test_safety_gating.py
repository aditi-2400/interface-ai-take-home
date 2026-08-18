"""Engine-level tests for the two Phase 6 gates: the allowlist (consulted
before every step) and approval_state (gating risky steps specifically).
Live, against the real mock app — the gates are policy checks around real
step execution, not something meaningful to unit-test in isolation.
"""

import pytest

import replay.engine
from artifacts.models import Capability, InputParam, Locator, Step
from replay.engine import replay_capability
from safety.allowlist import Allowlist

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def isolated_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path)


def _capability(**overrides) -> Capability:
    defaults = dict(
        capability_id="test_gating",
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
            Step(
                action="click",
                locator=Locator(strategy="role", role="link", value="Confirm Deposit"),
                risky=True,
            ),
        ],
        success_checkpoint="text_contains:Deposit Complete",
        outputs=[],
        risk_level="risky",
        approval_state="draft",
    )
    defaults.update(overrides)
    return Capability(**defaults)


@pytest.mark.asyncio
async def test_draft_capability_blocks_at_the_risky_step():
    cap = _capability(approval_state="draft")
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.status == "hard_failure"
    assert "approval_state" in result.failure_detail.observed
    assert "approved" in result.failure_detail.observed


@pytest.mark.asyncio
async def test_approved_capability_executes_the_risky_step():
    cap = _capability(approval_state="approved")
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"


@pytest.mark.asyncio
async def test_draft_capability_still_runs_safe_steps_up_to_the_risky_one():
    """Gating is per-step, not per-capability: the safe navigate/type/click
    steps before the risky one should still execute — only the risky step
    itself is blocked.
    """
    cap = _capability(approval_state="draft")
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.failure_detail.step_index == 3  # the "Confirm Deposit" step


@pytest.mark.asyncio
async def test_allowlist_blocks_disallowed_domain():
    cap = _capability(steps=[Step(action="navigate", value="/accounts/1001/deposit")])
    restrictive = Allowlist(
        allowed_domains=["not-the-mock-app.example.com"],
        allowed_route_patterns=[r"^/.*$"],
        allowed_action_types=["navigate", "click", "type"],
    )
    result = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True, allowlist=restrictive
    )
    assert result.status == "hard_failure"
    assert "allowlist" in result.failure_detail.observed
    assert "domain" in result.failure_detail.observed


@pytest.mark.asyncio
async def test_allowlist_blocks_disallowed_action_type():
    cap = _capability(steps=[Step(action="navigate", value="/accounts/1001/deposit")])
    restrictive = Allowlist(
        allowed_domains=["127.0.0.1:8000"],
        allowed_route_patterns=[r"^/.*$"],
        allowed_action_types=["click"],  # navigate deliberately excluded
    )
    result = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True, allowlist=restrictive
    )
    assert result.status == "hard_failure"
    assert "action type" in result.failure_detail.observed


@pytest.mark.asyncio
async def test_allowlist_blocks_disallowed_route():
    cap = _capability(steps=[Step(action="navigate", value="/admin/danger")])
    restrictive = Allowlist(
        allowed_domains=["127.0.0.1:8000"],
        allowed_route_patterns=[r"^/accounts(/.*)?$"],  # /admin not covered
        allowed_action_types=["navigate"],
    )
    result = await replay_capability(
        cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True, allowlist=restrictive
    )
    assert result.status == "hard_failure"
    assert "route" in result.failure_detail.observed


@pytest.mark.asyncio
async def test_default_allowlist_permits_the_real_mock_app():
    """No allowlist argument passed -> loads safety/allowlist.yaml, which
    must actually cover the mock app for every other test in this suite to
    have been exercising real enforcement rather than a no-op default."""
    cap = _capability(approval_state="approved")
    result = await replay_capability(cap, {"amount": "5.00"}, MOCK_APP_URL, headless=True)
    assert result.status == "success"
