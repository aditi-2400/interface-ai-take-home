from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from mock_app import db
from mock_app.routers import accounts, members


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Meridian Trust Core Banking Console", lifespan=lifespan)
app.include_router(members.router)
app.include_router(accounts.router)


@app.get("/")
def root():
    return RedirectResponse(url="/members/search")
