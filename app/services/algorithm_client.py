import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import PROJECT_ROOT, settings


MODEL_CALL_LOG_DIR = PROJECT_ROOT / "logs" / "model_services"
MODEL_CALL_LOG_LOCK = threading.Lock()
SERVICE_CODES = {
    settings.grounding_service_url.rstrip("/"): "grounding_dino",
    settings.dinov2_service_url.rstrip("/"): "dinov2",
    settings.qwen_vl_service_url.rstrip("/"): "qwen3_vl",
    settings.paddleocr_service_url.rstrip("/"): "paddleocr",
    settings.sam2_service_url.rstrip("/"): "sam2",
}


def _compact_value(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return str(value)[:240]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, depth + 1)
            for key, item in value.items()
            if key not in {"embedding", "pixel_values"}
        }
    if isinstance(value, list):
        if len(value) > 12:
            return {"item_count": len(value), "preview": [_compact_value(item, depth + 1) for item in value[:3]]}
        return [_compact_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 600 else f"{value[:600]}…"
    return value


def _write_call_log(
    *,
    base_url: str,
    path: str,
    payload: dict[str, Any],
    status: str,
    elapsed_ms: float,
    http_status: int | None = None,
    response: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    service_code = SERVICE_CODES.get(base_url.rstrip("/"), "unknown")
    MODEL_CALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "status": status,
        "service": service_code,
        "endpoint": path,
        "elapsed_ms": round(elapsed_ms, 2),
        "http_status": http_status,
        "request": _compact_value(payload),
        "response": _compact_value(response or {}),
        "error": error,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    log_path = MODEL_CALL_LOG_DIR / f"{service_code}.calls.log"
    with MODEL_CALL_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{line}\n")


class AlgorithmServiceClient:
    def __init__(self) -> None:
        self.timeout = settings.algorithm_timeout_seconds

    async def health(self, service_url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{service_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()

    async def localize(
        self,
        image_path: str,
        prompts: list[str],
        threshold: float = 0.25,
        text_threshold: float = 0.22,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "prompts": prompts,
            "threshold": threshold,
            "text_threshold": text_threshold,
        }
        return await self._post(
            settings.grounding_service_url,
            "/v1/localize",
            payload,
            timeout=max(self.timeout, 60.0),
        )

    async def embedding(self, image_path: str) -> dict[str, Any]:
        return await self._post(
            settings.dinov2_service_url,
            "/v1/embedding",
            {"image_path": image_path},
        )

    async def similarity(
        self,
        image_path: str,
        reference_paths: list[str],
        top_k: int = 3,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "reference_paths": reference_paths,
            "top_k": top_k,
        }
        return await self._post(
            settings.dinov2_service_url,
            "/v1/similarity",
            payload,
        )

    async def vlm_judge(
        self,
        image_path: str,
        prompt: str,
        expected: dict[str, Any] | None = None,
        max_new_tokens: int = 160,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "prompt": prompt,
            "expected": expected or {},
            "max_new_tokens": max_new_tokens,
        }
        return await self._post(
            settings.qwen_vl_service_url,
            "/v1/judge",
            payload,
            timeout=max(self.timeout, 60.0),
        )

    async def vlm_compare(
        self,
        baseline_image_path: str,
        candidate_image_path: str,
        prompt: str,
        expected: dict[str, Any] | None = None,
        max_new_tokens: int = 220,
    ) -> dict[str, Any]:
        payload = {
            "baseline_image_path": baseline_image_path,
            "candidate_image_path": candidate_image_path,
            "prompt": prompt,
            "expected": expected or {},
            "max_new_tokens": max_new_tokens,
        }
        return await self._post(
            settings.qwen_vl_service_url,
            "/v1/compare",
            payload,
            timeout=max(self.timeout, 90.0),
        )

    async def discover_objects(
        self,
        image_path: str,
        object_types: list[str] | None = None,
        max_objects: int = 30,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "object_types": object_types or [],
            "max_objects": max_objects,
            "max_new_tokens": 1024,
        }
        return await self._post(
            settings.qwen_vl_service_url,
            "/v1/discover",
            payload,
            timeout=max(self.timeout, 120.0),
        )

    async def inventory_objects(
        self,
        image_path: str,
        object_types: list[str] | None = None,
        max_types: int = 12,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "object_types": object_types or [],
            "max_types": max_types,
            "max_new_tokens": 320,
        }
        return await self._post(
            settings.qwen_vl_service_url,
            "/v1/inventory",
            payload,
            timeout=max(self.timeout, 90.0),
        )

    async def segment_harness(
        self,
        image_path: str,
        seeds: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._post(
            settings.sam2_service_url,
            "/v1/segment",
            {"image_path": image_path, "seeds": seeds},
            timeout=max(self.timeout, 180.0),
        )

    async def ocr(
        self,
        image_path: str,
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        return await self._post(
            settings.paddleocr_service_url,
            "/ocr",
            {"image_path": image_path, "expected_text": expected_text},
        )

    async def _post(
        self,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}{path}",
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
            _write_call_log(
                base_url=base_url,
                path=path,
                payload=payload,
                status="SUCCESS",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                http_status=response.status_code,
                response=result,
            )
            return result
        except Exception as exc:
            _write_call_log(
                base_url=base_url,
                path=path,
                payload=payload,
                status="ERROR",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                http_status=response.status_code if response is not None else None,
                error=str(exc),
            )
            raise
