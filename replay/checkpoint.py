"""Deterministic evaluator for the checkpoint expression DSL (artifacts.models).

No LLM involved: every expression is either a plain-text substring check or a
URL-path check, both directly answerable from live page state.
"""

from urllib.parse import urlparse

from playwright.async_api import Page


async def evaluate_checkpoint(expr: str, page: Page) -> bool:
    check_type, _, expected = expr.partition(":")
    if check_type == "text_contains":
        text = await page.locator("body").inner_text()
        return expected in text
    if check_type == "text_not_contains":
        text = await page.locator("body").inner_text()
        return expected not in text
    if check_type == "url_path_is":
        return urlparse(page.url).path == expected
    if check_type == "url_path_contains":
        return expected in urlparse(page.url).path
    raise ValueError(f"Unknown checkpoint type: {check_type!r} (from {expr!r})")


async def match_business_outcome(page: Page, known_business_outcomes: dict[str, str]) -> str | None:
    """First matching pattern wins, in declared (insertion) order."""
    for expr, outcome_code in known_business_outcomes.items():
        if await evaluate_checkpoint(expr, page):
            return outcome_code
    return None
