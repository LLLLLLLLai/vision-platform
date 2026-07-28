from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.inspection import public_router as public_detection_router
from app.core.config import PROJECT_ROOT, settings
from app.db.init_db import init_database
from app.web.router import router as web_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount(
    "/files",
    StaticFiles(directory=PROJECT_ROOT / "uploads"),
    name="files",
)
app.mount(
    "/results",
    StaticFiles(directory=PROJECT_ROOT / "detection_results"),
    name="results",
)
app.include_router(web_router)
app.include_router(public_detection_router, prefix="/api", tags=["inspection"])
app.include_router(api_router, prefix="/api/v1")
