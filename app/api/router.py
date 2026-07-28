from fastapi import APIRouter

from app.api.routes import algorithms, health


api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(
    algorithms.router,
    prefix="/algorithms",
    tags=["algorithms"],
)

