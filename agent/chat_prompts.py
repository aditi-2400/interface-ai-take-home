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
- Member/account ID format reliably tells the two banking systems apart: target_app \
"meridian-core-live" uses 6-digit member numbers starting with 10 (e.g. 100234, 100987, \
101555); target_app "meridian-trust-core-banking" (the older mock app) uses different-looking \
numbers like 12345 or 67890. An ID matching one of these patterns is enough on its own to pick \
the right capability, even with no other context.
- If your last reply asked a clarifying question, treat the user's new message as ANSWERING that \
question, not as a brand new request - combine it with whatever they asked for earlier in the \
conversation, don't drop that original request.
- Copy ID-like values (share IDs, account IDs, etc.) EXACTLY as the user wrote them, even if part \
of it looks redundant with another input (e.g. a member number also appearing inside a share ID \
like "100234-MMKT-7"). These systems match on the full string - trimming a part that looks \
repetitive will make it fail to match anything real.
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
