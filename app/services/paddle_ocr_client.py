from pathlib import Path
from typing import Any, Literal

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


class OcrPreprocessing(BaseModel):
    model_config = ConfigDict(strict=True)
    use_orientation_classify: bool
    use_doc_unwarping: bool
    doc_angle_degrees: int | None = None
    coordinate_space: Literal["INPUT_IMAGE", "PREPROCESSED_IMAGE"]


class OcrResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str
    lines: list[OcrLine]
    preprocessing: OcrPreprocessing | None = None


async def recognize_text(
    image_path: str, *, min_score: float = 0.0,
    use_orientation_classify: bool = False, use_doc_unwarping: bool = False,
) -> dict[str, Any]:
    if not 0 <= min_score <= 1:
        raise OcrRequestError("OCR 最低文字置信度必须在 0 到 1 之间。")
    if type(use_orientation_classify) is not bool or type(use_doc_unwarping) is not bool:
        raise OcrRequestError("OCR 方向分类和去畸变开关必须是布尔值。")
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
                    data={"use_orientation_classify": str(use_orientation_classify).lower(), "use_doc_unwarping": str(use_doc_unwarping).lower()},
                    headers=headers,
                )
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            explanation = detail[:1000] if isinstance(detail, str) else "请检查 OCR 服务日志。"
            raise OcrRequestError(f"PaddleOCR 服务返回 HTTP {response.status_code}：{explanation}")
        payload = OcrResponse.model_validate(response.json())
        if payload.status != "COMPLETED":
            raise OcrRequestError("PaddleOCR 服务未完成识别。")
        if use_orientation_classify or use_doc_unwarping:
            if payload.preprocessing is None:
                raise OcrRequestError("OCR 服务未确认预处理开关，请更新并重启 OCR 服务。")
        if payload.preprocessing is not None and (
            payload.preprocessing.use_orientation_classify != use_orientation_classify
            or payload.preprocessing.use_doc_unwarping != use_doc_unwarping
        ):
            raise OcrRequestError("OCR 服务实际预处理配置与节点不一致，请检查 OCR 服务版本。")
    except httpx.TimeoutException as exc:
        raise OcrRequestError("PaddleOCR 识别超时，请检查 OCR 服务负载或调整 OCR_SERVICE_TIMEOUT_SECONDS。") from exc
    except httpx.RequestError as exc:
        raise OcrRequestError("无法连接 PaddleOCR 服务，请启动 start-ocr 脚本并检查 OCR_SERVICE_BASE_URL。") from exc
    except (ValidationError, ValueError) as exc:
        raise OcrRequestError("PaddleOCR 返回格式无效，需要 status 和 lines 识别结果。") from exc
    lines = [line.model_dump() for line in payload.lines if line.text.strip() and line.confidence >= min_score]
    texts = [line["text"] for line in lines]
    result = {"status": "COMPLETED", "text": "\n".join(texts), "texts": texts, "lines": lines, "text_count": len(lines)}
    if payload.preprocessing is not None:
        result["preprocessing"] = payload.preprocessing.model_dump()
    return result
