import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.algorithm_client import AlgorithmServiceClient


router = APIRouter()
client = AlgorithmServiceClient()


class VlmJudgeRequest(BaseModel):
    image_path: str
    prompt: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(default_factory=dict)
    max_new_tokens: int = Field(default=160, ge=16, le=512)


@router.get("/status")
async def algorithm_status() -> dict[str, Any]:
    services = {
        "grounding_dino": settings.grounding_service_url,
        "dinov2": settings.dinov2_service_url,
        "qwen3_vl": settings.qwen_vl_service_url,
        "paddleocr": settings.paddleocr_service_url,
        "sam2": settings.sam2_service_url,
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


@router.post("/vlm/judge")
async def vlm_judge(payload: VlmJudgeRequest) -> dict[str, Any]:
    return await client.vlm_judge(
        image_path=payload.image_path,
        prompt=payload.prompt,
        expected=payload.expected,
        max_new_tokens=payload.max_new_tokens,
    )
