"""Engine-level escalation tests against the live mock app.

The operator side runs as a genuinely separate OS process (see
_operator_subprocess_helper.py for why), never an in-process asyncio task.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import escalation.queue as equeue
import replay.engine
from artifacts.models import Capability, InputParam, Locator, Step
from escalation import queue
from replay.engine import EscalationConfig, replay_capability

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"
OPERATOR_HELPER = Path(__file__).parent / "_operator_subprocess_helper.py"


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path)
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "interventions.db")
    # These tests exercise real escalations on purpose (real CDP, no mocks) -
    # but notification behavior itself is already covered by its own
    # dedicated test file (test_escalation_notify.py). Without this, every
    # escalating test here would also fire a real desktop notification.
    monkeypatch.setattr(replay.engine.enotify, "notify", lambda *a, **kw: None)
    return tmp_path


def _launch_operator(db_path: Path, capability_id: str, click: str | None = None) -> subprocess.Popen:
    args = [sys.executable, str(OPERATOR_HELPER), str(db_path), capability_id]
    if click:
        args += ["--click", click]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _stuck_capability(capability_id: str, success_checkpoint: str) -> Capability:
    return Capability(
        capability_id=capability_id,
        version=1,
        target_app="meridian-trust-core-banking",
        description="test fixture",
        inputs=[InputParam(name="member_id", type="integer", required=True, description="x")],
        steps=[
            Step(action="navigate", value="/members/{member_id}"),
            Step(
                action="click",
                locator=Locator(strategy="role", role="link", value="This Button Does Not Exist"),
            ),
        ],
        success_checkpoint=success_checkpoint,
        outputs=[],
        risk_level="safe",
    )


@pytest.mark.asyncio
async def test_escalation_disabled_behaves_exactly_like_before():
    cap = _stuck_capability("test_no_escalation", "text_contains:Member Detail")
    result = await replay_capability(cap, {"member_id": "12345"}, MOCK_APP_URL, headless=True)
    assert result.status == "hard_failure"
    assert equeue.list_pending() == []


@pytest.mark.asyncio
async def test_escalation_creates_intervention_and_times_out_without_resolution():
    cap = _stuck_capability("test_escalation_timeout", "text_contains:Member Detail")
    escalation = EscalationConfig(cdp_port=9401, poll_interval_seconds=0.3, timeout_seconds=2)
    result = await replay_capability(
        cap, {"member_id": "12345"}, MOCK_APP_URL, headless=True, escalation=escalation
    )
    assert result.status == "hard_failure"  # gave up, fell back to the original failure


@pytest.mark.asyncio
async def test_escalation_resolves_and_continues_to_success(isolated_roots):
    """The operator does nothing to the page, just resolves — the loop
    should move past the failed step, and since the current (unchanged)
    page already satisfies success_checkpoint, this verifies the
    "continue and re-evaluate" logic specifically, not any particular fix.
    """
    capability_id = "test_escalation_resolve"
    cap = _stuck_capability(capability_id, "text_contains:Member Detail")  # already true
    escalation = EscalationConfig(cdp_port=9402, poll_interval_seconds=0.3, timeout_seconds=15)

    operator = _launch_operator(queue.DB_PATH, capability_id)
    try:
        result = await replay_capability(
            cap, {"member_id": "12345"}, MOCK_APP_URL, headless=True, escalation=escalation
        )
    finally:
        stdout, _ = operator.communicate(timeout=10)
        print(stdout)

    assert operator.returncode == 0
    assert result.status == "success"


@pytest.mark.asyncio
async def test_escalation_resolves_via_real_cdp_reconnect(isolated_roots):
    """The real thing: a genuinely separate process connects over CDP, finds
    the SAME live page the paused runner was using, performs a real action,
    then resolves — exactly what escalation/operator.py does.
    """
    capability_id = "test_escalation_cdp_reconnect"
    cap = _stuck_capability(capability_id, "text_contains:Open New Sub-Account for Dana Whitfield")
    escalation = EscalationConfig(cdp_port=9403, poll_interval_seconds=0.3, timeout_seconds=15)

    operator = _launch_operator(queue.DB_PATH, capability_id, click="Open New Sub-Account")
    try:
        result = await replay_capability(
            cap, {"member_id": "12345"}, MOCK_APP_URL, headless=True, escalation=escalation
        )
    finally:
        stdout, _ = operator.communicate(timeout=10)
        print(stdout)

    assert operator.returncode == 0
    assert "clicked 'Open New Sub-Account'" in stdout
    assert result.status == "success"


@pytest.mark.asyncio
async def test_allowlist_violation_never_escalates():
    from safety.allowlist import Allowlist

    cap = Capability(
        capability_id="test_no_escalation_for_policy",
        version=1,
        target_app="meridian-trust-core-banking",
        description="test",
        inputs=[],
        steps=[Step(action="navigate", value="/members/12345")],
        success_checkpoint="text_contains:Member Detail",
        outputs=[],
        risk_level="safe",
    )
    restrictive = Allowlist(
        allowed_domains=["127.0.0.1:8000"],
        allowed_route_patterns=[r"^/nowhere$"],
        allowed_action_types=["navigate"],
    )
    escalation = EscalationConfig(cdp_port=9404, poll_interval_seconds=0.3, timeout_seconds=5)
    result = await replay_capability(
        cap, {}, MOCK_APP_URL, headless=True, allowlist=restrictive, escalation=escalation
    )
    assert result.status == "hard_failure"
    assert "allowlist" in result.failure_detail.observed
    assert equeue.list_pending() == []  # never even raised an intervention


@pytest.mark.asyncio
async def test_risky_action_block_escalates_and_human_performs_it(isolated_roots):
    capability_id = "test_risky_escalation"
    cap = Capability(
        capability_id=capability_id,
        version=1,
        target_app="meridian-trust-core-banking",
        description="test",
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
        approval_state="draft",  # unapproved - the risky step will be blocked
    )
    escalation = EscalationConfig(cdp_port=9405, poll_interval_seconds=0.3, timeout_seconds=15)

    operator = _launch_operator(queue.DB_PATH, capability_id, click="Confirm Deposit")
    try:
        result = await replay_capability(
            cap, {"amount": "2.00"}, MOCK_APP_URL, headless=True, escalation=escalation
        )
    finally:
        stdout, _ = operator.communicate(timeout=10)
        print(stdout)

    assert operator.returncode == 0
    assert result.status == "success"
