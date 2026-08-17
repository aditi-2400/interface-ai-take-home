"""Injectable failure modes, triggered via `?simulate=` on any route.

These exist so the replay engine (Phase 4/5) has deterministic, on-demand
runtime failures to detect and classify, without relying on flaky real-world
conditions. Every simulated response is a normal 200 OK HTML page — legacy
back-office apps typically render a "soft" interstitial rather than a raw
HTTP error, so the signal a replay engine has to key off is the page content
(heading / text), the same signal a human operator would use.

`dialog` is designed to be genuinely recoverable: dismissing it re-issues the
original request (same path/method, same field values, minus `simulate`) so
the underlying flow actually continues rather than dead-ending.
"""

import time

from starlette.requests import Request
from starlette.responses import Response

from mock_app.templating import templates

SIMULATE_TIMEOUT_DELAY_SECONDS = 3


def check_simulate(request: Request, dismiss_fields: dict | None = None) -> Response | None:
    simulate = request.query_params.get("simulate")
    if not simulate:
        return None

    if simulate == "timeout":
        time.sleep(SIMULATE_TIMEOUT_DELAY_SECONDS)
        return templates.TemplateResponse(request, "interstitial_timeout.html", {})

    if simulate == "perm_denied":
        return templates.TemplateResponse(request, "interstitial_perm_denied.html", {})

    if simulate == "dialog":
        remaining = [(k, v) for k, v in request.query_params.multi_items() if k != "simulate"]
        continue_url = request.url.path
        if remaining:
            continue_url += "?" + "&".join(f"{k}={v}" for k, v in remaining)
        return templates.TemplateResponse(
            request,
            "interstitial_dialog.html",
            {
                "continue_url": continue_url,
                "continue_method": request.method,
                "dismiss_fields": dismiss_fields or {},
            },
        )

    return None
