import pytest

from artifacts.models import Capability, InputParam
from replay.input_validation import ReplayInputError, validate_inputs


def _capability(inputs: list[InputParam]) -> Capability:
    return Capability(
        capability_id="test_cap",
        version=1,
        target_app="test-app",
        description="test",
        inputs=inputs,
        steps=[{"action": "navigate", "value": "/x"}],
        success_checkpoint="text_contains:ok",
        outputs=[],
        risk_level="safe",
    )


def test_validate_inputs_coerces_and_binds_all_types():
    cap = _capability(
        [
            InputParam(name="a", type="integer", required=True, description="x"),
            InputParam(name="b", type="decimal", required=True, description="x"),
            InputParam(name="c", type="boolean", required=True, description="x"),
            InputParam(name="d", type="string", required=True, description="x"),
        ]
    )
    bound = validate_inputs(cap, {"a": "5", "b": "25.00", "c": "true", "d": "hello"})
    assert bound == {"a": "5", "b": "25.00", "c": "true", "d": "hello"}


def test_validate_inputs_missing_required_raises():
    cap = _capability([InputParam(name="a", type="integer", required=True, description="x")])
    with pytest.raises(ReplayInputError, match="missing required input 'a'"):
        validate_inputs(cap, {})


def test_validate_inputs_missing_optional_is_fine():
    cap = _capability([InputParam(name="a", type="integer", required=False, description="x")])
    bound = validate_inputs(cap, {})
    assert bound == {}


@pytest.mark.parametrize(
    "type_name,bad_value", [("integer", "abc"), ("decimal", "abc"), ("boolean", "maybe")]
)
def test_validate_inputs_bad_type_raises(type_name, bad_value):
    cap = _capability([InputParam(name="a", type=type_name, required=True, description="x")])
    with pytest.raises(ReplayInputError, match="not a valid"):
        validate_inputs(cap, {"a": bad_value})


def test_validate_inputs_unknown_input_raises():
    cap = _capability([InputParam(name="a", type="string", required=True, description="x")])
    with pytest.raises(ReplayInputError, match="unknown inputs"):
        validate_inputs(cap, {"a": "x", "surprise": "y"})


def test_validate_inputs_collects_multiple_errors():
    cap = _capability(
        [
            InputParam(name="a", type="integer", required=True, description="x"),
            InputParam(name="b", type="integer", required=True, description="x"),
        ]
    )
    with pytest.raises(ReplayInputError) as exc_info:
        validate_inputs(cap, {"b": "not-an-int"})
    assert len(exc_info.value.errors) == 2
