"""Typed input validation/coercion against a Capability's declared InputParams.

Coercion just validates parseability (int/float/bool) — the bound value kept
for template substitution and typing into form fields stays the original
string the caller passed, so "25.00" doesn't silently become "25.0".
"""

from artifacts.models import Capability


class ReplayInputError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_type(raw: str, type_name: str) -> None:
    if type_name == "integer":
        int(raw)
    elif type_name == "decimal":
        float(raw)
    elif type_name == "boolean":
        if raw.lower() not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError(f"{raw!r} is not a valid boolean")
    # "string": always valid


def validate_inputs(capability: Capability, raw_inputs: dict) -> dict[str, str]:
    """Returns bound inputs (name -> string value) or raises ReplayInputError."""
    errors: list[str] = []
    bound: dict[str, str] = {}
    declared_names = {p.name for p in capability.inputs}

    for param in capability.inputs:
        if param.name not in raw_inputs or raw_inputs[param.name] is None:
            if param.required:
                errors.append(f"missing required input {param.name!r}")
            continue
        raw = str(raw_inputs[param.name])
        try:
            _check_type(raw, param.type)
        except (ValueError, TypeError):
            errors.append(f"input {param.name!r}={raw!r} is not a valid {param.type}")
            continue
        bound[param.name] = raw

    unknown = set(raw_inputs) - declared_names
    if unknown:
        errors.append(f"unknown inputs: {sorted(unknown)}")

    if errors:
        raise ReplayInputError(errors)
    return bound
