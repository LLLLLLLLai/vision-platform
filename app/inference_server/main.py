"""GPU-hosted, stateless YOLO inference API used by the application server."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from app.core.config import settings
from app.services.vision_model_runtime import (
    PublishedVisionModelSpec,
    VisionModelRuntimeError,
    run_yolo_inference,
)


app = FastAPI(title="Vision Platform YOLO Inference", version="1.0.0")


def _storage_root() -> Path:
    raw_root = settings.inference_model_storage_root or settings.vision_model_storage_root
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="模型存储目录不存在或尚未挂载。")
    return root


def _require_api_key(received: str | None) -> None:
    expected = str(settings.inference_api_key or "")
    if expected and received != expected:
        raise HTTPException(status_code=401, detail="模型服务认证失败。")


def _resolve_model_key(model_key: str) -> Path:
    normalized = str(model_key or "").strip().replace("\\", "/")
    candidate_key = PurePosixPath(normalized)
    if not normalized or candidate_key.is_absolute() or ".." in candidate_key.parts:
        raise HTTPException(status_code=422, detail="model_key 必须是模型目录内的相对路径。")
    root = _storage_root()
    candidate = (root / Path(*candidate_key.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="model_key 超出模型存储目录。") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="指定的模型权重不存在。")
    return candidate


def _parse_json(value: str, field_name: str, expected_type: type) -> Any:
    try:
        parsed = json.loads(value or ("[]" if expected_type is list else "{}"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} 不是有效 JSON。") from exc
    if not isinstance(parsed, expected_type):
        type_label = "数组" if expected_type is list else "对象"
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON {type_label}。")
    return parsed


@app.get("/health")
def health() -> dict[str, Any]:
    cuda_available = False
    cuda_name: str | None = None
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_name = str(torch.cuda.get_device_name(0))
    except Exception:
        pass
    root = Path(settings.inference_model_storage_root or settings.vision_model_storage_root).expanduser()
    return {
        "status": "READY" if root.is_dir() else "DEGRADED",
        "service": "yolo-inference",
        "cuda_available": cuda_available,
        "cuda_name": cuda_name,
        "model_storage_ready": root.is_dir(),
    }


@app.post("/v1/yolo/infer")
async def infer_yolo(
    image: UploadFile = File(...),
    model_key: str = Form(...),
    task_type: str = Form(...),
    labels_json: str = Form("[]"),
    config_json: str = Form("{}"),
    model_version_id: int = Form(0),
    model_id: int = Form(0),
    model_code: str = Form("REMOTE_YOLO"),
    model_name: str = Form("远程 YOLO 模型"),
    x_model_api_key: str | None = Header(default=None, alias="X-Model-API-Key"),
) -> dict[str, Any]:
    _require_api_key(x_model_api_key)
    suffix = Path(image.filename or "input.jpg").suffix or ".jpg"
    payload = await image.read()
    maximum = max(1, int(settings.inference_max_upload_mb)) * 1024 * 1024
    if not payload:
        raise HTTPException(status_code=422, detail="上传图片为空。")
    if len(payload) > maximum:
        raise HTTPException(status_code=413, detail="上传图片超过模型服务大小限制。")
    labels = _parse_json(labels_json, "labels_json", list)
    config = _parse_json(config_json, "config_json", dict)
    weights_path = _resolve_model_key(model_key)
    staged_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="vision-yolo-", suffix=suffix, delete=False) as temporary:
            temporary.write(payload)
            staged_path = Path(temporary.name)
        spec = PublishedVisionModelSpec(
            version_id=int(model_version_id),
            model_id=int(model_id),
            model_code=str(model_code),
            model_name=str(model_name),
            task_type=str(task_type).upper(),
            labels=tuple(str(item).strip() for item in labels if str(item).strip()),
            weights_path=weights_path,
        )
        result = await asyncio.to_thread(
            run_yolo_inference,
            spec,
            str(staged_path),
            config=config,
        )
        result["runtime"] = "MODEL_SERVER"
        result["uploaded_filename"] = image.filename
        result["content_type"] = image.content_type or mimetypes.guess_type(image.filename or "")[0]
        return result
    except VisionModelRuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if staged_path is not None:
            try:
                os.unlink(staged_path)
            except OSError:
                pass
