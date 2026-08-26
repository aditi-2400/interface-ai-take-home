"""Mark a draft capability approved, optionally declaring known business
outcomes and/or output fields.

All three are human decisions, never derived by discovery or replay — this
exists only so making them isn't manual JSON editing. Saves a new version
with the changes applied rather than mutating in place, consistent with
storage.save() never overwriting an existing version. known_business_outcomes
and outputs are both left empty by automatic conversion (agent/convert.py:
a single discovery run has no reliable, non-LLM way to tell "this is a
return value" from "this is decoration") and are exactly what a human
reviewer is expected to fill in before approval.
"""

import argparse

from artifacts import storage
from artifacts.models import Capability, Locator, OutputField


def approve(
    capability_id: str,
    version: int | None = None,
    known_business_outcomes: dict[str, str] | None = None,
    outputs: list[OutputField] | None = None,
) -> Capability:
    version = version or storage.latest_version(capability_id)
    if version is None:
        raise ValueError(f"No saved capability found for id {capability_id!r}")
    capability = storage.load(capability_id, version)
    if capability.approval_state == "approved":
        raise ValueError(f"{capability_id} v{version} is already approved")
    merged_outcomes = {**capability.known_business_outcomes, **(known_business_outcomes or {})}
    merged_outputs = _merge_outputs(capability.outputs, outputs or [])
    approved = capability.model_copy(
        update={
            "version": version + 1,
            "approval_state": "approved",
            "known_business_outcomes": merged_outcomes,
            "outputs": merged_outputs,
        }
    )
    storage.save(approved)
    return approved


def _merge_outputs(existing: list[OutputField], new: list[OutputField]) -> list[OutputField]:
    """Merge by name - a new declaration with the same name replaces the old one,
    same spirit as known_business_outcomes' dict merge."""
    merged = {o.name: o for o in existing}
    for field in new:
        merged[field.name] = field
    return list(merged.values())


def _parse_known_business_outcome(raw: str) -> tuple[str, str]:
    checkpoint, sep, code = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(
            f"expected CHECKPOINT_EXPR=OUTCOME_CODE, got {raw!r}"
        )
    return checkpoint, code


def _parse_output_field(raw: str) -> OutputField:
    # Fixed 5-field positional format, locator value always last (and never
    # further split) since it's the one piece realistically likely to
    # contain '|' itself (e.g. an xpath union). ROLE_OR_DASH is "-" for
    # strategies that don't need a role (css_fallback, text).
    parts = raw.split("|", maxsplit=4)
    if len(parts) != 5:
        raise argparse.ArgumentTypeError(
            "expected NAME|TYPE|LOCATOR_STRATEGY|ROLE_OR_DASH|LOCATOR_VALUE, got "
            f"{raw!r}"
        )
    name, type_, strategy, role_raw, value = parts
    role = None if role_raw == "-" else role_raw
    try:
        locator = Locator(strategy=strategy, value=value, role=role)
        return OutputField(name=name, type=type_, extraction_locator=locator)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"invalid --output {raw!r}: {e}") from e


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Approve a draft capability, saving it as a new version."
    )
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--version", type=int, default=None, help="Defaults to latest saved version.")
    parser.add_argument(
        "--known-business-outcome",
        action="append",
        default=[],
        metavar="CHECKPOINT_EXPR=OUTCOME_CODE",
        dest="known_business_outcomes",
        type=_parse_known_business_outcome,
        help='e.g. "text_contains:Insufficient funds=insufficient_funds". Repeatable.',
    )
    parser.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME|TYPE|LOCATOR_STRATEGY|ROLE_OR_DASH|LOCATOR_VALUE",
        dest="outputs",
        type=_parse_output_field,
        help='e.g. "savings_balance|decimal|css_fallback|-|xpath=//td[normalize-space(text())='
        "'Savings']/following-sibling::td[1]\". Repeatable.",
    )
    args = parser.parse_args()

    try:
        approved = approve(
            args.capability_id, args.version, dict(args.known_business_outcomes), args.outputs
        )
    except ValueError as e:
        raise SystemExit(str(e)) from e
    print(f"Approved: {approved.capability_id} v{approved.version}")


if __name__ == "__main__":
    _main()
