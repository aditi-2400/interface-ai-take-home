from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.config import EVIDENCE_DIR
from api.routers import capabilities, chat, dashboard, runs

app = FastAPI(title="Computer-Use Automation API")
app.include_router(capabilities.router)
app.include_router(runs.router)
app.include_router(chat.router)
app.include_router(dashboard.router)

# Serves screenshots the dashboard links to (see api/templating.py's
# evidence_url filter). Read-only, and it's the exact same evidence/
# directory already produced on disk - no new data, just a way to view it.
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/evidence-files", StaticFiles(directory=str(EVIDENCE_DIR)), name="evidence-files")


@app.get("/")
def root() -> dict:
    return {
        "service": "computer-use-automation-api",
        "capabilities": "/capabilities",
        "runs": "/runs",
        "chat": "/chat",
        "dashboard": "/dashboard",
    }
