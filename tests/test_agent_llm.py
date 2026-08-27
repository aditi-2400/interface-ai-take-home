"""Tests for agent/llm.py's shared structured-output dispatch, using
httpx.MockTransport (built into httpx, no extra dependency) so these run
fast and don't need a real Ollama/Anthropic call. Real end-to-end LLM
behavior is covered by the project's actual live discovery runs, not here -
this just protects the dispatch/parsing plumbing both decide_next_action
and decide_capability_choice share.
"""

import json

import httpx
import pytest

import agent.llm as llm_module
from agent.chat_schema import CapabilityChoice
from agent.llm import LLMError, decide_capability_choice, decide_next_action

# Captured before any monkeypatching - the fake AsyncClient below has to
# call the REAL class, not itself (patching httpx.AsyncClient with a
# lambda that calls httpx.AsyncClient(...) would just recurse into the
# patched version).
_RealAsyncClient = httpx.AsyncClient


def _fake_async_client_for(handler):
    def factory(**kw):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kw)

    return factory


def _ollama_transport(reply_content: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": reply_content}})

    return _fake_async_client_for(handler)


@pytest.fixture(autouse=True)
def use_ollama(monkeypatch):
    monkeypatch.setattr(llm_module, "FALLBACK_LLM_PROVIDER", "")


@pytest.mark.asyncio
async def test_decide_next_action_parses_a_valid_response(monkeypatch):
    action_json = json.dumps(
        {
            "reasoning": "clicking the link",
            "action": "click",
            "locator": {"role": "link", "value": "Continue", "nth": None},
            "input_value": None,
            "done_summary": None,
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", _ollama_transport(action_json))

    action, raw = await decide_next_action("system", "user")

    assert action.action == "click"
    assert action.locator.value == "Continue"
    assert raw == action_json


@pytest.mark.asyncio
async def test_decide_next_action_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ollama_transport("not json"))

    with pytest.raises(LLMError, match="invalid AgentAction"):
        await decide_next_action("system", "user")


@pytest.mark.asyncio
async def test_decide_capability_choice_parses_a_valid_response(monkeypatch):
    choice_json = json.dumps(
        {
            "reasoning": "user wants a balance check",
            "capability_id": "meridian_balance_inquiry",
            "inputs": [{"name": "value", "value": "100987"}],
            "clarification_needed": None,
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", _ollama_transport(choice_json))

    choice, raw = await decide_capability_choice("system", "user")

    assert isinstance(choice, CapabilityChoice)
    assert choice.capability_id == "meridian_balance_inquiry"
    assert [(kv.name, kv.value) for kv in choice.inputs] == [("value", "100987")]


@pytest.mark.asyncio
async def test_decide_capability_choice_survives_invalid_escaped_quote(monkeypatch):
    # Real, observed live failure: the model's own answer was fine, but it
    # sometimes writes a real backslash immediately before an apostrophe
    # inside the JSON string - not a valid JSON escape (only \" and \\ need
    # escaping at all) - which a strict parser correctly rejects. Built via
    # replace() on valid JSON, not a hand-escaped literal, so there's no
    # ambiguity about how many literal backslash characters end up in the
    # string (hand-escaping this got confusing enough to trip up a first
    # attempt at this very test).
    valid_json = json.dumps(
        {
            "reasoning": "The user's request about weather has no relation to banking.",
            "capability_id": None,
            "inputs": [],
            "clarification_needed": "I can't help with weather. Anything banking-related?",
        }
    )
    choice_json = valid_json.replace("'", "\\'")
    assert "\\'" in choice_json  # sanity: the broken escape is really in there
    monkeypatch.setattr(httpx, "AsyncClient", _ollama_transport(choice_json))

    choice, raw = await decide_capability_choice("system", "user")

    assert choice.capability_id is None
    assert "weather" in choice.clarification_needed.lower()


@pytest.mark.asyncio
async def test_decide_capability_choice_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _ollama_transport("garbage"))

    with pytest.raises(LLMError, match="invalid CapabilityChoice"):
        await decide_capability_choice("system", "user")


@pytest.mark.asyncio
async def test_ollama_http_error_becomes_llm_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client_for(handler))

    with pytest.raises(LLMError, match="HTTP 500"):
        await decide_next_action("system", "user")
