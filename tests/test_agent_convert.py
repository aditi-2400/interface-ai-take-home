import pytest

from agent.action_schema import AgentAction, AgentLocator
from agent.convert import (
    _dedupe_consecutive_repeats,
    _diff_new_text,
    _infer_param_type,
    _is_risky,
    _slugify,
    _templatize_path,
    convert_transcript,
)
from agent.observe import Observation, ObservedElement
from agent.transcript import Transcript, TranscriptStep
from artifacts.models import Locator, Step


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Destination account ID", "destination_account_id"),
        ("Transfer amount in dollars", "transfer_amount_in_dollars"),
        ("  Weird!!  Spacing__here  ", "weird_spacing_here"),
        ("", "value"),
    ],
)
def test_slugify(raw, expected):
    assert _slugify(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("1001", "integer"), ("25.00", "decimal"), ("checking", "string"), ("", "string")],
)
def test_infer_param_type(raw, expected):
    assert _infer_param_type(raw) == expected


def test_templatize_path_single_segment():
    templated, params, literals = _templatize_path("/accounts/1001/transfer")
    assert templated == "/accounts/{account_id}/transfer"
    assert len(params) == 1
    assert params[0].name == "account_id"
    assert params[0].type == "integer"
    assert literals == {"account_id": "1001"}


def test_templatize_path_multiple_numeric_segments_each_named_from_predecessor():
    templated, params, literals = _templatize_path("/accounts/1001/transfer/2001")
    assert templated == "/accounts/{account_id}/transfer/{transfer_id}"
    assert [p.name for p in params] == ["account_id", "transfer_id"]
    assert literals == {"account_id": "1001", "transfer_id": "2001"}


def test_templatize_path_disambiguates_same_predecessor():
    templated, params, literals = _templatize_path("/accounts/1001/accounts/2001")
    assert templated == "/accounts/{account_id}/accounts/{account_id_2}"
    assert [p.name for p in params] == ["account_id", "account_id_2"]


def test_templatize_path_unknown_prefix_falls_back():
    templated, params, literals = _templatize_path("/widgets/42")
    assert templated == "/widgets/{widget_id}"
    assert params[0].name == "widget_id"


def test_substitute_literals_templates_known_values():
    from agent.convert import _substitute_literals

    text = _substitute_literals(
        "View member 12345 (Dana Whitfield)", {"search_by_name_or_member_id": "12345"}
    )
    assert text == "View member {search_by_name_or_member_id} (Dana Whitfield)"


def test_substitute_literals_ignores_too_short_values():
    from agent.convert import _substitute_literals

    text = _substitute_literals("Account 1", {"digit": "1"})
    assert text == "Account 1"


def test_is_risky_flags_confirm_click():
    action = AgentAction(
        reasoning="x",
        action="click",
        locator=AgentLocator(role="link", value="Confirm Transfer"),
    )
    assert _is_risky(action) is True


def test_is_risky_false_for_non_click():
    action = AgentAction(
        reasoning="x",
        action="type",
        locator=AgentLocator(role="textbox", value="Amount"),
        input_value="5",
    )
    assert _is_risky(action) is False


def test_diff_new_text_finds_newly_appeared_text():
    prev = Observation(
        url="http://x/confirm",
        path="/confirm",
        elements=[ObservedElement(role="StaticText", name="Confirm Transfer")],
    )
    curr = Observation(
        url="http://x/success",
        path="/success",
        elements=[
            ObservedElement(role="StaticText", name="Confirm Transfer"),
            ObservedElement(role="StaticText", name="Transfer Complete"),
        ],
    )
    assert _diff_new_text(prev, curr) == "Transfer Complete"


def test_diff_new_text_none_when_nothing_new():
    obs = Observation(
        url="http://x", path="/x", elements=[ObservedElement(role="StaticText", name="Same Text")]
    )
    assert _diff_new_text(obs, obs) is None


def _obs(path: str, elements: list[tuple[str, str]]) -> Observation:
    return Observation(
        url=f"http://127.0.0.1:8000{path}",
        path=path,
        elements=[ObservedElement(role=r, name=n) for r, n in elements],
    )


def _step(index: int, obs: Observation, action: AgentAction, ok: bool = True) -> TranscriptStep:
    return TranscriptStep(
        step_index=index,
        observation=obs,
        raw_llm_response=action.model_dump_json(),
        action=action,
        execution_ok=ok,
        duration_seconds=1.0,
    )


