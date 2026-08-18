"""Phase 8 stretch: replay the same capability N times, report a flakiness
signal. Wraps replay_capability() as-is — no new execution path, no LLM.
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from artifacts.models import Capability
from replay.engine import replay_capability
from replay.result import ReplayResult

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "stability"


class RunOutcome(BaseModel):
    run_index: int
    status: str
    duration_seconds: float
    outcome_code: str | None = None
    failure_reason: str | None = None


class StabilityReport(BaseModel):
    capability_id: str
    version: int
    runs: int
    outcome_counts: dict[str, int]
    flaky: bool
    results: list[RunOutcome]


def _run_outcome(index: int, result: ReplayResult, duration: float) -> RunOutcome:
    return RunOutcome(
        run_index=index,
        status=result.status,
        duration_seconds=duration,
        outcome_code=result.outcome_code,
        failure_reason=result.failure_detail.observed if result.failure_detail else None,
    )


async def run_stability_check(
    capability: Capability,
    raw_inputs: dict,
    base_url: str,
    runs: int,
    headless: bool = True,
) -> StabilityReport:
    results = []
    for i in range(runs):
        t0 = time.monotonic()
        result = await replay_capability(capability, raw_inputs, base_url, headless=headless)
        results.append(_run_outcome(i, result, time.monotonic() - t0))

    outcome_counts = dict(Counter(r.status for r in results))
    return StabilityReport(
        capability_id=capability.capability_id,
        version=capability.version,
        runs=runs,
        outcome_counts=outcome_counts,
        flaky=len(outcome_counts) > 1,
        results=results,
    )


def _save_report(report: StabilityReport) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = EVIDENCE_ROOT / f"{report.capability_id}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.json"
    path.write_text(json.dumps(report.model_dump(), indent=2) + "\n")
    return path


def _main() -> None:
    from artifacts import storage

    parser = argparse.ArgumentParser(
        description="Replay the same capability N times and report a flakiness signal. No LLM."
    )
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--version", type=int, default=None, help="Defaults to latest saved version.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--input", action="append", default=[], metavar="NAME=VALUE", dest="inputs")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()

    raw_inputs = {}
    for item in args.inputs:
        name, _, value = item.partition("=")
        raw_inputs[name] = value

    version = args.version or storage.latest_version(args.capability_id)
    if version is None:
        raise SystemExit(f"No saved capability found for id {args.capability_id!r}")
    capability = storage.load(args.capability_id, version)

    report = asyncio.run(
        run_stability_check(capability, raw_inputs, args.base_url, args.runs, headless=not args.headed)
    )
    saved_path = _save_report(report)
    print(json.dumps(report.model_dump(), indent=2))
    print(f"\nsaved to {saved_path}")


if __name__ == "__main__":
    _main()
