"""Deterministic replay engine. No LLM calls in this path, ever.

Given a saved Capability and input parameters: validates inputs, runs each
step via Playwright using the artifact's locator strategy (primary, then
fallback_strategies in order), checks each step's checkpoint if declared and
the capability's success_checkpoint at the end, and returns a three-way
ReplayResult — success / business_outcome / hard_failure. A business_outcome
is checked for on every failure path before anything is called a
hard_failure, so a legitimate result like "member not found" is never
conflated with a crash. Evidence (structured log + a screenshot on failure)
is written to /evidence/replay/<run_id>/ on every run.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from artifacts.models import Capability
from replay.checkpoint import evaluate_checkpoint, match_business_outcome
from replay.input_validation import ReplayInputError, validate_inputs
from replay.log import ReplayRunLog, ReplayStepLog
from replay.result import FailureDetail, ReplayResult
from replay.step_executor import execute_step, resolve_locator

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "replay"


def _run_id(capability_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}_{stamp}"


async def _extract_outputs(page, capability: Capability, bound_inputs: dict[str, str]) -> dict:
    outputs = {}
    for field in capability.outputs:
        pw_locator = await resolve_locator(page, field.extraction_locator, bound_inputs)
        outputs[field.name] = (await pw_locator.first.inner_text()).strip()
    return outputs


async def _classify_failure(
    page, capability: Capability, step_index: int, expected: str, screenshots_dir: Path
) -> ReplayResult:
    outcome_code = await match_business_outcome(page, capability.known_business_outcomes)
    if outcome_code:
        return ReplayResult(status="business_outcome", outcome_code=outcome_code)

    screenshots_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshots_dir / f"failure_step_{step_index}.png"
    await page.screenshot(path=str(screenshot_path))
    observed = (await page.locator("body").inner_text())[:500]
    return ReplayResult(
        status="hard_failure",
        failure_detail=FailureDetail(
            step_index=step_index,
            expected=expected,
            observed=observed,
            screenshot_path=str(screenshot_path),
        ),
    )


def _save_log(log: ReplayRunLog, result: ReplayResult, run_dir: Path) -> None:
    log.result = result
    log.finished_at = datetime.now(timezone.utc).isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "log.json").write_text(log.model_dump_json(indent=2) + "\n")


async def replay_capability(
    capability: Capability,
    raw_inputs: dict,
    base_url: str,
    headless: bool = True,
) -> ReplayResult:
    run_dir = EVIDENCE_ROOT / _run_id(capability.capability_id)
    screenshots_dir = run_dir / "screenshots"

    log = ReplayRunLog(
        capability_id=capability.capability_id,
        version=capability.version,
        inputs=raw_inputs,
        base_url=base_url,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        bound_inputs = validate_inputs(capability, raw_inputs)
    except ReplayInputError as e:
        result = ReplayResult(
            status="hard_failure",
            failure_detail=FailureDetail(observed="; ".join(e.errors)),
        )
        _save_log(log, result, run_dir)
        return result

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(base_url=base_url)
        page = await context.new_page()

        try:
            for i, step in enumerate(capability.steps):
                t0 = time.monotonic()
                try:
                    await execute_step(page, step, bound_inputs)
                except Exception as e:  # noqa: BLE001 - every failure becomes classified evidence, never an uncaught crash
                    log.steps.append(
                        ReplayStepLog(
                            step_index=i,
                            action=step.action,
                            ok=False,
                            error=str(e),
                            duration_seconds=time.monotonic() - t0,
                        )
                    )
                    result = await _classify_failure(page, capability, i, str(e), screenshots_dir)
                    _save_log(log, result, run_dir)
                    return result

                log.steps.append(
                    ReplayStepLog(
                        step_index=i, action=step.action, ok=True, duration_seconds=time.monotonic() - t0
                    )
                )

                if step.checkpoint and not await evaluate_checkpoint(step.checkpoint, page):
                    result = await _classify_failure(
                        page, capability, i, step.checkpoint, screenshots_dir
                    )
                    _save_log(log, result, run_dir)
                    return result

            if not await evaluate_checkpoint(capability.success_checkpoint, page):
                result = await _classify_failure(
                    page, capability, len(capability.steps) - 1, capability.success_checkpoint, screenshots_dir
                )
                _save_log(log, result, run_dir)
                return result

            outputs = await _extract_outputs(page, capability, bound_inputs)
            result = ReplayResult(status="success", outputs=outputs)
            _save_log(log, result, run_dir)
            return result
        finally:
            await browser.close()


def _main() -> None:
    from artifacts import storage

    parser = argparse.ArgumentParser(description="Replay a saved capability artifact. No LLM.")
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--version", type=int, default=None, help="Defaults to latest saved version.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--input", action="append", default=[], metavar="NAME=VALUE", dest="inputs"
    )
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    raw_inputs = {}
    for item in args.inputs:
        name, _, value = item.partition("=")
        raw_inputs[name] = value

    version = args.version or storage.latest_version(args.capability_id)
    if version is None:
        raise SystemExit(f"No saved capability found for id {args.capability_id!r}")
    capability = storage.load(args.capability_id, version)

    result = asyncio.run(
        replay_capability(capability, raw_inputs, args.base_url, headless=args.headless)
    )
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    _main()