def test_convert_transcript_end_to_end():
    transcript = Transcript(
        goal="Transfer $25.00 from this account to account 2001, then confirm.",
        start_url="http://127.0.0.1:8000/accounts/1001/transfer",
        model_used="gemma4:e2b",
        started_at="2026-08-17T00:00:00Z",
        finished_at="2026-08-17T00:05:00Z",
        outcome="success",
        final_summary="Transfer completed; success page shows Transfer Complete.",
        steps=[
            _step(
                0,
                _obs("/accounts/1001/transfer", [("textbox", "Destination account ID")]),
                AgentAction(
                    reasoning="x",
                    action="type",
                    locator=AgentLocator(role="textbox", value="Destination account ID"),
                    input_value="2001",
                ),
            ),
            _step(
                1,
                _obs("/accounts/1001/transfer", [("textbox", "Transfer amount in dollars")]),
                AgentAction(
                    reasoning="x",
                    action="type",
                    locator=AgentLocator(role="textbox", value="Transfer amount in dollars"),
                    input_value="25.00",
                ),
            ),
            _step(
                2,
                _obs("/accounts/1001/transfer", [("link", "Continue")]),
                AgentAction(
                    reasoning="x",
                    action="click",
                    locator=AgentLocator(role="link", value="Continue"),
                ),
            ),
            _step(
                3,
                _obs("/accounts/1001/transfer/confirm", [("StaticText", "Confirm Transfer")]),
                AgentAction(
                    reasoning="x",
                    action="click",
                    locator=AgentLocator(role="link", value="Confirm Transfer"),
                ),
            ),
            _step(
                4,
                _obs(
                    "/members/12345",
                    [("StaticText", "Confirm Transfer"), ("StaticText", "Transfer Complete")],
                ),
                AgentAction(reasoning="done", action="goal_complete", done_summary="Done."),
            ),
        ],
    )

    cap = convert_transcript(
        transcript,
        capability_id="transfer_funds",
        version=1,
        target_app="meridian-trust-core-banking",
        description="Transfer funds between two accounts.",
    )

    assert cap.steps[0].action == "navigate"
    assert cap.steps[0].value == "/accounts/{account_id}/transfer"
    assert {i.name for i in cap.inputs} == {
        "account_id",
        "destination_account_id",
        "transfer_amount_in_dollars",
    }
    assert cap.success_checkpoint == "text_contains:Transfer Complete"
    assert cap.risk_level == "risky"
    confirm_step = [s for s in cap.steps if s.action == "click" and s.risky]
    assert len(confirm_step) == 1


def test_convert_transcript_templates_literal_reused_in_later_click():
    """Regression test for the lookup_member_balance run: a search-result
    link literally named "View member 12345 (Dana Whitfield)" must have the
    already-bound search value templated out, or the capability would only
    ever be able to look up member 12345.
    """
    transcript = Transcript(
        goal="Look up member 12345 and read their savings account balance.",
        start_url="http://127.0.0.1:8000/members/search",
        model_used="gemma4:e2b",
        started_at="2026-08-17T00:00:00Z",
        finished_at="2026-08-17T00:01:00Z",
        outcome="success",
        final_summary="Savings balance is $5000.00",
        steps=[
            _step(
                0,
                _obs("/members/search", [("textbox", "Search by name or member ID")]),
                AgentAction(
                    reasoning="x",
                    action="type",
                    locator=AgentLocator(role="textbox", value="Search by name or member ID"),
                    input_value="12345",
                ),
            ),
            _step(
                1,
                _obs("/members/search", [("link", "Search")]),
                AgentAction(reasoning="x", action="click", locator=AgentLocator(role="link", value="Search")),
            ),
            _step(
                2,
                _obs(
                    "/members/search?query=12345",
                    [("link", "View member 12345 (Dana Whitfield)")],
                ),
                AgentAction(
                    reasoning="x",
                    action="click",
                    locator=AgentLocator(role="link", value="View member 12345 (Dana Whitfield)"),
                ),
            ),
            _step(
                3,
                _obs("/members/12345", [("StaticText", "$5000.00")]),
                AgentAction(reasoning="done", action="goal_complete", done_summary="Done."),
            ),
        ],
    )

    cap = convert_transcript(
        transcript,
        capability_id="lookup_member_balance",
        version=1,
        target_app="meridian-trust-core-banking",
        description="Look up a member and read their savings balance.",
    )

    click_steps = [s for s in cap.steps if s.action == "click"]
    view_member_step = click_steps[-1]
    assert view_member_step.locator.value == "View member {search_by_name_or_member_id} (Dana Whitfield)"
    assert "12345" not in view_member_step.locator.value
    assert view_member_step.locator.fallback_strategies[0].value == (
        "View member {search_by_name_or_member_id} (Dana Whitfield)"
    )

    # Regression test for a real replay failure: "(Dana Whitfield)" is data
    # the app produced (a different member's search result has a different
    # name), so it won't generalize. A bare-placeholder fallback must be
    # present, at the top level (reachable by replay/step_executor.py's
    # flat resolve_locator loop), relying on Playwright's substring
    # accessible-name matching to find the link regardless of the name.
    fallback_values = [fb.value for fb in view_member_step.locator.fallback_strategies]
    assert "{search_by_name_or_member_id}" in fallback_values
    # And it must NOT be nested inside another fallback (dead, unreachable data).
    for fb in view_member_step.locator.fallback_strategies:
        assert fb.fallback_strategies == []


