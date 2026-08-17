"""Executes one AgentAction against a live Playwright page.

Action vocabulary intentionally mirrors artifacts.models.Step.action so
agent/convert.py can map 1:1 from a recorded AgentAction to a Step.
"""

from dataclasses import dataclass

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agent.action_schema import AgentAction, AgentLocator

DEFAULT_ACTION_TIMEOUT_MS = 5_000
NAVIGATE_TIMEOUT_MS = 10_000


@dataclass
class ExecutionResult:
    ok: bool
    error: str | None = None


# "StaticText"/"heading" are Chrome's internal accessibility-tree role names
# for plain text, surfaced to the model so it can target visible text with
# action="extract" — but they aren't valid Playwright/ARIA roles for
# querying (get_by_role("StaticText", ...) matches nothing). Plain text
# needs get_by_text() instead; every other role is a real ARIA role Playwright
# understands natively via get_by_role().
TEXT_ROLES = {"StaticText", "heading"}


def resolve_locator(page: Page, locator: AgentLocator):
    if locator.role in TEXT_ROLES:
        return page.get_by_text(locator.value, exact=False)
    return page.get_by_role(locator.role, name=locator.value, exact=False)


NEEDS_LOCATOR = {"click", "type", "select", "extract"}


async def execute_action(page: Page, action: AgentAction) -> ExecutionResult:
    if action.action in NEEDS_LOCATOR and action.locator is None:
        return ExecutionResult(
            ok=False,
            error=(
                f"action={action.action!r} requires a locator identifying which visible "
                "element to act on, but locator was null. You must set locator to one of the "
                "role/name pairs from the Visible elements list."
            ),
        )
    try:
        if action.action == "click":
            target = resolve_locator(page, action.locator)
            await target.first.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)

        elif action.action == "type":
            target = resolve_locator(page, action.locator)
            await target.first.fill(action.input_value or "", timeout=DEFAULT_ACTION_TIMEOUT_MS)

        elif action.action == "select":
            target = resolve_locator(page, action.locator)
            await target.first.select_option(
                action.input_value or "", timeout=DEFAULT_ACTION_TIMEOUT_MS
            )

        elif action.action == "navigate":
            await page.goto(action.input_value, timeout=NAVIGATE_TIMEOUT_MS)

        elif action.action == "wait_for":
            if action.locator is not None:
                target = resolve_locator(page, action.locator)
                await target.first.wait_for(timeout=DEFAULT_ACTION_TIMEOUT_MS)
            else:
                await page.wait_for_load_state("networkidle", timeout=DEFAULT_ACTION_TIMEOUT_MS)

        elif action.action == "extract":
            target = resolve_locator(page, action.locator)
            await target.first.wait_for(timeout=DEFAULT_ACTION_TIMEOUT_MS)

        else:
            return ExecutionResult(
                ok=False, error=f"execute_action called with terminal action {action.action!r}"
            )

        return ExecutionResult(ok=True)

    except PlaywrightTimeoutError:
        # Deliberately short and free of Playwright's raw multi-line "Call
        # log" stack trace: this string gets fed back into the model's next
        # prompt as history, and a wall of stack-trace text is exactly the
        # kind of noisy context that visibly degraded gemma4:e2b's later
        # decisions in practice (it started emitting garbled role/value
        # pairs immediately after seeing one of these).
        loc = action.locator
        target_desc = f'role={loc.role!r} name={loc.value!r}' if loc else "no locator"
        return ExecutionResult(
            ok=False, error=f"no element found matching {target_desc} — check it's spelled exactly as shown"
        )
    except Exception as e:  # noqa: BLE001 - execution errors become transcript data, not crashes
        return ExecutionResult(ok=False, error=f"{type(e).__name__} while executing {action.action}")
