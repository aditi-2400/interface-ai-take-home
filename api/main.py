from fastapi import FastAPI

from api.routers import capabilities, chat, runs

app = FastAPI(title="Computer-Use Automation API")
app.include_router(capabilities.router)
app.include_router(runs.router)
app.include_router(chat.router)


@app.get("/")
def root() -> dict:
    return {
        "service": "computer-use-automation-api",
        "capabilities": "/capabilities",
        "runs": "/runs",
        "chat": "/chat",
    }
