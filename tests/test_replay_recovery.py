import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from replay.recovery import try_dismiss_known_interstitial

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


@pytest.mark.asyncio
async def test_dismisses_known_interstitial_and_resumes_original_request(page):
    await page.goto("/members/search?simulate=dialog")
    assert await try_dismiss_known_interstitial(page) is True
    # Dismissing a GET's dialog re-issues that same GET — back to the normal page.
    assert "Unexpected Confirmation" not in await page.locator("body").inner_text()


@pytest.mark.asyncio
async def test_returns_false_when_no_interstitial_present(page):
    await page.goto("/members/search")
    assert await try_dismiss_known_interstitial(page) is False


@pytest.mark.asyncio
async def test_returns_false_on_non_recoverable_interstitial(page):
    # perm_denied has no "Dismiss & Continue" link at all — genuinely a dead end.
    await page.goto("/members/12345?simulate=perm_denied")
    assert await try_dismiss_known_interstitial(page) is False
