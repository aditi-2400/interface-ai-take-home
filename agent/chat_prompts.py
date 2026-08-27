"""Prompt construction for the chatbot's capability-choice decision.
Mirrors agent/prompts.py's shape - a fixed system prompt, and a per-turn
user prompt built from context (here: the capability catalog and
conversation history, instead of a page observation).
"""

from artifacts.models import Capability

MAX_CHAT_HISTORY_ENTRIES = 6

CHAT_SYSTEM_PROMPT = """You are a dispatcher for a set of typed, callable capabilities. Each one \
performs one real action against a live system (e.g. looking up a balance, transferring money). \
You never perform the action yourself - you only decide which ONE capability, if any, matches \
what the user asked for, and pull out its required inputs from what they said.

Rules:
- Only ever choose a capability_id that literally appears in the catalog below. Never invent one.
- Fill "inputs" using only the input names that capability declares, with values the user \
actually gave. Never make up a value for a required input they didn't provide.
- If the request is ambiguous, missing a required input, or doesn't match anything in the \
catalog, leave capability_id null and ask one clear, specific question in clarification_needed \
instead.
- Exactly one of capability_id and clarification_needed must be set, never both, never neither.
- Some capabilities do the same kind of thing (e.g. "look up a balance") but against different, \
unrelated real systems - each one's target_app says which. If more than one seems to match, use \
target_app plus any clue in the request (an ID's format, a system named, earlier turns) to pick \
the right one - don't just pick the first plausible-sounding match.
- If your last reply asked a clarifying question, treat the user's new message as ANSWERING that \
question, not as a brand new request - combine it with whatever they asked for earlier in the \
conversation, don't drop that original request.
"""


def _render_catalog(capabilities: list[Capability]) -> str:
    lines = ["Available capabilities:"]
    for cap in capabilities:
        lines.append(f"- {cap.capability_id} (target_app: {cap.target_app}): {cap.description}")
        if cap.inputs:
            params = ", ".join(
                f"{p.name} ({p.type}, {'required' if p.required else 'optional'})" for p in cap.inputs
            )
            lines.append(f"  inputs: {params}")
        else:
            lines.append("  inputs: none")
    return "\n".join(lines)


def build_chat_prompt(capabilities: list[Capability], history: list[str], message: str) -> str:
    lines = [_render_catalog(capabilities), ""]
    if history:
        recent = history[-MAX_CHAT_HISTORY_ENTRIES:]
        label = "Most recent turns" if len(recent) < len(history) else "Conversation so far"
        lines.append(f"{label}:")
        lines.extend(recent)
        lines.append("")
    lines.append(f"User: {message}")
    lines.append("")
    lines.append("Decide the capability choice as JSON matching the required schema.")
    return "\n".join(lines)


def summarize_turn_for_history(message: str, reply: str) -> str:
    return f'User: "{message}"\nAssistant: "{reply}"'
