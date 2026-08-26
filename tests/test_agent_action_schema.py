import pytest
from pydantic import ValidationError

from agent.action_schema import ACTION_JSON_SCHEMA, MAX_FREE_TEXT_LENGTH, AgentAction, AgentLocator


def test_agent_locator_value_rejects_overlong_strings():
    with pytest.raises(ValidationError):
        AgentLocator(role="link", value="x" * (MAX_FREE_TEXT_LENGTH + 1))


def test_agent_locator_value_accepts_max_length():
    AgentLocator(role="link", value="x" * MAX_FREE_TEXT_LENGTH)  # must not raise


def test_agent_action_input_value_rejects_overlong_strings():
    """Regression test for a real live failure: at temperature=0, the model
    occasionally spirals into repeating the same phrase hundreds of times
    inside input_value, running past any reasonable length and truncating
    mid-string before the JSON could close - an unparseable response that
    crashed discovery outright. Capping the field's length (reflected into
    the JSON schema Ollama's grammar-constrained decoding is built from)
    forces early termination, confirmed live: the same failing scenario
    went from outcome="error" (crash) to outcome="max_steps_exceeded" (a
    normal, already-handled outcome) once this was added.
    """
    with pytest.raises(ValidationError):
        AgentAction(
            reasoning="x",
            action="type",
            locator=AgentLocator(role="textbox", value="Initial deposit in dollars"),
            input_value="25.00_the_input_value_is_25.00_to_correct_the_error " * 20,
        )


def test_max_length_reflected_in_exported_json_schema():
    """The Pydantic constraint only matters here because it's what Ollama's
    grammar-constrained decoding actually reads - confirm it's really in
    the exported schema, not just enforced locally after the fact."""
    input_value_schema = ACTION_JSON_SCHEMA["properties"]["input_value"]
    max_lengths = [s.get("maxLength") for s in input_value_schema["anyOf"] if "maxLength" in s]
    assert max_lengths == [MAX_FREE_TEXT_LENGTH]

    locator_value_schema = ACTION_JSON_SCHEMA["$defs"]["AgentLocator"]["properties"]["value"]
    assert locator_value_schema["maxLength"] == MAX_FREE_TEXT_LENGTH
