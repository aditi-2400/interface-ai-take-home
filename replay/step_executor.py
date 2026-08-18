"""Executes one artifact Step against a live page. No LLM calls, ever.

Distinct from agent/executor.py (which resolves the LLM-facing AgentLocator
during discovery): this operates on the full artifacts.models.Locator,
including its fallback_strategies chain and {param} template substitution —
concerns that only exist at replay time.
"""

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from artifacts.models import Locator, Step

DEFAULT_ACTION_TIMEOUT_MS = 5_000
NAVIGATE_TIMEOUT_MS = 10_000

# Mirrors agent/executor.py's finding: "StaticText"/"heading" are Chrome's
# internal accessibility role names for plain text, not real ARIA roles —
# get_by_role("StaticText", ...) matches nothing. Plain text needs
# get_by_text() instead.
TEXT_ROLES = {"StaticText", "heading"}


class LocatorResolutionError(Exception):
    def __init__(self, attempted: list[str]):
        self.attempted = attempted
        super().__init__(f"no candidate locator resolved (tried: {', '.join(attempted)})")


def substitute_params(text: str, bound_inputs: dict[str, str]) -> str:
    for name, value in bound_inputs.items():
        text = text.replace("{" + name + "}", value)
    return text


def _build_pw_locator(page: Page, locator: Locator, bound_inputs: dict[str, str]):
    value = substitute_params(locator.value, bound_inputs)
    if locator.strategy == "role":
        if locator.role in TEXT_ROLES:
            return page.get_by_text(value, exact=False)
        return page.get_by_role(locator.role, name=value, exact=False)
    if locator.strategy == "text":
        return page.get_by_text(value, exact=False)
    if locator.strategy == "css_fallback":
        return page.locator(value)
    raise ValueError(f"Unknown locator strategy: {locator.strategy!r}")


async def resolve_locator(page: Page, locator: Locator, bound_inputs: dict[str, str]):
    """Try the primary locator, then each fallback_strategies entry in order.

    Raises LocatorResolutionError only once every candidate is exhausted —
    the exact "fail the step only if all are exhausted" rule from CLAUDE.md.
    """
    candidates = [locator, *locator.fallback_strategies]
    attempted: list[str] = []
    for candidate in candidates:
        pw_locator = _build_pw_locator(page, candidate, bound_inputs)
        attempted.append(f"{candidate.strategy}:{substitute_params(candidate.value, bound_inputs)}")
        try:
            await pw_locator.first.wait_for(timeout=DEFAULT_ACTION_TIMEOUT_MS, state="attached")
            return pw_locator
        except PlaywrightTimeoutError:
            continue
    raise LocatorResolutionError(attempted)


async def execute_step(page: Page, step: Step, bound_inputs: dict[str, str]) -> None:
    """Executes one step. Raises on failure; the caller (engine.py) classifies it."""
    if step.action == "navigate":
        path = substitute_params(step.value, bound_inputs)
        await page.goto(path, timeout=NAVIGATE_TIMEOUT_MS)
        return

    if step.action in {"click", "type", "select", "extract"}:
        pw_locator = await resolve_locator(page, step.locator, bound_inputs)
        if step.action == "click":
            await pw_locator.first.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        elif step.action == "type":
            value = bound_inputs.get(step.input_binding) if step.input_binding else step.value
            await pw_locator.first.fill(value or "", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        elif step.action == "select":
            value = bound_inputs.get(step.input_binding) if step.input_binding else step.value
            await pw_locator.first.select_option(value or "", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        elif step.action == "extract":
            pass  # presence already verified by resolve_locator
        return

    if step.action == "wait_for":
        if step.locator is not None:
            await resolve_locator(page, step.locator, bound_inputs)
        else:
            await page.wait_for_load_state("networkidle", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        return

    raise ValueError(f"Unknown step action: {step.action!r}")
