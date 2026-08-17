"""Capability artifact schema.

A Capability is the reusable, replayable unit an AI agent invokes: it captures
the ordered steps of a recorded flow, how each control was identified, typed
inputs/outputs, and a checkpoint the deterministic replay engine (Phase 4) can
verify without an LLM in the loop.

Checkpoint expression DSL
--------------------------
Every checkpoint / success_checkpoint / known_business_outcomes key is a
small, deterministically-evaluable string of the form "<check_type>:<expected>":

  - text_contains       page contains this visible text
  - text_not_contains    the negation
  - url_path_is          current URL path equals this exact path
  - url_path_contains    current URL path contains this substring

Keeping assertions in this DSL (rather than free English) means the replay
engine can evaluate every checkpoint deterministically, and a human reviewer
can read a capability and know exactly what "success" or a given business
outcome means without touching the live app.

Deliberate additions beyond the CLAUDE.md skeleton (see Phase 2 write-up for
the full rationale): Locator.role, Step.value/output_binding/risky, the
"select" action (for <select> dropdowns), the checkpoint DSL itself and its
validation, and cross-reference validation between steps and declared
inputs/outputs.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

CHECKPOINT_TYPES = ("text_contains", "text_not_contains", "url_path_is", "url_path_contains")


def _validate_checkpoint_expr(value: str) -> str:
    check_type, sep, _expected = value.partition(":")
    if not sep or check_type not in CHECKPOINT_TYPES:
        raise ValueError(
            f"checkpoint expression must be '<type>:<expected>' with type in "
            f"{CHECKPOINT_TYPES}, got {value!r}"
        )
    return value


CheckpointExpr = Annotated[str, AfterValidator(_validate_checkpoint_expr)]

ParamType = Literal["string", "integer", "decimal", "boolean"]


class Locator(BaseModel):
    """How to find a single control on the page.

    Preference order (most to least robust) is role > text > css_fallback,
    matching the target surface's reality: no ids/data-testids anywhere, so
    visible text / accessible name is the only reliable signal. css_fallback
    should only ever appear as a last-resort fallback_strategies entry (or,
    as in this project's mock app, for extracting a plain-text table cell
    that carries no independent accessible signal at all).
    """

    strategy: Literal["role", "text", "css_fallback"]
    value: str = Field(
        description="Accessible name (role) / visible text (text) / CSS selector (css_fallback)."
    )
    role: str | None = Field(
        default=None,
        description=(
            'ARIA role, e.g. "link", "textbox", "combobox". Required when strategy == "role".'
        ),
    )
    fallback_strategies: list["Locator"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _role_requires_role_field(self) -> "Locator":
        if self.strategy == "role" and not self.role:
            raise ValueError('Locator.role is required when strategy == "role"')
        return self


class Step(BaseModel):
    """One recorded action in a capability's flow."""

    action: Literal["click", "type", "select", "navigate", "wait_for", "extract"]
    locator: Locator | None = None
    input_binding: str | None = Field(
        default=None, description="Name of an InputParam supplying this step's runtime value."
    )
    value: str | None = Field(
        default=None,
        description=(
            "Literal, non-parameterized value. For 'navigate', the relative path to visit "
            "(may contain {param_name} placeholders resolved from bound inputs at replay "
            "time). For 'type'/'select', a literal fallback when input_binding isn't used."
        ),
    )
    output_binding: str | None = Field(
        default=None,
        description="Name of an OutputField this 'extract' step populates mid-flow, if any.",
    )
    checkpoint: CheckpointExpr | None = Field(
        default=None, description="Assertion verified after this step. Required for 'wait_for'."
    )
    risky: bool = Field(
        default=False,
        description=(
            "True for irreversible/risky actions (submit, confirm, delete). Discovery must "
            "get explicit confirmation before executing a risky step; replay gates risky "
            "steps on the capability's approval_state."
        ),
    )

    @model_validator(mode="after")
    def _validate_action_shape(self) -> "Step":
        needs_locator = {"click", "type", "select", "extract"}
        if self.action in needs_locator and self.locator is None:
            raise ValueError(f"Step.locator is required for action={self.action!r}")
        if self.action == "navigate":
            if self.locator is not None:
                raise ValueError("Step.locator must be None for action='navigate'")
            if not self.value:
                raise ValueError("Step.value (target path) is required for action='navigate'")
        if self.action in {"type", "select"} and not self.input_binding and not self.value:
            raise ValueError(
                f"Step with action={self.action!r} needs input_binding or a literal value"
            )
        if self.action == "wait_for" and not self.checkpoint:
            raise ValueError("Step.checkpoint is required for action='wait_for'")
        return self


class InputParam(BaseModel):
    name: str
    type: ParamType
    required: bool
    description: str


class OutputField(BaseModel):
    name: str
    type: ParamType
    extraction_locator: Locator


class Capability(BaseModel):
    """A recorded, replayable flow — the unit an AI agent invokes as a callable capability."""

    capability_id: str
    version: int = Field(ge=1)
    target_app: str = Field(
        description=(
            "Symbolic app/vendor-product identifier, NOT a base URL. The same capability "
            "should replay against any tenant's instance of this app given a separately "
            "resolved base URL, so every 'navigate' Step.value is a relative path."
        )
    )
    description: str
    inputs: list[InputParam]
    steps: list[Step] = Field(min_length=1)
    success_checkpoint: CheckpointExpr
    outputs: list[OutputField]
    risk_level: Literal["safe", "risky"]
    known_business_outcomes: dict[CheckpointExpr, str] = Field(
        default_factory=dict,
        description=(
            "Checkpoint expression -> outcome code (e.g. 'insufficient_funds'). Evaluated in "
            "insertion order; the first match wins."
        ),
    )
    approval_state: Literal["draft", "approved"] = "draft"

    @model_validator(mode="after")
    def _bindings_reference_known_names(self) -> "Capability":
        input_names = {i.name for i in self.inputs}
        output_names = {o.name for o in self.outputs}
        for i, step in enumerate(self.steps):
            if step.input_binding and step.input_binding not in input_names:
                raise ValueError(
                    f"steps[{i}].input_binding={step.input_binding!r} does not match any "
                    "declared input"
                )
            if step.output_binding and step.output_binding not in output_names:
                raise ValueError(
                    f"steps[{i}].output_binding={step.output_binding!r} does not match any "
                    "declared output"
                )
        return self
