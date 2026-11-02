from fastapi import APIRouter

from app.api.routes import (
    algorithms,
    automation,
    configuration,
    datasets,
    harness,
    health,
    inspection,
    model_services,
    scenarios,
    vision_models,
    vlm_models,
    world,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(harness.router, tags=["harness"])
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
api_router.include_router(
    scenarios.router,
    prefix="/scenarios",
    tags=["inspection-scenarios"],
)
api_router.include_router(
    datasets.router,
    prefix="/datasets",
    tags=["datasets"],
)
api_router.include_router(
    vlm_models.router,
    prefix="/vlm-models",
    tags=["vlm-models"],
)
api_router.include_router(
    vision_models.router,
    prefix="/vision-models",
    tags=["vision-models"],
)
api_router.include_router(
    automation.router,
    prefix="/automation-jobs",
    tags=["automation-jobs"],
)
