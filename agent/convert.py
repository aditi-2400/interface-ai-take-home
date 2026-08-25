"""Deterministic transcript -> Capability conversion. No LLM call in this path.

Design notes (see Phase 3 write-up for full rationale):

- The discovery loop's initial page load isn't itself a recorded AgentAction
  (the start URL is given as input, not decided), so this module synthesizes
  the capability's first Step as a "navigate" to the start path.
- Any numeric path segment in that start path becomes an InputParam (e.g.
  /accounts/1001/transfer -> /accounts/{account_id}/transfer), so the
  capability isn't hardcoded to the one account it happened to be recorded
  against.
- Every "type"/"select" action's literal value becomes a named InputParam
  too, named by slugifying the target element's accessible name.
- success_checkpoint is derived by diffing the observation shown right before
  the final action executed against the observation shown at goal_complete,
  and taking the first newly-appeared piece of visible text. This stays
  grounded in what the page actually rendered rather than the model's own
  paraphrase (done_summary), which is free text and not guaranteed to be a
  literal on-page substring.
- risk classification is safety.risk.is_risky_action — the same classifier
  agent/discovery.py uses to gate a live action on confirmation before
  execution, so what gets marked Step.risky here is exactly what discovery
  required a human to confirm, not a second, possibly-inconsistent judgment.
- known_business_outcomes and outputs are left empty by automatic
  conversion: a single happy-path discovery run has no evidence of what
  error copy looks like, and there's no reliable, non-LLM way to tell
  "this new text is a return value" from "this new text is decoration."
  Both are meant to be filled in by a human reviewer before approval — the
  artifact starts in approval_state="draft" precisely because of this.
- A discovered flow can reference a bound input's literal value again later
  in a way that isn't a navigate path or a typed field — e.g. search for
  member 12345, then click a result link literally named "View member 12345
  (Dana Whitfield)". Left alone, that click step would hardcode member 12345
  forever, defeating the entire point of a *reusable* capability. After all
  steps are built, a final deterministic pass finds every already-bound
  literal value reappearing in a later locator/step value and templates it
  to {param_name}, the same {param} convention navigate paths already use.
- The model sometimes re-attempts an action that already succeeded (observed
  with action="select": it correctly picked the dropdown option, then
  redundantly repeated the identical select 3 more times before moving on —
  harmless during discovery since re-selecting the same option is a no-op,
  but if left in the artifact, replay would pointlessly repeat it every
  single invocation). Consecutive steps that are identical after conversion
  (same action, locator, and input_binding) are collapsed to one.
"""

import re

from agent.action_schema import AgentAction
from agent.executor import _strip_trailing_punctuation
from agent.observe import Observation
from agent.transcript import Transcript
from artifacts.models import Capability, InputParam, Locator, Step
from safety.risk import is_risky_action

PATH_SEGMENT_PARAM_NAMES = {
    "members": "member_id",
    "accounts": "account_id",
}


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "value"


def _infer_param_type(value: str) -> str:
    try:
        int(value)
        return "integer"
    except ValueError:
        pass
    try:
        float(value)
        return "decimal"
    except ValueError:
        pass
    return "string"


def _templatize_path(path: str) -> tuple[str, list[InputParam], dict[str, str]]:
    segments = path.split("/")
    params: list[InputParam] = []
    literal_values: dict[str, str] = {}
    seen_names: set[str] = set()
    new_segments = []
    for i, seg in enumerate(segments):
        if seg.isdigit():
            preceding = segments[i - 1] if i > 0 else ""
            base_name = PATH_SEGMENT_PARAM_NAMES.get(
                preceding, f"{preceding.rstrip('s')}_id" if preceding else "id"
            )
            name = base_name
            suffix = 2
            while name in seen_names:
                name = f"{base_name}_{suffix}"
                suffix += 1
            seen_names.add(name)
            params.append(
                InputParam(
                    name=name,
                    type=_infer_param_type(seg),
                    required=True,
                    description=f"Numeric path parameter following the {preceding or '(root)'!r} route segment.",
                )
            )
            literal_values[name] = seg
            new_segments.append("{" + name + "}")
        else:
            new_segments.append(seg)
    return "/".join(new_segments), params, literal_values


MIN_TEMPLATE_LITERAL_LENGTH = 2


def _substitute_literals(text: str, literal_values: dict[str, str]) -> str:
    for name, literal in sorted(literal_values.items(), key=lambda kv: -len(kv[1])):
        if len(literal) < MIN_TEMPLATE_LITERAL_LENGTH:
            continue
        if literal in text:
            text = text.replace(literal, "{" + name + "}")
    return text


