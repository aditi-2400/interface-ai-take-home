"""Deterministic replay engine. No LLM calls in this path, ever.

Given a saved Capability and input parameters: validates inputs, runs each
step via Playwright using the artifact's locator strategy (primary, then
fallback_strategies in order), checks each step's checkpoint if declared and
the capability's success_checkpoint at the end, and returns a three-way
ReplayResult — success / business_outcome / hard_failure. A business_outcome
is checked for on every failure path before anything is called a
hard_failure, so a legitimate result like "member not found" is never
conflated with a crash.

A third category exists in the spec but not the result contract:
"recoverable conditions" (e.g. dismiss a known interstitial). Recovery is
transparent by design — when it succeeds, replay just continues and the
final status is whatever it would have been anyway (usually "success"); the
result contract doesn't need a fourth status because a successfully-handled
hiccup isn't a distinct outcome from the caller's point of view. It only
becomes visible via the "recovered_from_interstitial" flag in the saved
evidence log. Each step gets exactly one recovery attempt: on failure
(execution error or checkpoint mismatch), check for the known dismissible
interstitial (replay/recovery.py); if found, dismiss it and retry the same
step once; if that also fails, or no known interstitial was found, classify
normally.

Evidence (structured log + a screenshot on failure) is written to
/evidence/replay/<run_id>/ on every run.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from artifacts.models import Capability, Step
from replay.checkpoint import evaluate_checkpoint, match_business_outcome
from replay.input_validation import ReplayInputError, validate_inputs
from replay.log import ReplayRunLog, ReplayStepLog
from replay.recovery import try_dismiss_known_interstitial
from replay.result import FailureDetail, ReplayResult
from replay.step_executor import execute_step, resolve_locator, substitute_params
from safety.allowlist import Allowlist
from safety.redaction import redact

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "replay"


def _redact_log_for_disk(log: ReplayRunLog) -> dict:
    """Redact only the genuinely data-bearing fields, not the whole
    structure — a blanket pass over every string field also mangles
    infrastructure metadata (base_url's port, ISO timestamps) that was
    never sensitive, which both hurts debuggability and, as observed
    empirically, still misses real values that happen not to match the
    amount/ID patterns' exact shape (e.g. a bare "1.00" with no $ prefix
    and too few digits for the ID pattern). Redacting each data field
    explicitly is more precise than trying to make the regexes clever
    enough to avoid every non-sensitive number everywhere.
    """
    data = log.model_dump()
    data["inputs"] = {k: redact(v) for k, v in data["inputs"].items()}
    for step in data["steps"]:
        if step.get("error"):
            step["error"] = redact(step["error"])
    result = data.get("result")
    if result:
        failure_detail = result.get("failure_detail")
        if failure_detail:
            if failure_detail.get("expected"):
                failure_detail["expected"] = redact(failure_detail["expected"])
            if failure_detail.get("observed"):
                failure_detail["observed"] = redact(failure_detail["observed"])
        if result.get("outputs"):
            result["outputs"] = {k: redact(v) for k, v in result["outputs"].items()}
    return data


def _target_url_for_step(step: Step, base_url: str, bound_inputs: dict[str, str], current_url: str) -> str:
    if step.action == "navigate":
        return urljoin(base_url, substitute_params(step.value, bound_inputs))
    return current_url


def _policy_block(reason: str, step_index: int) -> ReplayResult:
    return ReplayResult(
        status="hard_failure",
        failure_detail=FailureDetail(step_index=step_index, observed=reason),
    )


class _StepFailed(Exception):
    pass


async def _run_step(page, step: Step, bound_inputs: dict[str, str]) -> None:
    """Execute step and check its checkpoint, if declared. Raises _StepFailed on either."""
    try:
        await execute_step(page, step, bound_inputs)
    except Exception as e:  # noqa: BLE001 - normalized into _StepFailed for the caller
        raise _StepFailed(str(e)) from e
    if step.checkpoint and not await evaluate_checkpoint(step.checkpoint, page):
        raise _StepFailed(step.checkpoint)


async def _retry_step_after_dismiss(page, step: Step, bound_inputs: dict[str, str]) -> None:
    """Complete a step's verification after a successful interstitial dismiss.

    Dismissing an interstitial re-issues whatever request it intercepted, as
    a side effect of clicking the dismiss link/form (see mock_app/simulate.py
    and replay/recovery.py). For a "navigate" step, that intercepted request
    almost certainly WAS this step's own navigation — calling execute_step
    again would re-goto() the exact same URL, including its ?simulate=dialog
    query string, looping straight back into the interstitial instead of
    recovering from it. Only the checkpoint (if any) is re-checked against
    whatever page dismissing actually landed on. For every other action, the
    interstitial more likely intercepted a PRIOR step's request (undetected
    until this step's own locator couldn't be found), so this step's action
    still needs to run against the now-correct page dismissal revealed.
    """
    if step.action == "navigate":
        if step.checkpoint and not await evaluate_checkpoint(step.checkpoint, page):
            raise _StepFailed(step.checkpoint)
        return
    await _run_step(page, step, bound_inputs)


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
    # Redact only the on-disk copy — the ReplayResult returned to the caller
    # (the AI agent invoking this capability) needs real values to be
    # useful; only the persisted file is redacted.
    redacted_log = _redact_log_for_disk(log)
    (run_dir / "log.json").write_text(json.dumps(redacted_log, indent=2) + "\n")


async def replay_capability(
    capability: Capability,
    raw_inputs: dict,
    base_url: str,
    headless: bool = True,
    allowlist: Allowlist | None = None,
) -> ReplayResult:
    allowlist = allowlist or Allowlist.load()
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
                recovered = False

                target_url = _target_url_for_step(step, base_url, bound_inputs, page.url)
                decision = allowlist.check(target_url, step.action)
                if not decision.allowed:
                    result = _policy_block(f"blocked by allowlist: {decision.reason}", i)
                    log.steps.append(
                        ReplayStepLog(
                            step_index=i,
                            action=step.action,
                            ok=False,
                            error=result.failure_detail.observed,
                            duration_seconds=time.monotonic() - t0,
                        )
                    )
                    _save_log(log, result, run_dir)
                    return result

                if step.risky and capability.approval_state != "approved":
                    result = _policy_block(
                        f"blocked: risky step (action={step.action!r}) requires "
                        f"approval_state='approved', capability is {capability.approval_state!r}",
                        i,
                    )
                    log.steps.append(
                        ReplayStepLog(
                            step_index=i,
                            action=step.action,
                            ok=False,
                            error=result.failure_detail.observed,
                            duration_seconds=time.monotonic() - t0,
                        )
                    )
                    _save_log(log, result, run_dir)
                    return result

                try:
                    await _run_step(page, step, bound_inputs)
                except _StepFailed as first_error:
                    if await try_dismiss_known_interstitial(page):
                        recovered = True
                        try:
                            await _retry_step_after_dismiss(page, step, bound_inputs)
                        except _StepFailed as second_error:
                            log.steps.append(
                                ReplayStepLog(
                                    step_index=i,
                                    action=step.action,
                                    ok=False,
                                    error=str(second_error),
                                    duration_seconds=time.monotonic() - t0,
                                    recovered_from_interstitial=True,
                                )
                            )
                            result = await _classify_failure(
                                page, capability, i, str(second_error), screenshots_dir
                            )
                            _save_log(log, result, run_dir)
                            return result
                    else:
                        log.steps.append(
                            ReplayStepLog(
                                step_index=i,
                                action=step.action,
                                ok=False,
                                error=str(first_error),
                                duration_seconds=time.monotonic() - t0,
                            )
                        )
                        result = await _classify_failure(
                            page, capability, i, str(first_error), screenshots_dir
                        )
                        _save_log(log, result, run_dir)
                        return result

                log.steps.append(
                    ReplayStepLog(
                        step_index=i,
                        action=step.action,
                        ok=True,
                        duration_seconds=time.monotonic() - t0,
                        recovered_from_interstitial=recovered,
                    )
                )

            if not await evaluate_checkpoint(capability.success_checkpoint, page):
                if await try_dismiss_known_interstitial(page):
                    recovered_at_end = await evaluate_checkpoint(capability.success_checkpoint, page)
                else:
                    recovered_at_end = False
                if not recovered_at_end:
                    result = await _classify_failure(
                        page,
                        capability,
                        len(capability.steps) - 1,
                        capability.success_checkpoint,
                        screenshots_dir,
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
