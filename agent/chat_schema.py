"""Strict JSON schema the chatbot's LLM call must emit: which capability to
invoke (with what args), or a clarifying question if the request is
ambiguous. capability_id is a plain string, not validated against the live
catalog by the schema itself - simpler than generating a fresh schema per
request, and the caller checks it against the real catalog anyway.
"""

from pydantic import BaseModel, Field

from agent.action_schema import MAX_FREE_TEXT_LENGTH, _forbid_additional_properties, _force_all_fields_required


class InputKV(BaseModel):
    name: str
    value: str


class CapabilityChoice(BaseModel):
    reasoning: str = Field(
        description="Brief explanation of which capability was picked (or why none fits) and why."
    )
    capability_id: str | None = Field(
        default=None,
        description="The capability to invoke, exactly as it appears in the catalog. Null if "
        "asking a clarifying question instead of invoking anything.",
    )
    # A list of {name, value} pairs, not a dict[str, str] - Anthropic's
    # structured-output schema validation rejects open-ended objects
    # outright (every object needs a fixed set of properties), confirmed
    # live: "additionalProperties: object is not supported... set to false".
    # A fixed-shape list works the same way for both providers.
    inputs: list[InputKV] = Field(
        default_factory=list,
        description="The chosen capability's declared input names, each paired with a value "
        "pulled from the conversation. Empty if capability_id is null.",
    )
    clarification_needed: str | None = Field(
        default=None,
        max_length=MAX_FREE_TEXT_LENGTH,
        description="A question to ask the user instead of invoking anything, if the request is "
        "ambiguous, or a required input is missing, or no capability in the catalog fits. Null "
        "when capability_id is set.",
    )


CAPABILITY_CHOICE_JSON_SCHEMA = _forbid_additional_properties(
    _force_all_fields_required(CapabilityChoice.model_json_schema())
)
