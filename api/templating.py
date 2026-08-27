"""Same pattern as mock_app/templating.py: one shared Jinja2Templates
object, imported wherever a route needs to render a page.
"""

from pathlib import Path

from starlette.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True
