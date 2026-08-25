"""Strict JSON schema the discovery LLM must emit for each decided action.

Deliberately narrower than artifacts.models.Step: the model only needs to
describe what to do *right now*, in terms of what it can see in the current
observation (role + accessible name). Deterministic conversion into a full
Capability (fallback locator chains, input parameterization, checkpoints)
happens afterward in agent/convert.py, not here.
"""

from typing import Literal

from pydantic import BaseModel, Field

AgentActionType = Literal[
    "click", "type", "select", "navigate", "wait_for", "extract", "goal_complete", "stuck"
]

# Matches agent/observe.py's KEEP_ROLES exactly: every element the model is
# ever shown already comes with one of these roles attached, so it never
# needs to guess. Including StaticText/heading (not just the "actionable"
# roles) matters for action="extract" — a value worth reading, like an
# account balance, is plain visible text, not an interactive control. The
# first real run without StaticText in this enum left the model with no
# legal way to say "read that balance," and it burned its whole step budget
# retrying nonsense role="textbox" name="Balance" locators that don't exist.
# The model is never shown raw HTML/CSS, so it has no information to build a
# valid CSS selector from — offering a "css_fallback" strategy at discovery
# time only gives it a way to fail with no corresponding way to succeed.
# Constraining role to a closed enum (rather than a free string) also makes
# Ollama's grammar-constrained decoding structurally incapable of putting
# garbage like an accessible name into the role field, which is exactly what
# gemma4:e4b did once its context got noisy.
ObservedRole = Literal[
    "textbox", "link", "button", "combobox", "checkbox", "radio", "option",
    "StaticText", "heading",
]


class AgentLocator(BaseModel):
    role: ObservedRole
    value: str = Field(description="The exact accessible name, copied verbatim from the Visible elements list.")


class AgentAction(BaseModel):
    reasoning: str = Field(
        description="Brief explanation of why this action moves toward the goal. Written first "
        "so the model reasons before deciding."
    )
    action: AgentActionType
    locator: AgentLocator | None = Field(
        default=None, description="Target element. Required for click/type/select/extract."
    )
    input_value: str | None = Field(
        default=None,
        description=(
            "For 'type'/'select': the literal value to enter/choose. For 'navigate': the "
            "relative path to visit. Unused for other actions."
        ),
    )
    done_summary: str | None = Field(
        default=None,
        description=(
            "Only for action='goal_complete' or 'stuck': a one-sentence summary of the final "
            "state and, if the goal produced a value worth returning (e.g. a new balance), "
            "what it was and where it appeared on the page."
        ),
    )


def _force_all_fields_required(schema: dict) -> dict:
    """Make every property key mandatory in the emitted JSON, even nullable ones.

    Pydantic's model_json_schema() only lists fields without a default in
    "required" — fields with default=None (locator, input_value, done_summary)
    are left optional. Ollama's structured-output decoding treats "required"
    as authoritative for what must be emitted at all, independent of whether
    the field's type allows null: an optional-but-nullable field can be
    skipped entirely rather than emitted as null. In practice this let
    gemma4:e4b emit {"action": "type", "input_value": "2001"} with no
    "locator" key at all, despite its own reasoning describing a specific
    target field — a crash waiting to happen downstream, not a reasoning
    failure. Forcing every key into "required" (values can still be null)
    makes the model commit to an explicit locator/null on every single turn.
    """
    for definition in schema.get("$defs", {}).values():
        if "properties" in definition:
            definition["required"] = list(definition["properties"].keys())
    if "properties" in schema:
        schema["required"] = list(schema["properties"].keys())
    return schema


ACTION_JSON_SCHEMA = _force_all_fields_required(AgentAction.model_json_schema())
