from fastapi import APIRouter

from app.api.routes import algorithms, configuration, health, inspection


api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(
    algorithms.router,
    prefix="/algorithms",
    tags=["algorithms"],
)
api_router.include_router(
    configuration.router,
    prefix="/configuration",
    tags=["configuration"],
)
api_router.include_router(
    inspection.router,
    prefix="/inspection",
    tags=["inspection"],
)
