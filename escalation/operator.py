"""Minimal operator surface — explicitly allowed to be mocked/minimal per
spec ("a CLI script or one plain HTML page is fine"). What's real here is
the handoff mechanism, not the UI polish: this connects to the *same* live
browser session via CDP (not a fresh one), lets a person drive it with real
Playwright actions, and signals resume back to the paused runner.

Reuses agent/observe.py's accessibility-tree capture for "show" — the same
signal the discovery agent itself decides from, so an operator sees exactly
what the automation was seeing.
"""

import argparse
import asyncio

from playwright.async_api import async_playwright

from agent.observe import capture_observation
from escalation import queue as equeue


async def _resolve_and_click(page, name: str) -> None:
    """Role-scoped, not text-scoped: substring text matching can silently
    resolve to a heading containing the target name instead of the actual
    link/button, with no error raised."""
    try:
        target = page.get_by_role("link", name=name, exact=False)
        await target.first.wait_for(timeout=2_000, state="attached")
    except Exception:  # noqa: BLE001 - fall back to button role
        target = page.get_by_role("button", name=name, exact=False)
    await target.first.click(timeout=5_000)
    await page.wait_for_load_state("networkidle", timeout=5_000)


async def _resolve_and_type(page, name: str, value: str) -> None:
    await page.get_by_role("textbox", name=name, exact=False).first.fill(value, timeout=5_000)


async def _run_repl(intervention_id: str) -> None:
    request = equeue.get(intervention_id)
    if request is None:
        print(f"No such intervention: {intervention_id!r}")
        return
    if request.status != "pending":
        print(f"Intervention {intervention_id!r} is not pending (status={request.status!r}).")
        return

    print(f"Intervention {intervention_id}")
    print(f"  capability: {request.capability_id} v{request.version}")
    print(f"  step_index: {request.step_index}")
    print(f"  reason:     {request.reason}")
    print(f"  connecting to live session at {request.cdp_endpoint} ...")

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(request.cdp_endpoint)
        context = browser.contexts[0]
        page = context.pages[0]
        equeue.set_control_state(intervention_id, "human")
        print(f"  connected. control_state=human. current page: {page.url}")
        print("Commands: show | click <name> | type <name> <value> | goto <path> | "
              "screenshot | resume [notes] | quit")

        while True:
            line = (await asyncio.to_thread(input, "operator> ")).strip()
            if not line:
                continue
            command, _, rest = line.partition(" ")
            command = command.lower()

            if command == "show":
                observation = await capture_observation(page)
                print(observation.render())
            elif command == "click":
                try:
                    await _resolve_and_click(page, rest)
                    print(f"clicked {rest!r}, now at {page.url}")
                except Exception as e:  # noqa: BLE001 - operator-facing error, not a crash
                    print(f"click failed: {e}")
            elif command == "type":
                name, _, value = rest.partition(" ")
                try:
                    await _resolve_and_type(page, name, value)
                    print(f"typed {value!r} into {name!r}")
                except Exception as e:  # noqa: BLE001
                    print(f"type failed: {e}")
            elif command == "goto":
                await page.goto(rest)
                print(f"now at {page.url}")
            elif command == "screenshot":
                path = f"{request.run_dir}/screenshots/operator_{intervention_id}.png"
                await page.screenshot(path=path)
                print(f"saved {path}")
            elif command == "resume":
                equeue.mark_resolved(intervention_id, human_notes=rest or None)
                print("marked resolved. control_state=agent. the paused runner will pick this up.")
                break
            elif command == "quit":
                print("disconnecting without resolving — the runner stays paused.")
                break
            else:
                print(f"unknown command: {command!r}")
    finally:
        # Disconnect only THIS client. The browser (and the runner still
        # awaiting resolution) must stay alive — never call browser.close()
        # here, that would kill the paused runner's session out from under it.
        await pw.stop()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Minimal operator console for paused replay sessions.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List pending interventions.")

    take_parser = sub.add_parser("take", help="Connect to a session and take control.")
    take_parser.add_argument("intervention_id")

    args = parser.parse_args()

    if args.command == "list":
        pending = equeue.list_pending()
        if not pending:
            print("No pending interventions.")
            return
        for request in pending:
            print(
                f"{request.intervention_id}  {request.capability_id} v{request.version}  "
                f"step={request.step_index}  reason={request.reason!r}"
            )
    elif args.command == "take":
        asyncio.run(_run_repl(args.intervention_id))


if __name__ == "__main__":
    _main()
