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

Escalation (Phase 7, opt-in via enable_escalation): a hard_failure or a
risky-action policy block raises an InterventionRequest and PAUSES — the
browser is launched with a remote debugging port and is never closed while
paused, so escalation/operator.py can connect to the exact same live session
from a genuinely separate process (verified empirically: the browser survives
independent of any one Playwright client disconnecting, but not independent
of the whole OS process that launched it exiting — so this coroutine blocks,
polling the intervention queue, keeping the process alive for as long as
the human takes). Allowlist violations are deliberately NOT escalation-
eligible — overriding an explicit security policy is the wrong response to
a policy violation, unlike being genuinely stuck. On resume, the current
page state is trusted and the run either continues to the next step or
re-checks the success_checkpoint; what the human did is captured via a
post-resolution screenshot and their own free-text note.

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
from uuid import uuid4

from playwright.async_api import async_playwright

from artifacts.models import Capability, Step
from escalation import notify as enotify
from escalation import queue as equeue
from escalation.models import InterventionRequest
from replay.checkpoint import evaluate_checkpoint, match_business_outcome
from replay.input_validation import ReplayInputError, validate_inputs
from replay.log import ReplayRunLog, ReplayStepLog
from replay.recovery import try_dismiss_known_interstitial
from replay.result import FailureDetail, ReplayResult
from replay.step_executor import execute_step, resolve_locator, substitute_params
from safety.allowlist import Allowlist
from safety.redaction import redact

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "replay"
DEFAULT_REMOTE_DEBUGGING_PORT = 9222


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


class _Escalated:
    """Sentinel: an intervention was raised and a human resolved it — the
    step loop should continue (re-evaluating state as normal), not treat
    this as a final result.
    """


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


class EscalationConfig:
    def __init__(
        self,
        cdp_port: int = DEFAULT_REMOTE_DEBUGGING_PORT,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float | None = None,
    ):
        self.cdp_port = cdp_port
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds


async def _wait_for_resolution(
    intervention_id: str, poll_interval: float, timeout: float | None
) -> InterventionRequest:
    elapsed = 0.0
    while True:
        current = equeue.get(intervention_id)
        if current.status in ("resolved", "expired"):
            return current
        if timeout is not None and elapsed >= timeout:
            return equeue.mark_expired(intervention_id)
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


async def _settle_after_resolution(page, screenshots_dir: Path, step_index: int) -> None:
    """Wait for the human's last action to settle before re-checking state —
    don't trust the operator tool to have waited on its own end."""
    try:
        await page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:  # noqa: BLE001 - best-effort settle; proceed regardless
        pass
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(screenshots_dir / f"resumed_after_step_{step_index}.png"))


