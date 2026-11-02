"""Client for the dedicated GPU-hosted YOLO inference service.

The model server deliberately receives a *relative* weights key instead of a
host filesystem path.  Both application and model servers mount the same model
storage hierarchy, so a compromised caller cannot make the model service read
arbitrary files from its host.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.vision_model_runtime import (
    PublishedVisionModelSpec,
    VisionModelRuntimeError,
)


class RemoteYoloInferenceError(VisionModelRuntimeError):
    """Raised when the dedicated YOLO service cannot return a valid result."""


def _api_url() -> str:
    base_url = str(settings.yolo_inference_base_url or "").strip().rstrip("/")
    if not base_url:
        raise RemoteYoloInferenceError(
            "YOLO_INFERENCE_MODE=REMOTE，但未配置 YOLO_INFERENCE_BASE_URL。"
        )
    return f"{base_url}/v1/yolo/infer"


def _model_key(spec: PublishedVisionModelSpec) -> str:
    root = Path(settings.vision_model_storage_root).expanduser().resolve()
    weights_path = spec.weights_path.resolve()
    try:
        return weights_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RemoteYoloInferenceError(
            "远程 YOLO 推理要求已发布权重位于 VISION_MODEL_STORAGE_ROOT 目录内。"
        ) from exc


def _response_error(response: httpx.Response) -> str:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        raw_detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(raw_detail, dict):
            raw_detail = raw_detail.get("message") or raw_detail.get("detail")
        detail = str(raw_detail or "").strip()
    if not detail:
        detail = response.text.strip()[:500]
    return f"远程 YOLO 服务返回 HTTP {response.status_code}" + (f"：{detail}" if detail else "")


async def run_remote_yolo_inference(
    spec: PublishedVisionModelSpec,
    image_path: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload one image to the GPU server and return the standard YOLO output."""

    source = Path(image_path)
    if not source.is_file():
        raise RemoteYoloInferenceError("待检测图片不存在或当前应用服务无法读取。")
    headers: dict[str, str] = {}
    if settings.yolo_inference_api_key:
        headers["X-Model-API-Key"] = settings.yolo_inference_api_key
    data = {
        "model_key": _model_key(spec),
        "task_type": spec.task_type,
        "labels_json": json.dumps(list(spec.labels), ensure_ascii=False),
        "config_json": json.dumps(dict(config or {}), ensure_ascii=False),
        "model_version_id": str(spec.version_id),
        "model_id": str(spec.model_id),
        "model_code": spec.model_code,
        "model_name": spec.model_name,
    }
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    try:
        with source.open("rb") as image_file:
            files = {"image": (source.name, image_file, content_type)}
            async with httpx.AsyncClient(
                timeout=settings.yolo_inference_timeout_seconds,
                verify=settings.yolo_inference_verify_tls,
            ) as client:
                response = await client.post(
                    _api_url(),
                    headers=headers,
                    data=data,
                    files=files,
                )
                response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RemoteYoloInferenceError(_response_error(exc.response)) from exc
    except httpx.HTTPError as exc:
        raise RemoteYoloInferenceError(f"远程 YOLO 服务不可用：{exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RemoteYoloInferenceError("远程 YOLO 服务返回的不是 JSON 结果。") from exc
    if not isinstance(payload, dict):
        raise RemoteYoloInferenceError("远程 YOLO 服务返回格式无效。")
    image = payload.get("image")
    if isinstance(image, dict):
        # Keep production records traceable to the application-side staged image
        # instead of leaking a temporary filename from the GPU container.
        image["path"] = str(source)
    payload["runtime"] = "REMOTE"
    payload["inference_endpoint"] = str(settings.yolo_inference_base_url).rstrip("/")
    return payload
