from pathlib import Path

from starlette.templating import Jinja2Templates

from mock_app.money import cents_to_dollars

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True
templates.env.filters["money"] = cents_to_dollars