def test_dedupe_consecutive_repeats_collapses_identical_steps():
    """Regression test for the open_sub_account run: gemma4:e2b correctly
    selected a dropdown option, then redundantly repeated the identical
    select 3 more times before moving on. Replay shouldn't inherit that.
    """
    combobox = Locator(strategy="role", role="combobox", value="New account type")
    steps = [
        Step(action="navigate", value="/members/{member_id}/sub-accounts/new"),
        Step(action="select", locator=combobox, input_binding="new_account_type"),
        Step(action="select", locator=combobox, input_binding="new_account_type"),
        Step(action="select", locator=combobox, input_binding="new_account_type"),
        Step(
            action="type",
            locator=Locator(strategy="role", role="textbox", value="Initial deposit in dollars"),
            input_binding="initial_deposit_in_dollars",
        ),
    ]
    deduped = _dedupe_consecutive_repeats(steps)
    assert [s.action for s in deduped] == ["navigate", "select", "type"]


def test_dedupe_consecutive_repeats_keeps_non_adjacent_duplicates():
    combobox = Locator(strategy="role", role="combobox", value="New account type")
    textbox = Locator(strategy="role", role="textbox", value="Amount")
    steps = [
        Step(action="select", locator=combobox, input_binding="x"),
        Step(action="type", locator=textbox, input_binding="y"),
        Step(action="select", locator=combobox, input_binding="x"),
    ]
    deduped = _dedupe_consecutive_repeats(steps)
    assert len(deduped) == 3


def test_dedupe_consecutive_repeats_collapses_repeated_multi_step_block():
    """Regression test for a real, repeated (not one-off) discovery finding:
    across multiple live transfer_funds runs, gemma4:e4b re-typed the
    transfer amount and re-clicked Continue before reaching Confirm — a
    2-step block repeated once, which the old single-step-only dedup didn't
    catch, and which broke replay outright (the redundant 'type' step's
    field no longer exists once the flow has already advanced past it).
    """
    dest = Locator(strategy="role", role="textbox", value="Destination account ID")
    amount = Locator(strategy="role", role="textbox", value="Transfer amount in dollars")
    continue_link = Locator(strategy="role", role="link", value="Continue")
    confirm_link = Locator(strategy="role", role="link", value="Confirm Transfer")
    steps = [
        Step(action="navigate", value="/accounts/{account_id}/transfer"),
        Step(action="type", locator=dest, input_binding="destination_account_id"),
        Step(action="type", locator=amount, input_binding="transfer_amount_in_dollars"),
        Step(action="click", locator=continue_link),
        Step(action="type", locator=amount, input_binding="transfer_amount_in_dollars"),
        Step(action="click", locator=continue_link),
        Step(action="click", locator=confirm_link, risky=True),
    ]
    deduped = _dedupe_consecutive_repeats(steps)
    assert [s.action for s in deduped] == ["navigate", "type", "type", "click", "click"]
    assert deduped[-1].locator.value == "Confirm Transfer"


def test_convert_transcript_rejects_non_success():
    transcript = Transcript(
        goal="x",
        start_url="http://x/y",
        model_used="m",
        started_at="2026-08-17T00:00:00Z",
        outcome="stuck",
        steps=[],
    )
    with pytest.raises(ValueError, match="non-success"):
        convert_transcript(transcript, "cap", 1, "app", "desc")
