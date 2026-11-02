from datetime import datetime
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.intelligence import AutomationJob, Dataset, VisionModel, VisionModelVersion
from app.services.dataset_split import assign_training_splits
from app.core.config import PROJECT_ROOT
from app.services.vision_model_runtime import (
    VisionModelRuntimeError,
    resolve_weights_path,
    task_output_profile,
    validate_yolo_weights_for_task,
)
from app.services.yolo_training import (
    YoloTrainingError,
    build_training_snapshot,
    validate_training_dataset,
)


router = APIRouter()

TRAINABLE_TASK_TYPES = {
    "YOLO_DETECTION",
    "YOLO_SEGMENTATION",
    "YOLO_CLASSIFICATION",
}

MAX_IMPORTED_WEIGHTS_BYTES = 8 * 1024 * 1024 * 1024


class VisionModelCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    task_type: str
    description: str | None = Field(default=None, max_length=1000)
    base_model: str | None = Field(default=None, max_length=300)
    labels_json: list[str] = Field(default_factory=list)


class VisionModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    task_type: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    base_model: str | None = Field(default=None, max_length=300)
    labels_json: list[str] | None = None
    enabled: bool | None = None


class VisionModelVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    dataset_id: int | None = None
    weights_path: str | None = Field(default=None, max_length=1000)
    metrics_json: dict[str, Any] = Field(default_factory=dict)
    artifact_paths_json: dict[str, Any] = Field(default_factory=dict)


class TrainingJobCreate(BaseModel):
    dataset_id: int
    version: str = Field(min_length=1, max_length=50)
    epochs: int = Field(default=100, ge=1, le=10000)
    image_size: int = Field(default=640, ge=64, le=4096)
    batch_size: int | None = Field(default=None, ge=1, le=1024)
    gpu_memory_required_mb: int = Field(default=4096, ge=512, le=1024 * 1024)
    extra_params_json: dict[str, Any] = Field(default_factory=dict)


def _safe_path_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return normalized.strip("._") or "model"


def _weights_root() -> Path:
    return PROJECT_ROOT / "vision-models"


def _relative_project_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _existing_import_path(raw_path: str) -> Path:
    """Resolve a server-side import path without allowing arbitrary file reads."""

    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    allowed_root = _weights_root().resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="服务器已有权重只能引用 vision-models/ 目录下的 .pt 文件。",
        ) from exc
    if resolved.suffix.lower() != ".pt":
        raise HTTPException(status_code=400, detail="当前仅支持导入 .pt 权重文件。")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="指定的服务器权重文件不存在。")
    return resolved


