"""Executor tests. Most require a live Playwright browser + the mock app
(marked individually with @pytest.mark.live, excluded from the default
unit-test run; run explicitly with `pytest -m live` once the server is up).
The trailing-punctuation-stripping tests are pure-function and run in the
default fast suite.
"""

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agent.action_schema import AgentAction, AgentLocator
from agent.executor import _strip_trailing_punctuation, execute_action, resolve_locator

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Continue-", "Continue"),
        ("Continue", "Continue"),  # nothing to strip
        ("Confirm Transfer.", "Confirm Transfer"),
        ("Cancel!", "Cancel"),
        ("Continue — ", "Continue"),  # em dash + trailing space
        ("", ""),
    ],
)
def test_strip_trailing_punctuation(raw, expected):
    assert _strip_trailing_punctuation(raw) == expected


@pytest_asyncio.fixture()
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        yield pg
        await browser.close()


@pytest.mark.live
@pytest.mark.asyncio
async def test_resolve_locator_finds_element_by_role_and_name(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    locator = resolve_locator(page, AgentLocator(role="textbox", value="Destination account ID"))
    await locator.first.wait_for(timeout=3000)
    assert await locator.first.get_attribute("name") == "to_account_id"


@pytest.mark.live
@pytest.mark.asyncio
async def test_execute_action_click_with_null_locator_reports_clear_error(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    action = AgentAction(reasoning="x", action="click", locator=None)
    result = await execute_action(page, action)
    assert result.ok is False
    assert "requires a locator" in result.error


@pytest.mark.live
@pytest.mark.asyncio
async def test_execute_action_type_and_click_full_step(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    type_action = AgentAction(
        reasoning="x",
        action="type",
        locator=AgentLocator(role="textbox", value="Destination account ID"),
        input_value="2001",
    )
    result = await execute_action(page, type_action)
    assert result.ok is True

    click_action = AgentAction(
        reasoning="x",
        action="click",
        locator=AgentLocator(role="link", value="Cancel"),
    )
    result = await execute_action(page, click_action)
    assert result.ok is True
    assert "/members/12345" in page.url


@pytest.mark.live
@pytest.mark.asyncio
async def test_execute_action_recovers_from_hallucinated_trailing_character(page):
    """Regression test for a real live failure: the model's structured
    output named a click target "Continue-" when the real link is
    "Continue" - reasoning correctly said "Continue", but the locator.value
    field didn't reproduce it verbatim. Reproduces the exact wrong value
    seen live and confirms the retry recovers instead of failing the step.
    """
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    await execute_action(
        page,
        AgentAction(
            reasoning="x",
            action="type",
            locator=AgentLocator(role="textbox", value="Destination account ID"),
            input_value="2001",
        ),
    )
    await execute_action(
        page,
        AgentAction(
            reasoning="x",
            action="type",
            locator=AgentLocator(role="textbox", value="Transfer amount in dollars"),
            input_value="10.00",
        ),
    )

    result = await execute_action(
        page,
        AgentAction(reasoning="x", action="click", locator=AgentLocator(role="link", value="Continue-")),
    )

    assert result.ok is True
    # Same-path POST (form action="/accounts/{id}/transfer") renders the
    # confirm template at the same URL, not a redirect to a new path.
    assert "Confirm Transfer" in await page.locator("body").inner_text()
