"""Discovery agent loop: observe -> decide (LLM) -> act -> repeat.

Runs a real LLM against a real browser (Playwright) driving the mock app,
until the goal is met, the model reports it's stuck, or max_steps is hit.
Every step's raw observation + model response goes into a structured
transcript, saved to /evidence/discovery/<run_id>/ along with screenshots and
(on success) the resulting Capability artifact.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from agent.action_schema import AgentAction
from agent.convert import convert_transcript
from agent.executor import execute_action
from agent.llm import LLMError, decide_next_action
from agent.observe import capture_observation
from agent.prompts import SYSTEM_PROMPT, build_user_prompt, summarize_action_for_history
from agent.transcript import Transcript, TranscriptStep
from artifacts import storage
from artifacts.models import Capability
from escalation import notify as enotify
from safety.allowlist import Allowlist
from safety.redaction import redact, redact_url
from safety.risk import is_risky_action

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "discovery"


def _redact_transcript_for_disk(transcript: Transcript) -> dict:
    """Redact only the genuinely data-bearing fields, not the whole
    structure — see replay/engine.py's _redact_log_for_disk for why a
    blanket pass over every string (including e.g. model_used, an enum-like
    "gemma4:e4b") is the wrong tool. Almost everything in a transcript IS
    data-bearing by nature (it exists to record exactly what the page showed
    and what the model decided), but step_index/duration/booleans/role
    enums/action-type enums stay untouched as plain structural metadata.
    """
    data = transcript.model_dump()
    data["goal"] = redact(data["goal"])
    if data.get("final_summary"):
        data["final_summary"] = redact(data["final_summary"])
    for step in data["steps"]:
        step["raw_llm_response"] = redact(step["raw_llm_response"])
        if step.get("execution_error"):
            step["execution_error"] = redact(step["execution_error"])

        action = step["action"]
        action["reasoning"] = redact(action["reasoning"])
        if action.get("input_value"):
            action["input_value"] = redact(action["input_value"])
        if action.get("done_summary"):
            action["done_summary"] = redact(action["done_summary"])
        if action.get("locator"):
            action["locator"]["value"] = redact(action["locator"]["value"])

        observation = step["observation"]
        # url carries scheme://host:port - a plain redact() would catch a
        # bare port number as a false-positive ID (same 4-10-digit shape),
        # so this one keeps that part untouched. path never has a port at
        # all, so plain redact() is correct there.
        observation["url"] = redact_url(observation["url"])
        observation["path"] = redact(observation["path"])
        for element in observation["elements"]:
            element["name"] = redact(element["name"])
    return data


def _run_id(capability_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}_{stamp}"


def _target_url_for_action(action: AgentAction, origin: str, current_url: str) -> str:
    if action.action == "navigate":
        return urljoin(origin + "/", action.input_value or "")
    return current_url


async def default_confirm_risky_action(action: AgentAction) -> bool:
    """Blocks on real operator input — discovery must get explicit
    confirmation before executing a risky action. Runs in a thread so the
    event loop (and Playwright's own background tasks) aren't blocked by
    the synchronous input() call.
    """
    target = action.locator.value if action.locator else action.input_value
    print(f"\n⚠ RISKY ACTION about to execute: {action.action} on {target!r}")
    print(f"  Reasoning: {action.reasoning}")
    response = await asyncio.to_thread(input, "  Confirm execution? [y/N]: ")
    return response.strip().lower() in ("y", "yes")


async def run_discovery(
    goal: str,
    start_url: str,
    capability_id: str,
    target_app: str,
    description: str,
    max_steps: int = 12,
    model: str | None = None,
    temperature: float | None = None,
    headless: bool = False,
    allowlist: Allowlist | None = None,
    confirm_risky_action=default_confirm_risky_action,
    load_storage_state_from: Path | None = None,
    save_storage_state_to: Path | None = None,
) -> tuple[Transcript, Capability | None, Path]:
    allowlist = allowlist or Allowlist.load()
    run_id = _run_id(capability_id)
    run_dir = EVIDENCE_ROOT / run_id
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    origin = f"{urlparse(start_url).scheme}://{urlparse(start_url).netloc}"
    transcript = Transcript(
        goal=goal,
        start_url=start_url,
        model_used=model or "unset",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        if load_storage_state_from:
            context = await browser.new_context(
                base_url=origin, storage_state=str(load_storage_state_from)
            )
        else:
            context = await browser.new_context(base_url=origin)
        page = await context.new_page()
        await page.goto(start_url)
        await page.screenshot(path=str(screenshots_dir / "step_00_initial.png"))

        history: list[str] = []
        last_successful_click_name: str | None = None
        outcome = "max_steps_exceeded"
        final_summary = None

        for step_index in range(max_steps):
            observation = await capture_observation(page)
            user_prompt = build_user_prompt(goal, observation, history, step_index, max_steps)

            t0 = time.monotonic()
            try:
                action, raw = await decide_next_action(
                    SYSTEM_PROMPT, user_prompt, model=model, temperature=temperature
                )
            except LLMError as e:
                outcome = "error"
                final_summary = f"LLM error: {e}"
                break
            duration = time.monotonic() - t0

            if action.action in {"goal_complete", "stuck"}:
                transcript.steps.append(
                    TranscriptStep(
                        step_index=step_index,
                        observation=observation,
                        raw_llm_response=raw,
                        action=action,
                        execution_ok=True,
                        duration_seconds=duration,
                    )
                )
                outcome = "success" if action.action == "goal_complete" else "stuck"
                final_summary = action.done_summary
                await page.screenshot(path=str(screenshots_dir / f"step_{step_index + 1:02d}_final.png"))
                break

            target_url = _target_url_for_action(action, origin, page.url)
            decision = allowlist.check(target_url, action.action)
            if not decision.allowed:
                transcript.steps.append(
                    TranscriptStep(
                        step_index=step_index,
                        observation=observation,
                        raw_llm_response=raw,
                        action=action,
                        execution_ok=False,
                        execution_error=f"blocked by allowlist: {decision.reason}",
                        duration_seconds=duration,
                    )
                )
                outcome = "error"
                final_summary = f"Blocked by allowlist: {decision.reason}"
                await page.screenshot(path=str(screenshots_dir / f"step_{step_index + 1:02d}_blocked.png"))
                break

            target_name = action.locator.value if action.locator else None
            if is_risky_action(action.action, target_name, last_successful_click_name):
                confirmed = await confirm_risky_action(action)
                if not confirmed:
                    transcript.steps.append(
                        TranscriptStep(
                            step_index=step_index,
                            observation=observation,
                            raw_llm_response=raw,
                            action=action,
                            execution_ok=False,
                            execution_error="risky action was not confirmed by the operator",
                            duration_seconds=duration,
                        )
                    )
                    outcome = "stuck"
                    final_summary = "A risky action was not confirmed by the operator; halting."
                    break

            result = await execute_action(page, action)
            if result.ok and action.action == "click":
                last_successful_click_name = target_name
            transcript.steps.append(
                TranscriptStep(
                    step_index=step_index,
                    observation=observation,
                    raw_llm_response=raw,
                    action=action,
                    execution_ok=result.ok,
                    execution_error=result.error,
                    duration_seconds=duration,
                )
            )
            history.append(
                summarize_action_for_history(step_index + 1, action, result.ok, result.error)
            )
            await page.screenshot(path=str(screenshots_dir / f"step_{step_index + 1:02d}.png"))

        transcript.outcome = outcome
        transcript.final_summary = final_summary
        transcript.finished_at = datetime.now(timezone.utc).isoformat()

        if save_storage_state_to:
            try:
                await context.storage_state(path=str(save_storage_state_to))
            except Exception:
                pass
        await browser.close()

    run_dir.mkdir(parents=True, exist_ok=True)
    # Redact only the on-disk copy — conversion below needs the real values
    # (a redacted "1001" can't be templated into a working artifact), and
    # this transcript.json is meant to be independently redacted from
    # whatever the (separate, not-persisted-here) fully raw in-memory
    # transcript would show.
    redacted_transcript = _redact_transcript_for_disk(transcript)
    (run_dir / "transcript.json").write_text(json.dumps(redacted_transcript, indent=2) + "\n")

    capability = None
    if transcript.outcome == "success":
        existing_version = storage.latest_version(capability_id)
        version = (existing_version or 0) + 1
        capability = convert_transcript(
            transcript,
            capability_id=capability_id,
            version=version,
            target_app=target_app,
            description=description,
        )
        storage.save(capability)
        (run_dir / "capability.json").write_text(capability.model_dump_json(indent=2) + "\n")
        # Best-effort and off the event loop, matching replay/engine.py's own
        # escalation notification - a fresh draft capability sitting
        # unreviewed is exactly the other real "needs a human" moment.
        await asyncio.to_thread(enotify.notify_approval_needed, capability_id, version)

    return transcript, capability, run_dir


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run the discovery agent loop against a live goal.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--start-url", required=True)
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--target-app", default="meridian-trust-core-banking")
    parser.add_argument("--description", required=True)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--model", default=None, help="Override OLLAMA_MODEL for this run.")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override LLM_TEMPERATURE (default 0.0) for this run. A small non-zero value "
        "gives the model a chance to escape a repetitive attractor it can otherwise get stuck "
        "in at temperature 0, at the cost of exact reproducibility.",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--auto-confirm-risky",
        action="store_true",
        help=(
            "Skip the interactive confirmation prompt for risky actions and auto-approve "
            "them instead. Off by default — discovery requires a real operator confirmation "
            "per CLAUDE.md's safety rule. Use only for scripted/automated runs; every "
            "auto-confirmation is still logged loudly, never silent."
        ),
    )
    parser.add_argument(
        "--load-storage-state",
        type=Path,
        default=None,
        help="Path to a Playwright storage_state JSON file to start this discovery run already "
        "signed in with (cookies/localStorage), instead of a fresh, unauthenticated context.",
    )
    parser.add_argument(
        "--save-storage-state",
        type=Path,
        default=None,
        help="Path to write the resulting storage_state JSON to after this run, for reuse by "
        "later discovery/replay runs (e.g. persisting a session cookie from a sign-on capability).",
    )
    args = parser.parse_args()

    async def _auto_confirm(action) -> bool:
        target = action.locator.value if action.locator else action.input_value
        print(f"\n⚠ RISKY ACTION auto-confirmed (--auto-confirm-risky): {action.action} on {target!r}")
        return True

    transcript, capability, run_dir = asyncio.run(
        run_discovery(
            goal=args.goal,
            start_url=args.start_url,
            capability_id=args.capability_id,
            target_app=args.target_app,
            description=args.description,
            max_steps=args.max_steps,
            model=args.model,
            temperature=args.temperature,
            headless=args.headless,
            confirm_risky_action=_auto_confirm if args.auto_confirm_risky else default_confirm_risky_action,
            load_storage_state_from=args.load_storage_state,
            save_storage_state_to=args.save_storage_state,
        )
    )

    print(f"Outcome: {transcript.outcome}")
    print(f"Steps taken: {len(transcript.steps)}")
    print(f"Evidence saved to: {run_dir}")
    if capability is not None:
        print(f"Capability saved: {capability.capability_id} v{capability.version}")
    else:
        print(f"Final summary: {transcript.final_summary}")


if __name__ == "__main__":
    _main()
