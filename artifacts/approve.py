"""Mark a draft capability approved, optionally declaring known business outcomes.

Both are human decisions, never derived by discovery or replay — this
exists only so making them isn't manual JSON editing. Saves a new version
with the changes applied rather than mutating in place, consistent with
storage.save() never overwriting an existing version. known_business_outcomes
is left empty by automatic conversion (agent/convert.py: a single discovery
run has no evidence of what error copy looks like) and is exactly what a
human reviewer is expected to fill in before approval.
"""

import argparse

from artifacts import storage
from artifacts.models import Capability


def approve(
    capability_id: str,
    version: int | None = None,
    known_business_outcomes: dict[str, str] | None = None,
) -> Capability:
    version = version or storage.latest_version(capability_id)
    if version is None:
        raise ValueError(f"No saved capability found for id {capability_id!r}")
    capability = storage.load(capability_id, version)
    if capability.approval_state == "approved":
        raise ValueError(f"{capability_id} v{version} is already approved")
    merged_outcomes = {**capability.known_business_outcomes, **(known_business_outcomes or {})}
    approved = capability.model_copy(
        update={
            "version": version + 1,
            "approval_state": "approved",
            "known_business_outcomes": merged_outcomes,
        }
    )
    storage.save(approved)
    return approved


def _parse_known_business_outcome(raw: str) -> tuple[str, str]:
    checkpoint, sep, code = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(
            f"expected CHECKPOINT_EXPR=OUTCOME_CODE, got {raw!r}"
        )
    return checkpoint, code


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
    args = parser.parse_args()

    try:
        approved = approve(
            args.capability_id, args.version, dict(args.known_business_outcomes)
        )
    except ValueError as e:
        raise SystemExit(str(e)) from e
    print(f"Approved: {approved.capability_id} v{approved.version}")


if __name__ == "__main__":
    _main()