def _substitute_locator_value_only(locator: Locator | None, literal_values: dict[str, str]) -> Locator | None:
    """Recursively substitute {param} placeholders into every value in the
    chain, without adding any new fallback entries — the building block
    _substitute_locator uses so the top-level-only fallback it adds never
    gets applied a second time to entries that are themselves fallbacks."""
    if locator is None:
        return None
    return Locator(
        strategy=locator.strategy,
        value=_substitute_literals(locator.value, literal_values),
        role=locator.role,
        fallback_strategies=[
            _substitute_locator_value_only(fb, literal_values) for fb in locator.fallback_strategies
        ],
    )


def _substitute_locator(locator: Locator | None, literal_values: dict[str, str]) -> Locator | None:
    if locator is None:
        return None
    substituted = _substitute_locator_value_only(locator, literal_values)

    # A bound literal templated as only PART of this value (e.g. "View
    # member {id} (Dana Whitfield)") leaves the untemplated remainder
    # hardcoded to whatever discovery happened to see — a search result's
    # displayed name is data the app produced, not something the caller
    # supplies. That remainder won't generalize to a different invocation.
    # Playwright's get_by_role(..., exact=False) already does substring
    # accessible-name matching, so a fallback whose value is JUST the
    # placeholder (resolved to the runtime literal alone at replay time)
    # finds the right element regardless of what surrounds it — verified
    # empirically: get_by_role("link", name="67890", exact=False) uniquely
    # matches "View member 67890 (Miguel Torres)" with no ambiguity. Added
    # once here, at the top level only, so it's a direct sibling in
    # fallback_strategies that replay/step_executor.py's flat resolve_locator
    # loop will actually reach — nesting it inside an existing fallback
    # would leave it unreachable dead data.
    extra_fallbacks = []
    for name, literal in literal_values.items():
        if len(literal) < MIN_TEMPLATE_LITERAL_LENGTH:
            continue
        placeholder = "{" + name + "}"
        if placeholder in substituted.value and substituted.value != placeholder:
            extra_fallbacks.append(Locator(strategy=locator.strategy, value=placeholder, role=locator.role))

    return substituted.model_copy(
        update={"fallback_strategies": substituted.fallback_strategies + extra_fallbacks}
    )


def _step_identity(step: Step) -> tuple:
    loc = step.locator
    return (
        step.action,
        loc.role if loc else None,
        loc.value if loc else None,
        step.input_binding,
        step.value,
    )


def _dedupe_consecutive_repeats(steps: list[Step]) -> list[Step]:
    """Collapse a repeated contiguous block of steps, not just a single step
    repeated back-to-back — the model doesn't only re-attempt one action, it
    sometimes redundantly repeats a whole short sequence (observed
    repeatedly, independently, across multiple real transfer_funds discovery
    runs: type an amount, click Continue, then do the exact same
    type-then-click again before finally reaching Confirm). Checked
    block-size-first from largest to smallest after each step is appended,
    so a length-2 repeat is caught as a length-2 block rather than only
    half-collapsing via two separate length-1 checks.
    """
    deduped: list[Step] = []
    for step in steps:
        deduped.append(step)
        window = len(deduped) // 2
        while window > 0:
            recent = deduped[-window:]
            preceding = deduped[-2 * window : -window]
            if [_step_identity(s) for s in recent] == [_step_identity(s) for s in preceding]:
                del deduped[-window:]
                break
            window -= 1
    return deduped


def _is_risky(action: AgentAction) -> bool:
    return is_risky_action(action.action, action.locator.value if action.locator else None)


def _to_artifact_locator(agent_locator) -> Locator:
    # Every AgentLocator recorded during discovery is role-based (see
    # agent/action_schema.py). A same-text fallback is added deterministically
    # here so replay has a degrade path if the role lookup ever fails.
    fallback_strategies = [Locator(strategy="text", value=agent_locator.value)]

    # Real, observed live failure: the model's structured output sometimes
    # doesn't reproduce a target name verbatim - a trailing hallucinated
    # character (e.g. "Continue-" for the real "Continue") - and
    # agent/executor.py's own retry-with-stripped-punctuation is what let
    # discovery recover live. But that retry only fixes the *live* action;
    # the recorded AgentLocator still carries the raw hallucinated value, so
    # without this, a saved artifact would replay-fail the exact same way
    # every single time (no LLM there to hallucinate around it). Adding a
    # stripped-value fallback here, once, at conversion time, means replay
    # self-heals via its own existing fallback chain - no replay-side code
    # needed at all.
    stripped = _strip_trailing_punctuation(agent_locator.value)
    if stripped != agent_locator.value:
        fallback_strategies.append(Locator(strategy="role", value=stripped, role=agent_locator.role))

    return Locator(
        strategy="role",
        value=agent_locator.value,
        role=agent_locator.role,
        fallback_strategies=fallback_strategies,
    )


