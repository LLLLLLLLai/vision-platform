from fastapi import APIRouter

from app.api.routes import (
    algorithms,
    configuration,
    health,
    inspection,
    model_services,
    reference_candidates,
    world,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(
    algorithms.router,
    prefix="/algorithms",
    tags=["algorithms"],
)
api_router.include_router(
    reference_candidates.router,
    prefix="/reference-candidates",
    tags=["reference-candidates"],
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
api_router.include_router(
    world.router,
    prefix="/world",
    tags=["product-world-model"],
)
api_router.include_router(
    model_services.router,
    prefix="/model-services",
    tags=["model-services"],
)
