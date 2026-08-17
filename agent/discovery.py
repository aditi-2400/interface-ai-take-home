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
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from agent.convert import convert_transcript
from agent.executor import execute_action
from agent.llm import LLMError, decide_next_action
from agent.observe import capture_observation
from agent.prompts import SYSTEM_PROMPT, build_user_prompt, summarize_action_for_history
from agent.transcript import Transcript, TranscriptStep
from artifacts import storage
from artifacts.models import Capability

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "discovery"


def _run_id(capability_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}_{stamp}"


async def run_discovery(
    goal: str,
    start_url: str,
    capability_id: str,
    target_app: str,
    description: str,
    max_steps: int = 12,
    model: str | None = None,
    headless: bool = False,
) -> tuple[Transcript, Capability | None, Path]:
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
        context = await browser.new_context(base_url=origin)
        page = await context.new_page()
        await page.goto(start_url)
        await page.screenshot(path=str(screenshots_dir / "step_00_initial.png"))

        history: list[str] = []
        outcome = "max_steps_exceeded"
        final_summary = None

        for step_index in range(max_steps):
            observation = await capture_observation(page)
            user_prompt = build_user_prompt(goal, observation, history, step_index, max_steps)

            t0 = time.monotonic()
            try:
                action, raw = await decide_next_action(SYSTEM_PROMPT, user_prompt, model=model)
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

            result = await execute_action(page, action)
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

        await browser.close()

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.json").write_text(transcript.model_dump_json(indent=2) + "\n")

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
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    transcript, capability, run_dir = asyncio.run(
        run_discovery(
            goal=args.goal,
            start_url=args.start_url,
            capability_id=args.capability_id,
            target_app=args.target_app,
            description=args.description,
            max_steps=args.max_steps,
            model=args.model,
            headless=args.headless,
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