def _diff_new_text(prev: Observation, curr: Observation) -> str | None:
    prev_names = {el.name for el in prev.elements}
    candidates = [
        el.name
        for el in curr.elements
        if el.role in {"StaticText", "heading"} and el.name not in prev_names and len(el.name) >= 4
    ]
    return candidates[0] if candidates else None


def convert_transcript(
    transcript: Transcript,
    capability_id: str,
    version: int,
    target_app: str,
    description: str,
) -> Capability:
    if transcript.outcome != "success":
        raise ValueError(
            f"Refusing to convert a non-success transcript (outcome={transcript.outcome!r})"
        )
    if len(transcript.steps) < 2:
        raise ValueError("Transcript too short to convert: need at least one real action")

    from urllib.parse import urlparse

    start_path = urlparse(transcript.start_url).path
    templated_start, path_inputs, literal_values = _templatize_path(start_path)

    inputs: list[InputParam] = list(path_inputs)
    steps: list[Step] = [Step(action="navigate", value=templated_start)]

    # transcript.steps[:-1] are the executed actions; the last entry is the
    # goal_complete/stuck decision, which isn't itself an executable Step.
    executable = transcript.steps[:-1]
    for t_step in executable:
        action = t_step.action
        if not t_step.execution_ok:
            # A failed attempt the agent recovered from isn't part of the
            # clean replay path.
            continue

        if action.action == "navigate":
            path = urlparse(action.input_value).path if "://" in (action.input_value or "") else (
                action.input_value or "/"
            )
            templated, extra_inputs, extra_literals = _templatize_path(path)
            for p in extra_inputs:
                if p.name not in {i.name for i in inputs}:
                    inputs.append(p)
                    literal_values[p.name] = extra_literals[p.name]
            steps.append(Step(action="navigate", value=templated))
            continue

        locator = _to_artifact_locator(action.locator) if action.locator else None
        input_binding = None
        if action.action in {"type", "select"} and action.locator is not None:
            param_name = _slugify(action.locator.value)
            if param_name not in {i.name for i in inputs}:
                inputs.append(
                    InputParam(
                        name=param_name,
                        type=_infer_param_type(action.input_value or ""),
                        required=True,
                        description=f'Value for the "{action.locator.value}" field.',
                    )
                )
                if action.input_value:
                    literal_values[param_name] = action.input_value
            input_binding = param_name

        steps.append(
            Step(
                action=action.action,
                locator=locator,
                input_binding=input_binding,
                risky=_is_risky(action),
            )
        )

    # Drop redundant repeats before anything else — the first Step in the
    # list (the synthesized initial navigate) is never a repeat of anything,
    # so this only ever collapses genuine agent re-attempts.
    steps = _dedupe_consecutive_repeats(steps)

    # Deterministic final pass: any later step whose locator happens to
    # repeat an already-bound input's literal value (e.g. a search-result
    # link literally named "View member 12345 (...)") gets that occurrence
    # templated to {param_name}, so the capability isn't silently hardcoded
    # to the one record it was recorded against. Steps that BIND a param
    # (their own locator carries the field's label, e.g. "Search by name or
    # member ID", never the literal value being entered) are unaffected.
    steps = [
        Step(
            action=s.action,
            locator=_substitute_locator(s.locator, literal_values),
            input_binding=s.input_binding,
            value=_substitute_literals(s.value, literal_values) if s.value else s.value,
            output_binding=s.output_binding,
            checkpoint=s.checkpoint,
            risky=s.risky,
        )
        for s in steps
    ]

    final_observation = transcript.steps[-1].observation
    prior_observation = executable[-1].observation if executable else final_observation
    success_text = _diff_new_text(prior_observation, final_observation)
    if success_text is None:
        raise ValueError(
            "Could not derive a success_checkpoint: no new visible text appeared between the "
            "last action and goal_complete. Conversion requires at least one distinguishing "
            "piece of new page text (see agent/convert.py's _diff_new_text)."
        )
    success_checkpoint = f"text_contains:{success_text}"

    risk_level = "risky" if any(s.risky for s in steps) else "safe"

    return Capability(
        capability_id=capability_id,
        version=version,
        target_app=target_app,
        description=description,
        inputs=inputs,
        steps=steps,
        success_checkpoint=success_checkpoint,
        outputs=[],
        risk_level=risk_level,
        known_business_outcomes={},
        approval_state="draft",
    )
