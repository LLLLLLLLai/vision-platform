from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.intelligence import VisionModelVersion, VlmModelConfig, VlmModelVersion


class ModelSelectionError(ValueError):
    pass


VLM_CONFIG_FIELDS = (
    "base_url", "model_name", "thinking_enabled", "extra_params_json",
    "temperature", "max_tokens", "timeout_seconds",
)


def snapshot_vlm_config(database: Session, model: VlmModelConfig) -> VlmModelVersion:
    database.flush()
    latest = database.scalar(select(VlmModelVersion).where(
        VlmModelVersion.vlm_model_id == model.id,
    ).order_by(VlmModelVersion.version.desc()).limit(1))
    config = {field: deepcopy(getattr(model, field)) for field in VLM_CONFIG_FIELDS}
    if (latest and latest.config_json == config
            and latest.api_key_ciphertext == model.api_key_ciphertext
            and latest.api_key_env_name == model.api_key_env_name):
        return latest
    version = VlmModelVersion(
        vlm_model_id=model.id, version=latest.version + 1 if latest else 1,
        config_json=config, api_key_ciphertext=model.api_key_ciphertext,
        api_key_env_name=model.api_key_env_name,
    )
    database.add(version)
    database.flush()
    return version


def resolve_vision_version(database: Session, config: dict) -> VisionModelVersion:
    mode = config.get("model_version_mode", "FIXED" if config.get("model_version_id") else "LATEST")
    if mode not in {"LATEST", "FIXED"}:
        raise ModelSelectionError("训练模型版本策略必须是最新版本或指定版本。")
    try:
        if mode == "LATEST":
            model_id = int(config.get("vision_model_id"))
            query = select(VisionModelVersion).where(
                VisionModelVersion.vision_model_id == model_id,
                VisionModelVersion.status == "PUBLISHED",
                VisionModelVersion.is_deleted.is_(False),
            ).order_by(VisionModelVersion.published_at.desc(), VisionModelVersion.id.desc()).limit(1)
        else:
            query = select(VisionModelVersion).where(VisionModelVersion.id == int(config.get("model_version_id")))
        version = database.scalar(query.options(selectinload(VisionModelVersion.vision_model)))
    except (TypeError, ValueError) as exc:
        raise ModelSelectionError("请选择训练模型及其版本。") from exc
    if version is None or version.is_deleted or version.status not in {"PUBLISHED", "ARCHIVED"}:
        raise ModelSelectionError("训练模型没有可用的已发布版本，或指定版本不存在。")
    model = version.vision_model
    if model is None or model.is_deleted or not model.enabled:
        raise ModelSelectionError("训练模型不存在或已停用。")
    if config.get("vision_model_id") and str(config["vision_model_id"]) != str(model.id):
        raise ModelSelectionError("指定版本不属于所选训练模型。")
    return version


def resolve_vlm_config(database: Session, config: dict, fallback_id: int | None = None):
    try:
        model_id = int(config.get("vlm_model_id") or fallback_id)
    except (TypeError, ValueError) as exc:
        raise ModelSelectionError("请选择 VLM 模型。") from exc
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted or not model.enabled:
        raise ModelSelectionError("VLM 模型不存在或已停用。")
    mode = config.get("vlm_version_mode", "LATEST")
    if mode not in {"LATEST", "FIXED"}:
        raise ModelSelectionError("VLM 版本策略必须是最新版本或指定版本。")
    if mode == "LATEST":
        revision = database.scalar(select(VlmModelVersion).where(
            VlmModelVersion.vlm_model_id == model.id,
        ).order_by(VlmModelVersion.version.desc()).limit(1))
        values = {field: deepcopy(getattr(model, field)) for field in VLM_CONFIG_FIELDS}
    else:
        try:
            revision = database.get(VlmModelVersion, int(config.get("vlm_version_id")))
        except (TypeError, ValueError) as exc:
            raise ModelSelectionError("请选择指定的 VLM 配置版本。") from exc
        if revision is None or revision.is_deleted or revision.vlm_model_id != model.id:
            raise ModelSelectionError("VLM 配置版本不存在或不属于所选模型。")
        values = deepcopy(revision.config_json)
    credentials = revision if mode == "FIXED" else model
    return SimpleNamespace(
        id=model.id, code=model.code, name=model.name, enabled=True, is_deleted=False,
        api_key_ciphertext=credentials.api_key_ciphertext,
        api_key_env_name=credentials.api_key_env_name, api_key_hint=model.api_key_hint,
        config_version_id=revision.id if revision else None,
        config_version=revision.version if revision else None, **values,
    )
