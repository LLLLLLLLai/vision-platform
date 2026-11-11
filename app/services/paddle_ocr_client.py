from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import PROJECT_ROOT, settings


class OcrRequestError(RuntimeError):
    pass


class OcrLine(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    text: str
    confidence: float = Field(ge=0, le=1)
    polygon: list[list[float]] = Field(default_factory=list)


class OcrResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str
    lines: list[OcrLine]


async def recognize_text(image_path: str, *, min_score: float = 0.0) -> dict[str, Any]:
    if not 0 <= min_score <= 1:
        raise OcrRequestError("OCR 最低文字置信度必须在 0 到 1 之间。")
    source = Path(image_path)
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    if not source.is_file():
        raise OcrRequestError("OCR 输入图片不存在，请检查图片参数映射。")
    if source.stat().st_size > 20 * 1024 * 1024:
        raise OcrRequestError("OCR 图片不能超过 20 MB。")
    headers = {"X-OCR-API-Key": settings.ocr_service_api_key} if settings.ocr_service_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=settings.ocr_service_timeout_seconds, trust_env=False) as client:
            with source.open("rb") as image:
                response = await client.post(
                    f"{settings.ocr_service_base_url.rstrip('/')}/ocr",
                    files={"image": (source.name, image, "application/octet-stream")},
                    headers=headers,
                )
        if response.status_code != 200:
            raise OcrRequestError(f"PaddleOCR 服务返回 HTTP {response.status_code}，请检查 OCR 服务日志。")
        payload = OcrResponse.model_validate(response.json())
        if payload.status != "COMPLETED":
            raise OcrRequestError("PaddleOCR 服务未完成识别。")
    except httpx.TimeoutException as exc:
        raise OcrRequestError("PaddleOCR 识别超时，请检查 OCR 服务负载或调整 OCR_SERVICE_TIMEOUT_SECONDS。") from exc
    except httpx.RequestError as exc:
        raise OcrRequestError("无法连接 PaddleOCR 服务，请启动 start-ocr 脚本并检查 OCR_SERVICE_BASE_URL。") from exc
    except (ValidationError, ValueError) as exc:
        raise OcrRequestError("PaddleOCR 返回格式无效，需要 status 和 lines 识别结果。") from exc
    lines = [line.model_dump() for line in payload.lines if line.text.strip() and line.confidence >= min_score]
    texts = [line["text"] for line in lines]
    return {"status": "COMPLETED", "text": "\n".join(texts), "texts": texts, "lines": lines, "text_count": len(lines)}
