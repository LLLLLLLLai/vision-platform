from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter()


class VlmJudgeRequest(BaseModel):
    image_path: str
    prompt: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(default_factory=dict)
    max_new_tokens: int = Field(default=160, ge=16, le=512)


@router.get("/status")
async def algorithm_status() -> dict[str, Any]:
    """Report that legacy local model services have been removed."""
    return {
        "status": "RETIRED",
        "message": (
            "本地 DINOv2、PaddleOCR、Qwen3-VL 和 SAM2 服务均已下线。"
            "请在场景中使用已配置的 OpenAI 兼容 VLM 或已发布 YOLO 模型。"
        ),
        "services": {},
    }


@router.post("/vlm/judge")
async def vlm_judge(payload: VlmJudgeRequest) -> dict[str, Any]:
    del payload
    raise HTTPException(
        status_code=410,
        detail="本地 Qwen3-VL 服务已下线，请使用已发布的 VLM 检测场景。",
    )
