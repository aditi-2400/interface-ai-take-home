"""Executor tests against a live Playwright browser + the mock app.

Requires the mock app server running on 127.0.0.1:8000 (these are excluded
from the default unit-test run via the `live` marker; run explicitly with
`pytest -m live` once the server is up).
"""

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agent.action_schema import AgentAction, AgentLocator
from agent.executor import execute_action, resolve_locator

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest_asyncio.fixture()
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        yield pg
        await browser.close()


@pytest.mark.asyncio
async def test_resolve_locator_finds_element_by_role_and_name(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    locator = resolve_locator(page, AgentLocator(role="textbox", value="Destination account ID"))
    await locator.first.wait_for(timeout=3000)
    assert await locator.first.get_attribute("name") == "to_account_id"


@pytest.mark.asyncio
async def test_execute_action_click_with_null_locator_reports_clear_error(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/transfer")
    action = AgentAction(reasoning="x", action="click", locator=None)
    result = await execute_action(page, action)
    assert result.ok is False
    assert "requires a locator" in result.error


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