async def _raise_intervention_and_wait(
    *,
    page,
    capability: Capability,
    step_index: int,
    reason: str,
    screenshot_path: str | None,
    bound_inputs: dict[str, str],
    base_url: str,
    run_dir: Path,
    escalation: EscalationConfig,
    log: ReplayRunLog,
) -> InterventionRequest:
    intervention = InterventionRequest(
        intervention_id=str(uuid4()),
        capability_id=capability.capability_id,
        version=capability.version,
        step_index=step_index,
        reason=reason,
        screenshot_path=screenshot_path,
        cdp_endpoint=f"http://localhost:{escalation.cdp_port}",
        bound_inputs=bound_inputs,
        base_url=base_url,
        run_dir=str(run_dir),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    equeue.create(intervention)
    log.escalations.append(intervention.intervention_id)
    # Best-effort and off the event loop (osascript/httpx are blocking calls) -
    # a notification failure must never stop the paused run from waiting.
    await asyncio.to_thread(enotify.notify, intervention.intervention_id, capability.capability_id, reason)
    return await _wait_for_resolution(
        intervention.intervention_id, escalation.poll_interval_seconds, escalation.timeout_seconds
    )


async def _handle_hard_failure_point(
    *,
    log: ReplayRunLog,
    capability: Capability,
    step_index: int,
    reason: str,
    page,
    bound_inputs: dict[str, str],
    base_url: str,
    run_dir: Path,
    screenshots_dir: Path,
    escalation: EscalationConfig | None,
) -> ReplayResult | type[_Escalated]:
    """Classify (business_outcome vs hard_failure). A business_outcome is
    always final. A hard_failure is final too UNLESS escalation is enabled,
    in which case an intervention is raised and this blocks until a human
    resolves it (or it times out) before returning _Escalated to signal the
    caller should re-evaluate state and continue, rather than stop here.
    """
    result = await _classify_failure(page, capability, step_index, reason, screenshots_dir)
    if result.status != "hard_failure" or escalation is None:
        _save_log(log, result, run_dir)
        return result

    resolution = await _raise_intervention_and_wait(
        page=page,
        capability=capability,
        step_index=step_index,
        reason=reason,
        screenshot_path=result.failure_detail.screenshot_path,
        bound_inputs=bound_inputs,
        base_url=base_url,
        run_dir=run_dir,
        escalation=escalation,
        log=log,
    )
    if resolution.status != "resolved":
        _save_log(log, result, run_dir)
        return result

    await _settle_after_resolution(page, screenshots_dir, step_index)
    return _Escalated


async def replay_capability(
    capability: Capability,
    raw_inputs: dict,
    base_url: str,
    headless: bool = True,
    allowlist: Allowlist | None = None,
    escalation: EscalationConfig | None = None,
    load_storage_state_from: Path | None = None,
    save_storage_state_to: Path | None = None,
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

    launch_args = [f"--remote-debugging-port={escalation.cdp_port}"] if escalation else []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, args=launch_args)
        if load_storage_state_from:
            context = await browser.new_context(
                base_url=base_url, storage_state=str(load_storage_state_from)
            )
        else:
            context = await browser.new_context(base_url=base_url)
        page = await context.new_page()

        try:
            i = 0
            while i < len(capability.steps):
                step = capability.steps[i]
                t0 = time.monotonic()
                recovered = False

                target_url = _target_url_for_step(step, base_url, bound_inputs, page.url)
                decision = allowlist.check(target_url, step.action)
                if not decision.allowed:
                    # Allowlist violations are never escalation-eligible: a
                    # human clicking through an explicit policy violation is
                    # the wrong response to one, unlike being genuinely stuck.
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
                    reason = (
                        f"blocked: risky step (action={step.action!r}) requires "
                        f"approval_state='approved', capability is {capability.approval_state!r}"
                    )
                    result = _policy_block(reason, i)
                    log.steps.append(
                        ReplayStepLog(
                            step_index=i,
                            action=step.action,
                            ok=False,
                            error=reason,
                            duration_seconds=time.monotonic() - t0,
                        )
                    )
                    if escalation is None:
                        _save_log(log, result, run_dir)
                        return result
                    resolution = await _raise_intervention_and_wait(
                        page=page,
                        capability=capability,
                        step_index=i,
                        reason=reason,
                        screenshot_path=None,
                        bound_inputs=bound_inputs,
                        base_url=base_url,
                        run_dir=run_dir,
                        escalation=escalation,
                        log=log,
                    )
                    if resolution.status != "resolved":
                        _save_log(log, result, run_dir)
                        return result
                    await _settle_after_resolution(page, screenshots_dir, i)
                    i += 1
                    continue

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
                            outcome = await _handle_hard_failure_point(
                                log=log,
                                capability=capability,
                                step_index=i,
                                reason=str(second_error),
                                page=page,
                                bound_inputs=bound_inputs,
                                base_url=base_url,
                                run_dir=run_dir,
                                screenshots_dir=screenshots_dir,
                                escalation=escalation,
                            )
                            if outcome is not _Escalated:
                                return outcome
                            i += 1
                            continue
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
                        outcome = await _handle_hard_failure_point(
                            log=log,
                            capability=capability,
                            step_index=i,
                            reason=str(first_error),
                            page=page,
                            bound_inputs=bound_inputs,
                            base_url=base_url,
                            run_dir=run_dir,
                            screenshots_dir=screenshots_dir,
                            escalation=escalation,
                        )
                        if outcome is not _Escalated:
                            return outcome
                        i += 1
                        continue

                log.steps.append(
                    ReplayStepLog(
                        step_index=i,
                        action=step.action,
                        ok=True,
                        duration_seconds=time.monotonic() - t0,
                        recovered_from_interstitial=recovered,
                    )
                )
                i += 1

            if not await evaluate_checkpoint(capability.success_checkpoint, page):
                if await try_dismiss_known_interstitial(page):
                    recovered_at_end = await evaluate_checkpoint(capability.success_checkpoint, page)
                else:
                    recovered_at_end = False
                if not recovered_at_end:
                    outcome = await _handle_hard_failure_point(
                        log=log,
                        capability=capability,
                        step_index=len(capability.steps) - 1,
                        reason=capability.success_checkpoint,
                        page=page,
                        bound_inputs=bound_inputs,
                        base_url=base_url,
                        run_dir=run_dir,
                        screenshots_dir=screenshots_dir,
                        escalation=escalation,
                    )
                    if outcome is not _Escalated:
                        return outcome
                    # Escalation resolved at the very last checkpoint: trust
                    # it and finalize as success rather than looping forever.

            outputs = await _extract_outputs(page, capability, bound_inputs)
            result = ReplayResult(status="success", outputs=outputs)
            _save_log(log, result, run_dir)
            return result
        finally:
            if save_storage_state_to:
                try:
                    await context.storage_state(path=str(save_storage_state_to))
                except Exception:
                    pass
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
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window. Works fine with --enable-escalation too - headed "
        "Chromium exposes its page over --cdp-port for an operator to reconnect to, same as "
        "headless. Defaults to headless mainly for practicality (no display required, faster).",
    )
    parser.add_argument(
        "--enable-escalation",
        action="store_true",
        help="On a hard_failure or risky-action block, pause and wait for an operator "
        "(escalation/operator.py) instead of finalizing immediately.",
    )
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_REMOTE_DEBUGGING_PORT)
    parser.add_argument(
        "--escalation-timeout", type=float, default=None, help="Seconds to wait before giving up."
    )
    parser.add_argument(
        "--load-storage-state",
        type=Path,
        default=None,
        help="Path to a Playwright storage_state JSON file to sign in with (cookies/localStorage) "
        "instead of starting from a fresh, unauthenticated browser context.",
    )
    parser.add_argument(
        "--save-storage-state",
        type=Path,
        default=None,
        help="Path to write the resulting storage_state JSON to after this run, for reuse by a "
        "later replay (e.g. persisting a session cookie across separate capability invocations).",
    )
    args = parser.parse_args()

    raw_inputs = {}
    for item in args.inputs:
        name, _, value = item.partition("=")
        raw_inputs[name] = value

    version = args.version or storage.latest_version(args.capability_id)
    if version is None:
        raise SystemExit(f"No saved capability found for id {args.capability_id!r}")
    capability = storage.load(args.capability_id, version)

    escalation = (
        EscalationConfig(cdp_port=args.cdp_port, timeout_seconds=args.escalation_timeout)
        if args.enable_escalation
        else None
    )

    result = asyncio.run(
        replay_capability(
            capability,
            raw_inputs,
            args.base_url,
            headless=not args.headed,
            escalation=escalation,
            load_storage_state_from=args.load_storage_state,
            save_storage_state_to=args.save_storage_state,
        )
    )
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    _main()
