import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from artifacts.models import Locator, Step
from replay.step_executor import (
    LocatorResolutionError,
    execute_step,
    resolve_locator,
    substitute_params,
)

pytestmark = pytest.mark.live

MOCK_APP_URL = "http://127.0.0.1:8000"


@pytest_asyncio.fixture()
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(base_url=MOCK_APP_URL)
        pg = await context.new_page()
        yield pg
        await browser.close()


def test_substitute_params_replaces_placeholder():
    assert substitute_params("/accounts/{account_id}/deposit", {"account_id": "1001"}) == (
        "/accounts/1001/deposit"
    )


def test_substitute_params_leaves_unmatched_text_alone():
    assert substitute_params("no placeholders here", {"account_id": "1001"}) == "no placeholders here"


@pytest.mark.asyncio
async def test_resolve_locator_primary_succeeds(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    locator = Locator(strategy="role", role="textbox", value="Search by name or member ID")
    pw_locator = await resolve_locator(page, locator, {})
    assert await pw_locator.first.get_attribute("name") == "query"


@pytest.mark.asyncio
async def test_resolve_locator_falls_back_when_primary_fails(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    locator = Locator(
        strategy="role",
        role="textbox",
        value="This Does Not Exist",
        fallback_strategies=[
            Locator(strategy="role", role="textbox", value="Search by name or member ID")
        ],
    )
    pw_locator = await resolve_locator(page, locator, {})
    assert await pw_locator.first.get_attribute("name") == "query"


@pytest.mark.asyncio
async def test_resolve_locator_raises_when_all_candidates_exhausted(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    locator = Locator(
        strategy="role",
        role="textbox",
        value="Nope",
        fallback_strategies=[Locator(strategy="text", value="Also Nope")],
    )
    with pytest.raises(LocatorResolutionError) as exc_info:
        await resolve_locator(page, locator, {})
    assert len(exc_info.value.attempted) == 2


@pytest.mark.asyncio
async def test_resolve_locator_substitutes_params_in_value(page):
    await page.goto(f"{MOCK_APP_URL}/members/12345")
    locator = Locator(strategy="role", role="link", value="Transfer from account {account_id} (checking)")
    pw_locator = await resolve_locator(page, locator, {"account_id": "1001"})
    assert await pw_locator.first.get_attribute("href") == "/accounts/1001/transfer"


@pytest.mark.asyncio
async def test_execute_step_navigate_substitutes_params(page):
    step = Step(action="navigate", value="/accounts/{account_id}/deposit")
    await execute_step(page, step, {"account_id": "1001"})
    assert "/accounts/1001/deposit" in page.url


@pytest.mark.asyncio
async def test_execute_step_type_uses_input_binding(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/deposit")
    step = Step(
        action="type",
        locator=Locator(strategy="role", role="textbox", value="Deposit amount in dollars"),
        input_binding="amount",
    )
    await execute_step(page, step, {"amount": "12.34"})
    value = await page.get_by_role("textbox", name="Deposit amount in dollars").input_value()
    assert value == "12.34"


@pytest.mark.asyncio
async def test_execute_step_click(page):
    await page.goto(f"{MOCK_APP_URL}/accounts/1001/deposit")
    step = Step(action="click", locator=Locator(strategy="role", role="link", value="Cancel"))
    await execute_step(page, step, {})
    assert "/members/12345" in page.url


@pytest.mark.asyncio
async def test_execute_step_unresolvable_locator_raises(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    step = Step(action="click", locator=Locator(strategy="role", role="link", value="Nonexistent Link"))
    with pytest.raises(LocatorResolutionError):
        await execute_step(page, step, {})
