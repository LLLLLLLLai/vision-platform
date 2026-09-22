from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.intelligence import VlmModelConfig
from app.services.openai_compatible_vlm import (
    VlmConfigurationError,
    api_key_hint,
    encrypt_api_key,
    vlm_client,
)


router = APIRouter()


class VlmModelCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    api_key_env_name: str | None = Field(default=None, max_length=200)
    thinking_enabled: bool = False
    extra_params_json: dict[str, Any] = Field(default_factory=dict)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)


class VlmModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    api_key_env_name: str | None = Field(default=None, max_length=200)
    clear_saved_api_key: bool = False
    thinking_enabled: bool | None = None
    extra_params_json: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=600.0)
    enabled: bool | None = None


def _payload(model: VlmModelConfig) -> dict[str, Any]:
    return {
        "id": model.id,
        "code": model.code,
        "name": model.name,
        "base_url": model.base_url,
        "model_name": model.model_name,
        "api_key_configured": bool(model.api_key_ciphertext or model.api_key_env_name),
        "api_key_env_name": model.api_key_env_name,
        "api_key_hint": model.api_key_hint,
        "thinking_enabled": model.thinking_enabled,
        "extra_params_json": model.extra_params_json,
        "temperature": model.temperature,
        "max_tokens": model.max_tokens,
        "timeout_seconds": model.timeout_seconds,
        "enabled": model.enabled,
        "created_at": model.created_at.isoformat(),
        "updated_at": model.updated_at.isoformat(),
    }


def _validate_secret_inputs(api_key: str | None, api_key_env_name: str | None) -> None:
    if api_key and api_key_env_name:
        raise HTTPException(
            status_code=400,
            detail="API Key 与 API Key 环境变量只能选择一种保存方式。",
        )


def _apply_api_key(model: VlmModelConfig, api_key: str | None) -> None:
    if not api_key:
        return
    try:
        model.api_key_ciphertext = encrypt_api_key(api_key.strip())
        model.api_key_hint = api_key_hint(api_key.strip())
    except VlmConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_vlm_models(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    models = database.scalars(
        select(VlmModelConfig)
        .where(VlmModelConfig.is_deleted.is_(False))
        .order_by(VlmModelConfig.id.desc())
    ).all()
    return [_payload(model) for model in models]


@router.post("")
def create_vlm_model(
    payload: VlmModelCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_secret_inputs(payload.api_key, payload.api_key_env_name)
    code = payload.code.strip()
    if database.scalar(select(VlmModelConfig.id).where(VlmModelConfig.code == code)):
        raise HTTPException(status_code=409, detail="VLM 模型编码已存在。")
    model = VlmModelConfig(
        code=code,
        name=payload.name.strip(),
        base_url=payload.base_url.strip().rstrip("/"),
        model_name=payload.model_name.strip(),
        api_key_env_name=payload.api_key_env_name.strip() if payload.api_key_env_name else None,
        thinking_enabled=payload.thinking_enabled,
        extra_params_json=payload.extra_params_json,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        timeout_seconds=payload.timeout_seconds,
    )
    _apply_api_key(model, payload.api_key)
    if model.api_key_env_name:
        model.api_key_hint = f"env:{model.api_key_env_name}"
    database.add(model)
    database.commit()
    database.refresh(model)
    return _payload(model)


@router.get("/{model_id}")
def get_vlm_model(
    model_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="VLM 模型不存在。")
    return _payload(model)


@router.put("/{model_id}")
def update_vlm_model(
    model_id: int,
    payload: VlmModelUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="VLM 模型不存在。")
    values = payload.model_dump(exclude_unset=True)
    _validate_secret_inputs(values.get("api_key"), values.get("api_key_env_name"))
    if values.pop("clear_saved_api_key", False):
        model.api_key_ciphertext = None
        model.api_key_hint = None
    api_key = values.pop("api_key", None)
    if "api_key_env_name" in values:
        env_name = values["api_key_env_name"]
        model.api_key_env_name = env_name.strip() if env_name else None
        if model.api_key_env_name:
            model.api_key_ciphertext = None
            model.api_key_hint = f"env:{model.api_key_env_name}"
    _apply_api_key(model, api_key)
    for key, value in values.items():
        if key == "api_key_env_name":
            continue
        if isinstance(value, str):
            value = value.strip()
            if key == "base_url":
                value = value.rstrip("/")
        setattr(model, key, value)
    database.commit()
    return _payload(model)


@router.post("/{model_id}/test")
async def test_vlm_model(
    model_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="VLM 模型不存在。")
    return await vlm_client.test_connection(model)


@router.delete("/{model_id}")
def delete_vlm_model(
    model_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="VLM 模型不存在。")
    model.is_deleted = True
    model.enabled = False
    database.commit()
    return {"id": model.id, "deleted": True}
