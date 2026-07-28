import asyncio
from typing import Any

from fastapi import APIRouter

from app.core.config import settings
from app.services.algorithm_client import AlgorithmServiceClient


router = APIRouter()
client = AlgorithmServiceClient()


@router.get("/status")
async def algorithm_status() -> dict[str, Any]:
    services = {
        "grounding_dino": settings.grounding_service_url,
        "dinov2": settings.dinov2_service_url,
        "paddleocr": settings.paddleocr_service_url,
    }

    async def inspect(name: str, url: str) -> tuple[str, dict[str, Any]]:
        try:
            result = await client.health(url)
            return name, {"status": "READY", "detail": result}
        except Exception as exc:
            return name, {"status": "UNAVAILABLE", "detail": str(exc)}

    checks = await asyncio.gather(
        *(inspect(name, url) for name, url in services.items())
    )
    return dict(checks)

