"""Same pattern as mock_app/templating.py: one shared Jinja2Templates
object, imported wherever a route needs to render a page.
"""

from datetime import datetime
from pathlib import Path

from starlette.templating import Jinja2Templates

import api.config as api_config

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True


def evidence_url(path: str | None) -> str | None:
    """Turns an absolute screenshot path from a log.json into a URL under
    the /evidence-files static mount (see api/main.py), or None if the
    path isn't actually under the evidence directory. Reads
    api.config.EVIDENCE_DIR through the module (not a direct value import)
    so tests can monkeypatch it, same as api/routers/runs.py does for
    replay.engine.EVIDENCE_ROOT."""
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(api_config.EVIDENCE_DIR.resolve())
    except ValueError:
        return None
    return f"/evidence-files/{rel.as_posix()}"


def human_time(value: str | None) -> str:
    """Formats a stored ISO-8601 timestamp (e.g. "2026-08-27T23:55:59.648855+00:00")
    for display. Falls back to the raw string if it isn't parseable, so a
    template never breaks over a formatting concern."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%b %d, %Y %I:%M:%S %p UTC")


templates.env.filters["evidence_url"] = evidence_url
templates.env.filters["human_time"] = human_time
