from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.api.routes.inspection import public_router as public_detection_router
from app.api.routes.scenarios import public_router as public_scene_router
from app.core.config import PROJECT_ROOT, settings
from app.db.init_db import init_database
from app.harness.runtime import get_harness
from app.services.automation_worker import automation_worker
from app.web.router import router as web_router


_VALID_APP_ROLES = {"ALL", "CONTROL", "INSPECTION"}


def _resolve_app_role(role: str | None = None) -> str:
    resolved = str(role or settings.app_role or "ALL").strip().upper()
    if resolved not in _VALID_APP_ROLES:
        choices = "、".join(sorted(_VALID_APP_ROLES))
        raise RuntimeError(f"APP_ROLE 仅支持 {choices}，当前值为 {resolved or '空'}。")
    return resolved


def create_app(role: str | None = None) -> FastAPI:
    """Create the management, inspection, or compatibility all-in-one app.

    ``ALL`` remains the local-development default.  Production uses CONTROL for
    the UI/management/worker process and INSPECTION for the public detect and
    published-scene endpoints.
    """

    app_role = _resolve_app_role(role)
    control_enabled = app_role in {"ALL", "CONTROL"}
    inspection_enabled = app_role in {"ALL", "INSPECTION"}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_database()
        get_harness()
        worker_started = False
        if control_enabled and settings.automation_worker_enabled:
            await automation_worker.start()
            worker_started = True
        try:
            yield
        finally:
            if worker_started:
                await automation_worker.stop()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.app_role = app_role
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

    if control_enabled:
        app.include_router(web_router)
        app.include_router(api_router, prefix="/api/v1")
    if inspection_enabled:
        # In ALL mode health is already included by api_router.  The standalone
        # production runtime still exposes the exact same readiness endpoint.
        if not control_enabled:
            app.include_router(health_router, prefix="/api/v1", tags=["system"])
        app.include_router(public_detection_router, prefix="/api", tags=["inspection"])
        app.include_router(
            public_scene_router,
            prefix="/api/v1/scenarios",
            tags=["inspection-scenarios-public"],
        )
    return app


app = create_app()
