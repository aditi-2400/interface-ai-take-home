"""Discovery-loop safety tests: the allowlist check and the risky-action
confirmation gate, exercised end to end via run_discovery with a scripted
fake LLM (no real model call, no blocking on real stdin — confirm_risky_action
is always injected explicitly here, never the real interactive default).
"""

from unittest.mock import AsyncMock, patch

import pytest

import agent.discovery
import artifacts.storage
from agent.action_schema import AgentAction, AgentLocator
from agent.discovery import default_confirm_risky_action, run_discovery
from safety.allowlist import Allowlist

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path, monkeypatch):
    # run_discovery writes both evidence (transcript/screenshots) and, on
    # success, a saved capability — isolate both so repeated test runs don't
    # accumulate test_* byproducts in the real evidence/ and artifacts/store/
    # trees, mirroring test_replay_engine.py's isolated_evidence_root.
    monkeypatch.setattr(agent.discovery, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(artifacts.storage, "STORE_DIR", tmp_path / "store")


def _scripted_llm(actions):
    calls = {"n": 0}

    async def fake_decide_next_action(system_prompt, user_prompt, model=None):
        action = actions[calls["n"]]
        calls["n"] += 1
        return action, action.model_dump_json()

    return fake_decide_next_action


TRANSFER_ACTIONS = [
    AgentAction(
        reasoning="type destination",
        action="type",
        locator=AgentLocator(role="textbox", value="Destination account ID"),
        input_value="2001",
    ),
    AgentAction(
        reasoning="type amount",
        action="type",
        locator=AgentLocator(role="textbox", value="Transfer amount in dollars"),
        input_value="1.00",
    ),
    AgentAction(
        reasoning="click continue",
        action="click",
        locator=AgentLocator(role="link", value="Continue"),
    ),
    AgentAction(
        reasoning="click confirm - this is the risky one",
        action="click",
        locator=AgentLocator(role="link", value="Confirm Transfer"),
    ),
    AgentAction(reasoning="done", action="goal_complete", done_summary="Transfer confirmed."),
]


@pytest.mark.asyncio
async def test_allowlist_blocks_disallowed_navigate_target():
    restrictive = Allowlist(
        allowed_domains=["127.0.0.1:8000"],
        allowed_route_patterns=[r"^/nowhere-that-exists$"],  # deliberately excludes /accounts
        allowed_action_types=["navigate", "click", "type"],
    )
    actions = [AgentAction(reasoning="try to navigate", action="navigate", input_value="/accounts/1001/deposit")]
    with patch("agent.discovery.decide_next_action", new=AsyncMock(side_effect=_scripted_llm(actions))):
        transcript, capability, run_dir = await run_discovery(
            goal="[TEST] irrelevant",
            start_url=f"{MOCK_APP_URL}/members/12345",
            capability_id="test_allowlist_block",
            target_app="meridian-trust-core-banking",
            description="test",
            max_steps=5,
            headless=True,
            allowlist=restrictive,
        )
    assert transcript.outcome == "error"
    assert "allowlist" in transcript.final_summary
    assert capability is None


@pytest.mark.asyncio
async def test_risky_action_confirmed_completes_normally():
    async def always_confirm(action) -> bool:
        return True

    with patch(
        "agent.discovery.decide_next_action", new=AsyncMock(side_effect=_scripted_llm(TRANSFER_ACTIONS))
    ):
        transcript, capability, run_dir = await run_discovery(
            goal="[TEST] Transfer $1.00 from this account to account 2001, then confirm.",
            start_url=f"{MOCK_APP_URL}/accounts/1001/transfer",
            capability_id="test_risky_confirmed",
            target_app="meridian-trust-core-banking",
            description="test",
            max_steps=8,
            headless=True,
            confirm_risky_action=always_confirm,
        )
    assert transcript.outcome == "success"
    assert capability is not None
    confirm_steps = [s for s in capability.steps if s.risky]
    assert len(confirm_steps) == 1


@pytest.mark.asyncio
async def test_risky_action_declined_halts_as_stuck_without_executing():
    async def always_decline(action) -> bool:
        return False

    import httpx

    before = httpx.get(f"{MOCK_APP_URL}/members/12345").text

    with patch(
        "agent.discovery.decide_next_action", new=AsyncMock(side_effect=_scripted_llm(TRANSFER_ACTIONS))
    ):
        transcript, capability, run_dir = await run_discovery(
            goal="[TEST] Transfer $1.00 from this account to account 2001, then confirm.",
            start_url=f"{MOCK_APP_URL}/accounts/1001/transfer",
            capability_id="test_risky_declined",
            target_app="meridian-trust-core-banking",
            description="test",
            max_steps=8,
            headless=True,
            confirm_risky_action=always_decline,
        )

    after = httpx.get(f"{MOCK_APP_URL}/members/12345").text
    assert transcript.outcome == "stuck"
    assert capability is None
    assert "not confirmed" in transcript.final_summary
    # The declined click never executed - no transfer, no balance change.
    assert before == after
    last_step = transcript.steps[-1]
    assert last_step.execution_ok is False
    assert "not confirmed" in last_step.execution_error


@pytest.mark.asyncio
async def test_default_confirm_risky_action_parses_stdin_yes_and_no():
    action = AgentAction(
        reasoning="x", action="click", locator=AgentLocator(role="link", value="Confirm Transfer")
    )
    with patch("builtins.input", return_value="y"):
        assert await default_confirm_risky_action(action) is True
    with patch("builtins.input", return_value="n"):
        assert await default_confirm_risky_action(action) is False
    with patch("builtins.input", return_value=""):
        assert await default_confirm_risky_action(action) is False


@pytest.mark.asyncio
async def test_transcript_json_is_redacted_on_disk():
    async def always_confirm(action) -> bool:
        return True

    with patch(
        "agent.discovery.decide_next_action", new=AsyncMock(side_effect=_scripted_llm(TRANSFER_ACTIONS))
    ):
        transcript, capability, run_dir = await run_discovery(
            goal="[TEST] Transfer $1.00 from this account to account 2001, then confirm.",
            start_url=f"{MOCK_APP_URL}/accounts/1001/transfer",
            capability_id="test_transcript_redaction",
            target_app="meridian-trust-core-banking",
            description="test",
            max_steps=8,
            headless=True,
            confirm_risky_action=always_confirm,
        )
    assert transcript.outcome == "success"

    transcript_text = (run_dir / "transcript.json").read_text()
    assert "2001" not in transcript_text  # the destination account number never appears on disk
    assert "REDACTED" in transcript_text

    # Conversion ran against the real (unredacted) in-memory transcript, not
    # the redacted on-disk copy — otherwise this would have bound the input
    # to the literal string "[REDACTED_ID]" instead of correctly recognizing
    # and templating the real recorded value.
    assert capability is not None
    assert any(i.name == "destination_account_id" for i in capability.inputs)
