import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from artifacts.models import Capability, InputParam, Locator, OutputField, Step

EXAMPLE_PATH = Path(__file__).parent.parent / "artifacts" / "examples" / "transfer_v1.json"


def load_example() -> dict:
    return json.loads(EXAMPLE_PATH.read_text())


def test_hand_written_example_validates():
    cap = Capability.model_validate(load_example())
    assert cap.capability_id == "transfer_funds"
    assert cap.version == 1
    assert len(cap.steps) == 5
    assert cap.risk_level == "risky"


def test_capability_round_trips_through_json():
    cap = Capability.model_validate(load_example())
    reloaded = Capability.model_validate_json(cap.model_dump_json())
    assert reloaded == cap


def test_locator_role_strategy_requires_role_field():
    with pytest.raises(ValidationError, match="role is required"):
        Locator(strategy="role", value="Search")


def test_locator_text_strategy_does_not_require_role_field():
    loc = Locator(strategy="text", value="Confirm Deposit")
    assert loc.role is None


@pytest.mark.parametrize("action", ["click", "type", "select", "extract"])
def test_step_requires_locator_for_element_actions(action):
    kwargs = {"action": action}
    if action in {"type", "select"}:
        kwargs["input_binding"] = "amount"
    with pytest.raises(ValidationError, match="locator is required"):
        Step(**kwargs)


def test_step_navigate_forbids_locator():
    with pytest.raises(ValidationError, match="must be None"):
        Step(action="navigate", locator=Locator(strategy="text", value="x"), value="/foo")


def test_step_navigate_requires_value():
    with pytest.raises(ValidationError, match="Step.value"):
        Step(action="navigate")


def test_step_type_requires_binding_or_value():
    with pytest.raises(ValidationError, match="needs input_binding or a literal value"):
        Step(action="type", locator=Locator(strategy="text", value="Amount"))


def test_step_wait_for_requires_checkpoint():
    with pytest.raises(ValidationError, match="checkpoint is required"):
        Step(action="wait_for")


def test_checkpoint_expression_must_use_known_type():
    with pytest.raises(ValidationError, match="checkpoint expression"):
        Step(action="wait_for", checkpoint="banana:Deposit Complete")


def test_capability_rejects_unknown_input_binding():
    data = load_example()
    data["steps"][1]["input_binding"] = "does_not_exist"
    with pytest.raises(ValidationError, match="does not match any declared input"):
        Capability.model_validate(data)


def test_capability_rejects_unknown_output_binding():
    data = load_example()
    data["steps"][0]["action"] = "extract"
    data["steps"][0]["locator"] = {"strategy": "text", "value": "x"}
    data["steps"][0]["value"] = None
    data["steps"][0]["output_binding"] = "does_not_exist"
    with pytest.raises(ValidationError, match="does not match any declared output"):
        Capability.model_validate(data)


def test_capability_requires_at_least_one_step():
    data = load_example()
    data["steps"] = []
    with pytest.raises(ValidationError):
        Capability.model_validate(data)


def test_json_schema_exports_top_level_capability_fields():
    schema = Capability.model_json_schema()
    assert schema["title"] == "Capability"
    for field in (
        "capability_id",
        "version",
        "steps",
        "inputs",
        "outputs",
        "success_checkpoint",
        "risk_level",
        "approval_state",
    ):
        assert field in schema["properties"], field
