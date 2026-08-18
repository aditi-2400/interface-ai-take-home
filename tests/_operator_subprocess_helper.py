"""Standalone script run as a genuinely separate OS process by
tests/test_escalation_engine.py, never as an in-process asyncio task.

Launching Chromium with --remote-debugging-port and then running any other
task on the SAME event loop stalls Playwright's CDP communication
indefinitely (verified empirically) — a separate process sidesteps that
and matches how the real operator (escalation/operator.py) connects.

Usage: python _operator_subprocess_helper.py <db_path> <capability_id> [--click "Name"]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

from escalation import queue as equeue


async def wait_for_intervention(capability_id: str, timeout: float = 15.0):
    elapsed = 0.0
    while elapsed < timeout:
        for request in equeue.list_pending():
            if request.capability_id == capability_id:
                return request
        await asyncio.sleep(0.2)
        elapsed += 0.2
    raise TimeoutError(f"no pending intervention for capability {capability_id!r} within timeout")


async def main() -> None:
    db_path = sys.argv[1]
    capability_id = sys.argv[2]
    click_name = None
    if "--click" in sys.argv:
        click_name = sys.argv[sys.argv.index("--click") + 1]

    equeue.DB_PATH = Path(db_path)

    request = await wait_for_intervention(capability_id)
    intervention_id = request.intervention_id
    print(f"[operator] connected to intervention {intervention_id}, step {request.step_index}")

    if click_name:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(request.cdp_endpoint)
            page = browser.contexts[0].pages[0]
            equeue.set_control_state(intervention_id, "human")
            await page.get_by_role("link", name=click_name).click()
            print(f"[operator] clicked {click_name!r}, now at {page.url}")
        finally:
            await pw.stop()

    equeue.mark_resolved(intervention_id, human_notes=f"test operator (click={click_name!r})")
    print("[operator] resolved")


asyncio.run(main())
