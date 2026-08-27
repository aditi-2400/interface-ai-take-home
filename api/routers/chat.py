"""Thin chatbot: one LLM call maps free text to a capability + args, then
invokes it through the exact same invoke_capability() capabilities.py
uses - no second LLM call for the summary, the reply is a deterministic
per-status template.

Session state is a plain in-memory dict, deliberately not persisted or
isolated across concurrent users - out of scope for this build, and not
something the brief asks for.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.chat_prompts import CHAT_SYSTEM_PROMPT, build_chat_prompt, summarize_turn_for_history
from agent.llm import LLMError, decide_capability_choice
from api.routers.capabilities import invoke_capability
from api.templating import templates
from artifacts import storage
from replay.result import ReplayResult

router = APIRouter(prefix="/chat", tags=["chat"])

_HISTORY: dict[str, list[str]] = {}


@router.get("", response_class=HTMLResponse)
def chat_ui(request: Request):
    """The conversational front door itself - a plain page, no build step,
    calling the POST endpoint below via fetch(). Kept thin on purpose: it's
    a demo driver over the API, not a second product."""
    return templates.TemplateResponse(request, "chat.html", {})


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    capability_id: str | None = None
    result: ReplayResult | None = None


def _render_output_value(key: str, value) -> str:
    if isinstance(value, list):
        lines = [f"{key}:"]
        lines.extend(f"  - {item}" for item in value)
        return "\n".join(lines)
    return f"{key}: {value}"


def _render_result(result: ReplayResult) -> str:
    if result.status == "success":
        if result.outputs:
            details = "\n".join(_render_output_value(k, v) for k, v in result.outputs.items())
            return f"Done —\n{details}"
        return "Done."
    if result.status == "business_outcome":
        return f"Couldn't complete that: {result.outcome_code}."
    if not result.failure_detail:
        return "Something went wrong."
    step = result.failure_detail.step_index
    observed = (result.failure_detail.observed or "").strip()
    # observed can be a whole page's text (or a raw Playwright error dump) -
    # a full-length wall of text in a chat reply is worse than a short
    # snippet; the dashboard's run-detail page has the full picture.
    snippet = observed.splitlines()[0][:150] if observed else None
    where = f" at step {step}" if step is not None else ""
    return f"Something went wrong{where}{f': {snippet}' if snippet else ''}."


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    history = _HISTORY.setdefault(request.session_id, [])
    catalog = storage.list_latest_capabilities()

    prompt = build_chat_prompt(catalog, history, request.message)
    try:
        choice, _ = await decide_capability_choice(CHAT_SYSTEM_PROMPT, prompt)
    except LLMError as e:
        reply = f"Sorry, I couldn't process that: {e}"
        history.append(summarize_turn_for_history(request.message, reply))
        return ChatResponse(reply=reply)

    if not choice.capability_id or choice.clarification_needed:
        reply = choice.clarification_needed or "Could you clarify what you'd like me to do?"
        history.append(summarize_turn_for_history(request.message, reply))
        return ChatResponse(reply=reply)

    catalog_ids = {c.capability_id for c in catalog}
    if choice.capability_id not in catalog_ids:
        reply = f"I tried to use {choice.capability_id!r}, which doesn't exist - can you rephrase?"
        history.append(summarize_turn_for_history(request.message, reply))
        return ChatResponse(reply=reply)

    capability = storage.load_latest(choice.capability_id)
    inputs = {kv.name: kv.value for kv in choice.inputs}
    try:
        result = await invoke_capability(capability, inputs)
    except ValueError as e:
        reply = f"Sorry, something's misconfigured: {e}"
        history.append(summarize_turn_for_history(request.message, reply))
        return ChatResponse(reply=reply, capability_id=choice.capability_id)

    reply = _render_result(result)
    history.append(summarize_turn_for_history(request.message, reply))
    return ChatResponse(reply=reply, capability_id=choice.capability_id, result=result)
