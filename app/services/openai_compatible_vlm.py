"""OpenAI-compatible VLM adapter used by the new scene runtime.

The adapter intentionally keeps provider-specific options in ``extra_params_json``.
Only the OpenAI-compatible ``/v1/models`` and ``/v1/chat/completions`` contracts
are assumed by the platform.
"""

import base64
import asyncio
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.models.intelligence import VlmModelConfig
from app.services.inference_concurrency import vlm_inference_gate


class VlmConfigurationError(RuntimeError):
    """Raised when an endpoint configuration is incomplete or unsafe to use."""


class VlmRequestError(RuntimeError):
    """Raised when an OpenAI-compatible service cannot complete a request."""


def _fernet() -> Any:
    """Load Fernet lazily so read-only installations need no crypto dependency."""

    if not settings.vlm_secret_key:
        raise VlmConfigurationError(
            "未设置 VLM_SECRET_KEY，不能把 API Key 保存到数据库。"
            "请改用环境变量名称，或在 .env 中配置 Fernet 密钥。"
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - depends on deployment extras
        raise VlmConfigurationError(
            "缺少 cryptography 依赖，无法加密保存 API Key。"
        ) from exc
    try:
        return Fernet(settings.vlm_secret_key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise VlmConfigurationError("VLM_SECRET_KEY 不是有效的 Fernet 密钥。") from exc


def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # pragma: no cover - invalid key or corrupt database
        raise VlmConfigurationError("无法解密保存的 VLM API Key。") from exc


def api_key_hint(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def resolve_api_key(config: VlmModelConfig) -> str | None:
    if config.api_key_env_name:
        value = os.getenv(config.api_key_env_name)
        if not value:
            raise VlmConfigurationError(
                f"环境变量 {config.api_key_env_name} 未设置。"
            )
        return value
    if config.api_key_ciphertext:
        return decrypt_api_key(config.api_key_ciphertext)
    return None


def _api_root(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise VlmConfigurationError("VLM Base URL 不能为空。")
    for endpoint_suffix in (
        "/v1/chat/completions",
        "/v1/models",
        "/chat/completions",
        "/models",
    ):
        if normalized.endswith(endpoint_suffix):
            normalized = normalized[: -len(endpoint_suffix)].rstrip("/")
            break
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


_endpoint_counters: dict[int, int] = {}


def _api_roots(config: VlmModelConfig) -> list[str]:
    """Use an optional VLM endpoint pool without leaking it to provider payloads."""

    extra = config.extra_params_json or {}
    configured_pool = extra.get("endpoint_pool")
    values = configured_pool if isinstance(configured_pool, list) else []
    roots = [_api_root(str(value)) for value in values if str(value).strip()]
    return roots or [_api_root(config.base_url)]


def _next_api_root(config: VlmModelConfig) -> str:
    roots = _api_roots(config)
    current = _endpoint_counters.get(config.id, 0)
    _endpoint_counters[config.id] = current + 1
    return roots[current % len(roots)]


def _headers(config: VlmModelConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = resolve_api_key(config)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _as_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return {"result": "UNCERTAIN", "raw_text": text}
    return value if isinstance(value, dict) else {"result": "UNCERTAIN", "raw": value}


def _image_content(
    image_path: str,
    *,
    max_pixels: int | None = None,
) -> dict[str, Any]:
    source = Path(image_path)
    if not source.is_file():
        raise VlmConfigurationError(f"VLM 输入图片不存在：{image_path}")
    pixel_limit = max(28 * 28, int(max_pixels or settings.vlm_request_max_image_pixels))
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            source_pixels = image.width * image.height
            if source_pixels > pixel_limit:
                scale = math.sqrt(pixel_limit / source_pixels)
                resized_size = (
                    max(1, int(image.width * scale)),
                    max(1, int(image.height * scale)),
                )
                image = image.resize(resized_size, Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                buffer = BytesIO()
                image.save(
                    buffer,
                    format="JPEG",
                    quality=max(50, min(int(settings.vlm_request_jpeg_quality), 100)),
                    optimize=True,
                )
                image_bytes = buffer.getvalue()
                mime_type = "image/jpeg"
            else:
                image_bytes = source.read_bytes()
                mime_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
    except (UnidentifiedImageError, OSError) as exc:
        raise VlmConfigurationError(f"VLM 输入图片无法读取：{image_path}") from exc
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def _http_error_message(response: httpx.Response) -> str:
    """Return a concise provider error without exposing request credentials."""

    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("detail") or "").strip()
        elif error:
            detail = str(error).strip()
        if not detail:
            detail = str(payload.get("detail") or payload.get("message") or "").strip()
    if not detail:
        detail = response.text.strip()[:800]
    suffix = f"：{detail}" if detail else ""
    return f"VLM 服务返回 HTTP {response.status_code}{suffix}"


class OpenAiCompatibleVlmClient:
    async def test_connection(self, config: VlmModelConfig) -> dict[str, Any]:
        roots = _api_roots(config)

        async def check_endpoint(
            client: httpx.AsyncClient,
            root: str,
        ) -> dict[str, Any]:
            try:
                response = await client.get(f"{root}/models", headers=_headers(config))
                response.raise_for_status()
                payload = response.json()
                models = payload.get("data", []) if isinstance(payload, dict) else []
                return {
                    "base_url": root,
                    "ok": True,
                    "models": [
                        str(item.get("id"))
                        for item in models
                        if isinstance(item, dict)
                    ],
                }
            except (httpx.HTTPError, VlmConfigurationError) as exc:
                return {"base_url": root, "ok": False, "models": [], "message": str(exc)}

        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                endpoints = await asyncio.gather(
                    *(check_endpoint(client, root) for root in roots)
                )
        except VlmConfigurationError as exc:
            return {"ok": False, "message": str(exc), "models": [], "endpoints": []}

        healthy = [endpoint for endpoint in endpoints if endpoint["ok"]]
        model_ids = sorted(
            {
                model_id
                for endpoint in healthy
                for model_id in endpoint["models"]
            }
        )
        all_healthy = len(healthy) == len(endpoints)
        configured_model_found = config.model_name in model_ids
        connection_ok = all_healthy and configured_model_found
        if not all_healthy:
            message = f"{len(healthy)}/{len(endpoints)} 个 VLM 端点连接成功。"
        elif not configured_model_found:
            message = (
                "OpenAI 兼容接口可以连接，但未找到配置的模型 ID："
                f"{config.model_name}。请从返回的 models 列表中选择正确名称。"
            )
        else:
            message = "OpenAI 兼容接口连接成功，且已找到配置的模型 ID。"
        return {
            "ok": connection_ok,
            "service_ok": all_healthy,
            "message": message,
            "models": model_ids,
            "configured_model_found": configured_model_found,
            "endpoints": endpoints,
        }

    async def judge(
        self,
        config: VlmModelConfig,
        *,
        prompt: str,
        image_path: str | None = None,
        context: dict[str, Any] | None = None,
        system_prompt: str | None = None,
        request_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        extra = dict(config.extra_params_json or {})
        extra.pop("endpoint_pool", None)
        overrides = dict(request_overrides or {})
        # The model connection is managed centrally.  Scene-level overrides only
        # tune generation; they cannot replace the configured endpoint or model.
        temperature = overrides.pop("temperature", config.temperature)
        max_tokens = overrides.pop("max_tokens", config.max_tokens)
        thinking_enabled = overrides.pop("thinking_enabled", config.thinking_enabled)
        image_max_pixels = overrides.pop(
            "image_max_pixels",
            extra.pop("image_max_pixels", settings.vlm_request_max_image_pixels),
        )
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_path:
            user_content.append(_image_content(image_path, max_pixels=int(image_max_pixels)))
        payload: dict[str, Any] = {
            "model": config.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or (
                        "你是工业视觉复核助手。只根据提供的图片和上下文判断，"
                        "看不清或无法证实时返回 UNCERTAIN。输出 JSON 对象，"
                        "至少包含 result、confidence、reason。"
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            **extra,
            **overrides,
        }
        if thinking_enabled and "enable_thinking" not in payload:
            payload["enable_thinking"] = True
        if context:
            payload["messages"][1]["content"].insert(
                0,
                {
                    "type": "text",
                    "text": "上下文：" + json.dumps(context, ensure_ascii=False),
                },
            )
        url = f"{_next_api_root(config)}/chat/completions"
        try:
            async with vlm_inference_gate.acquire():
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    response = await client.post(url, headers=_headers(config), json=payload)
                    response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VlmRequestError(_http_error_message(exc.response)) from exc
        except (httpx.HTTPError, VlmConfigurationError) as exc:
            raise VlmRequestError(str(exc)) from exc
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VlmRequestError("OpenAI 兼容接口响应中缺少 choices[0].message.content。") from exc
        parsed = _parse_json_response(_as_content_text(content))
        parsed["_raw_response"] = data
        return parsed


vlm_client = OpenAiCompatibleVlmClient()
