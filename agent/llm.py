"""Ollama chat client for the discovery loop's structured-output decisions.

Calls Ollama's local REST API directly over HTTP (httpx) rather than the
`ollama` pip package, so the structured-output request (the `format` field,
Ollama's grammar-constrained generation) stays fully explicit — the model is
never asked to freelance valid JSON unaided.
"""

import os

import httpx

from agent.action_schema import ACTION_JSON_SCHEMA, AgentAction

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))


class LLMError(RuntimeError):
    pass


async def decide_next_action(
    system_prompt: str, user_prompt: str, model: str | None = None
) -> tuple[AgentAction, str]:
    """Call the local Ollama model with structured output.

    Returns (parsed_action, raw_json_string) — the raw string is kept for the
    transcript even though it's already validated, since the transcript is
    meant to preserve the model's actual output for audit/debugging.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": ACTION_JSON_SCHEMA,
        "stream": False,
        "options": {"temperature": 0.0},
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
        action = AgentAction.model_validate_json(raw_content)
    except Exception as e:
        raise LLMError(f"Model returned invalid AgentAction JSON: {raw_content!r}") from e
    return action, raw_content