async def _save_imported_weights(
    *,
    model: VisionModel,
    version: str,
    uploaded_file: UploadFile,
) -> tuple[Path, str]:
    original_name = Path(uploaded_file.filename or "weights.pt").name
    if Path(original_name).suffix.lower() != ".pt":
        raise HTTPException(status_code=400, detail="当前仅支持上传 .pt 权重文件。")
    destination = (
        _weights_root()
        / "imported"
        / f"model-{model.id}-{_safe_path_component(model.code)}"
        / _safe_path_component(version)
        / "weights.pt"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    try:
        with destination.open("wb") as output:
            while chunk := await uploaded_file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_IMPORTED_WEIGHTS_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="权重文件超过 8 GB 导入上限，请将文件放入 vision-models/ 后用服务器路径引用。",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if total_bytes == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传的权重文件为空。")
    return destination, original_name


def _version_payload(version: VisionModelVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "dataset_id": version.dataset_id,
        "status": version.status,
        "weights_path": version.weights_path,
        "metrics_json": version.metrics_json,
        "artifact_paths_json": version.artifact_paths_json,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


def _model_payload(model: VisionModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "code": model.code,
        "name": model.name,
        "task_type": model.task_type,
        "output_profile": task_output_profile(model.task_type),
        "description": model.description,
        "base_model": model.base_model,
        "labels_json": model.labels_json,
        "enabled": model.enabled,
        "versions": [_version_payload(version) for version in sorted(model.versions, key=lambda item: item.id, reverse=True)],
        "created_at": model.created_at.isoformat(),
    }


@router.get("/published")
def list_published_vision_model_versions(
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = database.execute(
        select(VisionModel, VisionModelVersion)
        .join(VisionModelVersion, VisionModelVersion.vision_model_id == VisionModel.id)
        .where(
            VisionModel.is_deleted.is_(False),
            VisionModel.enabled.is_(True),
            VisionModelVersion.is_deleted.is_(False),
            VisionModelVersion.status == "PUBLISHED",
        )
        .order_by(VisionModel.name, VisionModelVersion.id.desc())
    ).all()
    result: list[dict[str, Any]] = []
    for model, version in rows:
        weights_path = resolve_weights_path(version.weights_path)
        result.append(
            {
                "id": version.id,
                "version_id": version.id,
                "version": version.version,
                "model_id": model.id,
                "model_code": model.code,
                "model_name": model.name,
                "task_type": model.task_type,
                "output_profile": task_output_profile(model.task_type),
                "labels_json": model.labels_json,
                "weights_path": version.weights_path,
                "available": bool(weights_path and weights_path.is_file()),
            }
        )
    return result


@router.get("")
def list_vision_models(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    models = database.scalars(
        select(VisionModel)
        .options(selectinload(VisionModel.versions))
        .where(VisionModel.is_deleted.is_(False))
        .order_by(VisionModel.id.desc())
    ).all()
    return [_model_payload(model) for model in models]


@router.post("")
def create_vision_model(
    payload: VisionModelCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    task_type = payload.task_type.upper()
    if task_type not in TRAINABLE_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail="当前仅支持 YOLO 目标检测、目标分割和分类模型。",
        )
    code = payload.code.strip()
    if database.scalar(select(VisionModel.id).where(VisionModel.code == code)):
        raise HTTPException(status_code=409, detail="模型编码已存在。")
    model = VisionModel(
        code=code,
        name=payload.name.strip(),
        task_type=task_type,
        description=payload.description,
        base_model=payload.base_model,
        labels_json=payload.labels_json,
    )
    database.add(model)
    database.commit()
    database.refresh(model)
    return _model_payload(model)


@router.put("/{model_id}")
def update_vision_model(
    model_id: int,
    payload: VisionModelUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.scalar(
        select(VisionModel)
        .options(selectinload(VisionModel.versions))
        .where(VisionModel.id == model_id)
    )
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")
    values = payload.model_dump(exclude_unset=True)
    task_type = values.get("task_type")
    if task_type is not None:
        task_type = task_type.upper()
        if task_type not in TRAINABLE_TASK_TYPES:
            raise HTTPException(status_code=400, detail="不支持的训练模型任务类型。")
        if task_type != model.task_type and model.versions:
            raise HTTPException(status_code=409, detail="已有训练版本的模型不能修改任务类型，请新建模型。")
        values["task_type"] = task_type
    for key, value in values.items():
        setattr(model, key, value.strip() if isinstance(value, str) else value)
    database.commit()
    database.refresh(model)
    return _model_payload(model)


@router.get("/{model_id}")
def get_vision_model(
    model_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.scalar(
        select(VisionModel)
        .options(selectinload(VisionModel.versions))
        .where(VisionModel.id == model_id)
    )
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")
    return _model_payload(model)


@router.delete("/{model_id}")
def delete_vision_model(
    model_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VisionModel, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")
    model.enabled = False
    model.is_deleted = True
    database.commit()
    return {"id": model_id, "deleted": True}


@router.post("/{model_id}/versions")
def create_model_version(
    model_id: int,
    payload: VisionModelVersionCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VisionModel, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")
    if payload.dataset_id and database.get(Dataset, payload.dataset_id) is None:
        raise HTTPException(status_code=400, detail="训练数据集不存在。")
    duplicate = database.scalar(
        select(VisionModelVersion.id).where(
            VisionModelVersion.vision_model_id == model.id,
            VisionModelVersion.version == payload.version,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该模型版本已存在。")
    version = VisionModelVersion(vision_model_id=model.id, **payload.model_dump())
    database.add(version)
    database.commit()
    database.refresh(version)
    return _version_payload(version)


@router.post("/{model_id}/versions/import")
async def import_model_version(
    model_id: int,
    version: str = Form(..., min_length=1, max_length=50),
    existing_weights_path: str | None = Form(default=None),
    weights_file: UploadFile | None = File(default=None),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Register a pre-trained local Ultralytics YOLO checkpoint as a draft version.

    Two mutually exclusive input modes are exposed deliberately: upload a local
    ``.pt`` file through the browser, or reference a file an administrator has
    already copied below ``vision-models/``.  Imported versions are never
    published automatically.
    """

    model = database.get(VisionModel, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")

    normalized_version = version.strip()
    if not normalized_version:
        raise HTTPException(status_code=400, detail="模型版本不能为空。")
    if database.scalar(
        select(VisionModelVersion.id).where(
            VisionModelVersion.vision_model_id == model.id,
            VisionModelVersion.version == normalized_version,
        )
    ):
        raise HTTPException(status_code=409, detail="该模型版本已存在，请更换版本号。")

    has_uploaded_file = bool(weights_file and weights_file.filename)
    normalized_existing_path = str(existing_weights_path or "").strip()
    has_existing_file = bool(normalized_existing_path)
    if has_uploaded_file == has_existing_file:
        raise HTTPException(
            status_code=400,
            detail="请上传一个 .pt 权重文件，或填写一个服务器已有权重路径，两者只能选择一种。",
        )

    uploaded_destination: Path | None = None
    import_source = "server_path"
    original_filename: str | None = None
    import_committed = False
    try:
        if has_uploaded_file:
            assert weights_file is not None
            uploaded_destination, original_filename = await _save_imported_weights(
                model=model,
                version=normalized_version,
                uploaded_file=weights_file,
            )
            weights_path = uploaded_destination
            import_source = "upload"
        else:
            weights_path = _existing_import_path(normalized_existing_path)
            original_filename = weights_path.name

        validation = validate_yolo_weights_for_task(weights_path, model.task_type)
        version_row = VisionModelVersion(
            vision_model_id=model.id,
            version=normalized_version,
            status="DRAFT",
            weights_path=_relative_project_path(weights_path),
            artifact_paths_json={
                "import_source": import_source,
                "original_filename": original_filename,
                "validation": validation,
            },
        )
        database.add(version_row)
        database.commit()
        import_committed = True
        database.refresh(version_row)
        return {
            **_version_payload(version_row),
            "message": "权重已导入并完成任务类型校验，当前为草稿版本；请确认后手动发布。",
        }
    except HTTPException:
        database.rollback()
        if uploaded_destination is not None and not import_committed:
            uploaded_destination.unlink(missing_ok=True)
        raise
    except VisionModelRuntimeError as exc:
        database.rollback()
        if uploaded_destination is not None and not import_committed:
            uploaded_destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"权重校验失败：{exc}") from exc
    except Exception as exc:
        database.rollback()
        if uploaded_destination is not None and not import_committed:
            uploaded_destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"导入模型版本失败：{exc}") from exc


@router.post("/{model_id}/versions/{version_id}/publish")
def publish_model_version(
    model_id: int,
    version_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VisionModel, model_id)
    version = database.get(VisionModelVersion, version_id)
    if model is None or model.is_deleted or not model.enabled:
        raise HTTPException(status_code=404, detail="视觉模型不存在或已停用。")
    if version is None or version.vision_model_id != model_id or version.is_deleted:
        raise HTTPException(status_code=404, detail="模型版本不存在。")
    if not version.weights_path:
        raise HTTPException(status_code=400, detail="模型版本尚未上传或产出权重文件。")
    weights_path = resolve_weights_path(version.weights_path)
    if weights_path is None or not weights_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="模型权重文件不存在，不能发布。请先重新训练或修正权重路径。",
        )
    database.query(VisionModelVersion).filter(
        VisionModelVersion.vision_model_id == model_id,
        VisionModelVersion.id != version.id,
        VisionModelVersion.status == "PUBLISHED",
    ).update({"status": "ARCHIVED"}, synchronize_session=False)
    version.status = "PUBLISHED"
    version.published_at = datetime.utcnow()
    database.commit()
    return _version_payload(version)


@router.post("/{model_id}/training-jobs")
def queue_training_job(
    model_id: int,
    payload: TrainingJobCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = database.get(VisionModel, model_id)
    dataset = database.scalar(
        select(Dataset)
        .options(selectinload(Dataset.items))
        .where(Dataset.id == payload.dataset_id)
    )
    if model is None or model.is_deleted:
        raise HTTPException(status_code=404, detail="视觉模型不存在。")
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="训练数据集不存在。")
    if dataset.purpose != "TRAIN":
        raise HTTPException(status_code=400, detail="模型训练只能使用 TRAIN 分类的数据集。")
    if dataset.media_type != "IMAGE":
        raise HTTPException(status_code=400, detail="当前训练流程只支持 IMAGE 图片数据集。")
    if model.task_type not in TRAINABLE_TASK_TYPES:
        raise HTTPException(status_code=400, detail="当前模型不是可执行的 YOLO 训练模型。")
    if database.scalar(
        select(VisionModelVersion.id).where(
            VisionModelVersion.vision_model_id == model.id,
            VisionModelVersion.version == payload.version,
            VisionModelVersion.is_deleted.is_(False),
        )
    ):
        raise HTTPException(status_code=409, detail="该模型版本已存在，请更换训练版本号。")
    try:
        validation_summary = validate_training_dataset(
            task_type=model.task_type,
            labels=model.labels_json or [],
            dataset_annotation_type=dataset.annotation_type,
            items=dataset.items,
        )
    except YoloTrainingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    split_summary = assign_training_splits(
        dataset.items,
        seed=f"{dataset.code}:revision:{dataset.revision}",
    )
    if split_summary["total"] == 0:
        raise HTTPException(status_code=400, detail="训练数据集没有可用素材。")
    training_samples = build_training_snapshot(dataset.items)
    job = AutomationJob(
        job_type="YOLO_TRAINING",
        vision_model_id=model.id,
        dataset_id=dataset.id,
        config_json=payload.model_dump(),
        input_snapshot_json={
            "dataset_revision": dataset.revision,
            "model_code": model.code,
            "model_task_type": model.task_type,
            "dataset_split": split_summary,
            "training_validation": validation_summary,
            "training_samples": training_samples,
        },
    )
    database.add(job)
    database.commit()
    return {
        "job_id": job.id,
        "status": job.status,
        "split_summary": split_summary,
        "message": "训练数据已通过标注校验，并按 70% / 20% / 10% 切分后进入 GPU 调度队列。",
    }
