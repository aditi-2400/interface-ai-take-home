import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from replay.checkpoint import evaluate_checkpoint, match_business_outcome

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
async def test_text_contains_true(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await evaluate_checkpoint("text_contains:Member Search", page) is True


@pytest.mark.asyncio
async def test_text_contains_false(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await evaluate_checkpoint("text_contains:Nonexistent Text XYZ", page) is False


@pytest.mark.asyncio
async def test_text_not_contains(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await evaluate_checkpoint("text_not_contains:Nonexistent Text XYZ", page) is True
    assert await evaluate_checkpoint("text_not_contains:Member Search", page) is False


@pytest.mark.asyncio
async def test_url_path_is(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await evaluate_checkpoint("url_path_is:/members/search", page) is True
    assert await evaluate_checkpoint("url_path_is:/members/12345", page) is False


@pytest.mark.asyncio
async def test_url_path_contains(page):
    await page.goto(f"{MOCK_APP_URL}/members/12345")
    assert await evaluate_checkpoint("url_path_contains:12345", page) is True
    assert await evaluate_checkpoint("url_path_contains:99999", page) is False


@pytest.mark.asyncio
async def test_unknown_checkpoint_type_raises(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    with pytest.raises(ValueError, match="Unknown checkpoint type"):
        await evaluate_checkpoint("banana:foo", page)


@pytest.mark.asyncio
async def test_match_business_outcome_first_match_wins(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    outcomes = {
        "text_contains:Nonexistent": "wrong_one",
        "text_contains:Member Search": "right_one",
        "text_contains:Search": "would_also_match_but_second",
    }
    assert await match_business_outcome(page, outcomes) == "right_one"


@pytest.mark.asyncio
async def test_match_business_outcome_no_match_returns_none(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await match_business_outcome(page, {"text_contains:Nonexistent XYZ": "code"}) is None


@pytest.mark.asyncio
async def test_match_business_outcome_empty_dict_returns_none(page):
    await page.goto(f"{MOCK_APP_URL}/members/search")
    assert await match_business_outcome(page, {}) is None
