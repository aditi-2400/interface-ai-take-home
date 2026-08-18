"""Recovery from this target app's one documented recoverable interstitial.

Per mock_app/simulate.py's ?simulate=dialog design, every such interstitial
exposes a link with this exact accessible name, and dismissing it re-issues
the original request unchanged. This is deliberately narrow — replay knows
how to recover from THIS specific, documented app convention, not from
arbitrary unexpected pages. Guessing at unknown pages would be exactly the
"freelancing" this project avoids everywhere else; a real system would learn
new recoverable conventions the same way it learns everything else here —
via a real discovery/review pass, not runtime heuristics.
"""

from playwright.async_api import Page

from artifacts.models import Locator
from replay.step_executor import LocatorResolutionError, resolve_locator

DISMISS_LOCATOR = Locator(strategy="role", role="link", value="Dismiss & Continue")


async def try_dismiss_known_interstitial(page: Page) -> bool:
    """Returns True if a known dismissible interstitial was found and dismissed."""
    try:
        pw_locator = await resolve_locator(page, DISMISS_LOCATOR, {})
    except LocatorResolutionError:
        return False
    await pw_locator.first.click(timeout=3_000)
    return True
