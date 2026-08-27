"""LLM client for structured-output decisions: the discovery loop's next
action, and the chatbot's capability choice. Both share the same
provider dispatch below - only the Pydantic model and its JSON schema
differ per call site.

Primary: Ollama's local REST API, called directly over HTTP (httpx) rather
than the `ollama` pip package, so the structured-output request (the
`format` field, Ollama's grammar-constrained generation) stays fully
explicit — the model is never asked to freelance valid JSON unaided.

Fallback (FALLBACK_LLM_PROVIDER=anthropic): Claude, via output_config's
JSON-schema structured output, forced to the same schema — a documented
decision per CLAUDE.md's own fallback plan, not a silent swap.
See REPORT.md for what was observed on Gemma 4 that motivated this.
"""

import os
import re
from typing import TypeVar

import anthropic
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

from agent.action_schema import ACTION_JSON_SCHEMA, AgentAction
from agent.chat_schema import CAPABILITY_CHOICE_JSON_SCHEMA, CapabilityChoice

load_dotenv()

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
# 0.0 (greedy decoding) was chosen for reproducibility, but that has a real
# cost: greedy decoding has zero chance to escape a repetitive attractor
# once it enters one, since it always re-picks the same most-likely next
# token. Observed live: input_value spiraling into the same phrase repeated
# hundreds of times. A small non-zero value trades away some determinism
# for a chance to break out of exactly that failure mode. Overridable per
# call (and per env) to A/B test rather than picking a value blindly.
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.0"))

FALLBACK_LLM_PROVIDER = os.environ.get("FALLBACK_LLM_PROVIDER", "").strip().lower()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_MAX_TOKENS = 1024

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


# Real, observed live failure: the model wrote a perfectly sensible reply
# containing an apostrophe (e.g. "I can't help with that"), but escaped it
# as \' inside the JSON string - which isn't a valid JSON escape at all
# (only \" and \\ need escaping; a bare ' never does). A strict JSON parser
# correctly rejects that, even though the model's actual answer was fine.
# Stripping the invalid escape back to a plain ' fixes it without touching
# any real content.
_INVALID_ESCAPED_QUOTE_RE = re.compile(r"\\'")


def _fix_invalid_json_escapes(raw: str) -> str:
    return _INVALID_ESCAPED_QUOTE_RE.sub("'", raw)


async def decide_next_action(
    system_prompt: str, user_prompt: str, model: str | None = None, temperature: float | None = None
) -> tuple[AgentAction, str]:
    """Decide the next discovery action. Returns (parsed_action, raw_json_string) —
    the raw string is kept for the transcript even though it's already
    validated, since the transcript is meant to preserve the model's actual
    output for audit/debugging.
    """
    return await _decide_structured(
        system_prompt, user_prompt, AgentAction, ACTION_JSON_SCHEMA, model, temperature
    )


async def decide_capability_choice(
    system_prompt: str, user_prompt: str, model: str | None = None, temperature: float | None = None
) -> tuple[CapabilityChoice, str]:
    """Decide which capability (if any) a chatbot message maps to, and with
    what args. Same idea as decide_next_action, different question."""
    return await _decide_structured(
        system_prompt,
        user_prompt,
        CapabilityChoice,
        CAPABILITY_CHOICE_JSON_SCHEMA,
        model,
        temperature,
    )


async def _decide_structured(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    json_schema: dict,
    model: str | None,
    temperature: float | None,
) -> tuple[T, str]:
    """Shared provider dispatch for every structured-output call in this
    project. Dispatches to Ollama (default) or Claude
    (FALLBACK_LLM_PROVIDER=anthropic), same signature either way so nothing
    upstream needs to know which provider is active.
    """
    if FALLBACK_LLM_PROVIDER == "anthropic":
        return await _decide_structured_anthropic(
            system_prompt, user_prompt, response_model, json_schema, model, temperature
        )
    return await _decide_structured_ollama(
        system_prompt, user_prompt, response_model, json_schema, model, temperature
    )


async def _decide_structured_ollama(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    json_schema: dict,
    model: str | None,
    temperature: float | None,
) -> tuple[T, str]:
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": json_schema,
        "stream": False,
        "options": {"temperature": temperature if temperature is not None else LLM_TEMPERATURE},
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach Ollama at {OLLAMA_BASE_URL}: {e}") from e
    if resp.status_code != 200:
        raise LLMError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    raw_content = data["message"]["content"]
    try:
        parsed = response_model.model_validate_json(_fix_invalid_json_escapes(raw_content))
    except Exception as e:
        raise LLMError(f"Model returned invalid {response_model.__name__} JSON: {raw_content!r}") from e
    return parsed, raw_content


async def _decide_structured_anthropic(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    json_schema: dict,
    model: str | None,
    temperature: float | None,
) -> tuple[T, str]:
    # temperature is deliberately unused here: this SDK generation (Claude 5)
    # has no temperature parameter at all - confirmed empirically, not just
    # undocumented. Sampling control in this API is effort-based instead,
    # which isn't a like-for-like swap, so it's left alone rather than
    # guessed at.
    del temperature
    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model=model or ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": json_schema}},
            # Disabled on purpose - this model thinks by default even when
            # not asked to, and on a harder page that thinking can eat the
            # whole token budget before it produces any real answer (we saw
            # a response with only a thinking block, no answer at all).
            # Both response models already have their own reasoning field,
            # so we don't need extended thinking for this kind of quick
            # decision anyway.
            thinking={"type": "disabled"},
        )
    except anthropic.APIError as e:
        raise LLMError(f"Could not reach Anthropic API: {e}") from e

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        raise LLMError(f"Model did not return a text block: {response.content!r}")

    raw_content = text_block.text
    try:
        parsed = response_model.model_validate_json(_fix_invalid_json_escapes(raw_content))
    except Exception as e:
        raise LLMError(f"Model returned invalid {response_model.__name__} JSON: {raw_content!r}") from e
    return parsed, raw_content
