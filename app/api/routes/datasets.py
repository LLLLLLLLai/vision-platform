import hashlib
import math
import re
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models.intelligence import (
    Dataset,
    DatasetItem,
    InspectionScenarioVersion,
    VisionModel,
    VisionModelVersion,
)
from app.models.recipe import Recipe
from app.services.vision_model_runtime import (
    VisionModelRuntimeError,
    build_published_model_spec,
    run_yolo_inference,
)


router = APIRouter()

PURPOSES = {"TEST", "TRAIN"}
MEDIA_TYPES = {"IMAGE", "VIDEO"}
ANNOTATION_TYPES = {"NONE", "CLASSIFICATION", "DETECTION", "SEGMENTATION"}
GROUND_TRUTHS = {"OK", "NG"}
COLLECTION_SCOPES = {"ROI", "ORIGINAL"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
OFFLINE_YOLO_MAX_IMAGES = 2000
OFFLINE_YOLO_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
OFFLINE_YOLO_MAX_LABEL_BYTES = 5 * 1024 * 1024


class DatasetCreate(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    purpose: str = "TEST"
    media_type: str = "IMAGE"
    annotation_type: str = "NONE"
    labels: list[str] = Field(default_factory=list)
    collection_scenario_version_id: int | None = None
    collection_scope: str = "ROI"
    collection_recipe_id: int | None = None
    auto_collect_enabled: bool = False
    auto_collect_limit: int = Field(default=1000, ge=1, le=100000)


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    labels: list[str] | None = None
    collection_scenario_version_id: int | None = None
    collection_scope: str | None = None
    collection_recipe_id: int | None = None
    auto_collect_enabled: bool | None = None
    auto_collect_limit: int | None = Field(default=None, ge=1, le=100000)


class DatasetItemUpdate(BaseModel):
    ground_truth: str | None = None
    annotation_status: str | None = Field(default=None, max_length=30)
    annotation_json: dict[str, Any] | None = None
    split: str | None = Field(default=None, max_length=30)


class DatasetItemPreAnnotationRequest(BaseModel):
    """Runtime options for a non-persistent AI pre-annotation request."""

    vision_model_version_id: int = Field(gt=0)
    confidence: float = Field(default=0.25, ge=0.0, le=1.0)
    iou: float = Field(default=0.45, ge=0.0, le=1.0)
    max_detections: int = Field(default=300, ge=1, le=3000)


def _normalize(value: str) -> str:
    return value.strip().upper()


def _normalize_labels(values: list[str] | None) -> list[str]:
    """Keep a stable, operator-owned label list for one dataset."""

    labels: list[str] = []
    seen: set[str] = set()
    for raw_value in values or []:
        label = str(raw_value or "").strip()
        key = label.casefold()
        if not label or key in seen:
            continue
        if len(label) > 100:
            raise HTTPException(status_code=400, detail="类别名称不能超过 100 个字符。")
        labels.append(label)
        seen.add(key)
    return labels


def _merge_dataset_labels(dataset: Dataset, labels: list[str]) -> None:
    dataset.label_schema_json = _normalize_labels(
        [*(dataset.label_schema_json or []), *labels]
    )


def _validate_collection_configuration(
    database: Session,
    *,
    media_type: str,
    collection_scope: str,
    scenario_version_id: int | None,
    recipe_id: int | None,
    enabled: bool,
) -> None:
    if collection_scope not in COLLECTION_SCOPES:
        raise HTTPException(status_code=400, detail="自动采集范围仅支持 ROI 或 ORIGINAL。")
    if not enabled:
        return
    if media_type != "IMAGE":
        raise HTTPException(status_code=400, detail="自动采集仅支持图片数据集。")
    if collection_scope == "ROI":
        if scenario_version_id is None:
            raise HTTPException(status_code=400, detail="采集 ROI 时必须关联一个已发布场景。")
        scenario_version = database.get(InspectionScenarioVersion, scenario_version_id)
        if scenario_version is None or scenario_version.status != "PUBLISHED":
            raise HTTPException(status_code=400, detail="ROI 自动采集只能关联已发布的检测场景。")
        return
    if recipe_id is None:
        raise HTTPException(status_code=400, detail="采集相机原图时必须关联一个已发布工艺配方。")
    recipe = database.get(Recipe, recipe_id)
    if recipe is None or recipe.is_deleted or recipe.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="原图自动采集只能关联已发布工艺配方。")


def _new_dataset_code(purpose: str, media_type: str) -> str:
    """Generate a readable unique code so ordinary users only enter a name."""

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"DATASET_{purpose}_{media_type}_{timestamp}_{suffix}"


def _annotation_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"标注字段 {field} 必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"标注字段 {field} 必须是数字。") from exc
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"标注字段 {field} 必须是有效数字。")
    return number


def _annotation_label(value: Any, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail=f"标注字段 {field} 不能为空。")
    return label


def _validate_training_annotation(dataset: Dataset, annotation: dict[str, Any]) -> None:
    """Validate the user-facing annotation payload before it enters a training set."""

    annotation_type = _normalize(dataset.annotation_type)
    if annotation_type == "DETECTION":
        boxes = annotation.get("boxes")
        if not isinstance(boxes, list):
            raise HTTPException(status_code=400, detail="目标检测标注必须包含 boxes 数组。")
        for index, box in enumerate(boxes, start=1):
            if not isinstance(box, dict):
                raise HTTPException(status_code=400, detail=f"第 {index} 个检测框格式不正确。")
            _annotation_label(box.get("label"))
            x = _annotation_number(box.get("x"), "x")
            y = _annotation_number(box.get("y"), "y")
            width = _annotation_number(box.get("width"), "width")
            height = _annotation_number(box.get("height"), "height")
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail=f"第 {index} 个检测框的坐标或尺寸无效。")
            normalized_values = (x, y, width, height)
            if max(normalized_values) <= 1 and (x + width > 1.000001 or y + height > 1.000001):
                raise HTTPException(status_code=400, detail=f"第 {index} 个检测框超出图片边界。")
        return

    if annotation_type == "CLASSIFICATION":
        _annotation_label(annotation.get("label"))
        return

    if annotation_type == "SEGMENTATION":
        segments = annotation.get("segments", annotation.get("polygons"))
        if not isinstance(segments, list):
            raise HTTPException(status_code=400, detail="目标分割标注必须包含 segments 数组。")
        for index, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                raise HTTPException(status_code=400, detail=f"第 {index} 个分割区域格式不正确。")
            _annotation_label(segment.get("label"))
            points = segment.get("points")
            if not isinstance(points, list) or len(points) < 3:
                raise HTTPException(status_code=400, detail=f"第 {index} 个分割区域至少需要三个点。")
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise HTTPException(status_code=400, detail=f"第 {index} 个分割区域的点位格式不正确。")
                x = _annotation_number(point[0], "point.x")
                y = _annotation_number(point[1], "point.y")
                if x < 0 or y < 0:
                    raise HTTPException(status_code=400, detail=f"第 {index} 个分割区域的点位不能为负数。")
        return

    raise HTTPException(status_code=400, detail="训练数据集必须选择目标检测、目标分割或分类标注。")


def _annotation_labels(dataset: Dataset, annotation: dict[str, Any]) -> list[str]:
    annotation_type = _normalize(dataset.annotation_type)
    if annotation_type == "DETECTION":
        return [str(item.get("label") or "").strip() for item in annotation.get("boxes", [])]
    if annotation_type == "CLASSIFICATION":
        return [str(annotation.get("label") or "").strip()]
    if annotation_type == "SEGMENTATION":
        segments = annotation.get("segments", annotation.get("polygons", []))
        return [str(item.get("label") or "").strip() for item in segments]
    return []


ANNOTATION_MODEL_TASKS = {
    "DETECTION": "YOLO_DETECTION",
    "SEGMENTATION": "YOLO_SEGMENTATION",
    "CLASSIFICATION": "YOLO_CLASSIFICATION",
}


def _clamp_normalized(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pre_annotation_from_inference(
    dataset: Dataset,
    inference: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Turn the common YOLO output contract into editable dataset annotations.

    This deliberately returns only suggestions. The browser presents these as
    editable candidates and only a later explicit save turns them into labeled
    training data.
    """

    annotation_type = _normalize(dataset.annotation_type)
    warnings: list[str] = []
    if annotation_type == "CLASSIFICATION":
        classification = inference.get("classification") or {}
        label = str(classification.get("top1_label") or "").strip()
        if not label:
            raise HTTPException(status_code=422, detail="模型未返回可用的分类类别。")
        return {
            "label": label,
            "_meta": {"source": "AI_PREANNOTATION"},
        }, warnings

    if annotation_type == "DETECTION":
        image = inference.get("image") or {}
        image_width = float(image.get("width") or 0)
        image_height = float(image.get("height") or 0)
        if image_width <= 0 or image_height <= 0:
            raise HTTPException(status_code=422, detail="模型未返回图片尺寸，无法转换检测框。")
        boxes: list[dict[str, Any]] = []
        for object_result in inference.get("objects") or []:
            bbox = object_result.get("bbox") if isinstance(object_result, dict) else None
            if not isinstance(bbox, list) or len(bbox) < 4:
                continue
            try:
                left, top, right, bottom = (float(value) for value in bbox[:4])
            except (TypeError, ValueError):
                continue
            left = _clamp_normalized(left / image_width)
            top = _clamp_normalized(top / image_height)
            right = _clamp_normalized(right / image_width)
            bottom = _clamp_normalized(bottom / image_height)
            width = right - left
            height = bottom - top
            label = str(object_result.get("label") or "").strip()
            if not label or width <= 0 or height <= 0:
                continue
            boxes.append(
                {
                    "label": label,
                    "x": round(left, 6),
                    "y": round(top, 6),
                    "width": round(width, 6),
                    "height": round(height, 6),
                    "confidence": object_result.get("confidence"),
                    "auto_generated": True,
                }
            )
        return {
            "boxes": boxes,
            "_meta": {"source": "AI_PREANNOTATION"},
        }, warnings

    if annotation_type == "SEGMENTATION":
        segments: list[dict[str, Any]] = []
        missing_masks = 0
        for object_result in inference.get("objects") or []:
            if not isinstance(object_result, dict):
                continue
            label = str(object_result.get("label") or "").strip()
            raw_points = object_result.get("mask")
            if not label or not isinstance(raw_points, list):
                missing_masks += 1
                continue
            points: list[list[float]] = []
            for point in raw_points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    x, y = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue
                points.append([round(_clamp_normalized(x), 6), round(_clamp_normalized(y), 6)])
            if len(points) >= 3:
                segments.append(
                    {
                        "label": label,
                        "points": points,
                        "confidence": object_result.get("confidence"),
                        "auto_generated": True,
                    }
                )
            else:
                missing_masks += 1
        if missing_masks:
            warnings.append("部分模型结果没有可用轮廓，未生成对应分割建议。")
        return {
            "segments": segments,
            "_meta": {"source": "AI_PREANNOTATION"},
        }, warnings

    raise HTTPException(status_code=400, detail="当前数据集未启用可自动标注的训练类型。")


def _dataset_payload(dataset: Dataset, *, include_items: bool = False) -> dict[str, Any]:
    active_items = [item for item in dataset.items if not item.is_deleted]
    split_counts = {"TRAIN": 0, "VAL": 0, "TEST": 0, "UNASSIGNED": 0}
    for item in active_items:
        split = (item.split or "UNASSIGNED").upper()
        split_counts[split if split in split_counts else "UNASSIGNED"] += 1
    payload: dict[str, Any] = {
        "id": dataset.id,
        "code": dataset.code,
        "name": dataset.name,
        "description": dataset.description,
        "purpose": dataset.purpose,
        "media_type": dataset.media_type,
        "annotation_type": dataset.annotation_type,
        "labels": list(dataset.label_schema_json or []),
        "collection_scenario_version_id": dataset.collection_scenario_version_id,
        "collection_scope": dataset.collection_scope or "ROI",
        "collection_recipe_id": dataset.collection_recipe_id,
        "auto_collect_enabled": dataset.auto_collect_enabled,
        "auto_collect_limit": dataset.auto_collect_limit,
        "revision": dataset.revision,
        "enabled": dataset.enabled,
        "item_count": len(active_items) if include_items else None,
        "split_counts": split_counts,
        "created_at": dataset.created_at.isoformat(),
        "updated_at": dataset.updated_at.isoformat(),
    }
    if include_items:
        payload["items"] = [_item_payload(item) for item in active_items]
    return payload


def _item_payload(item: DatasetItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "media_path": item.media_path,
        "file_url": "/files/" + item.media_path.replace("\\", "/").split("uploads/", 1)[-1]
        if "uploads/" in item.media_path.replace("\\", "/")
        else None,
        "original_name": item.original_name,
        "media_type": item.media_type,
        "ground_truth": item.ground_truth,
        "annotation_status": item.annotation_status,
        "annotation_json": item.annotation_json,
        "split": item.split,
        "source": item.source,
        "created_at": item.created_at.isoformat(),
    }


@router.get("")
def list_datasets(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    datasets = database.scalars(
        select(Dataset)
        .options(selectinload(Dataset.items))
        .where(Dataset.is_deleted.is_(False))
        .order_by(Dataset.id.desc())
    ).all()
    return [_dataset_payload(item, include_items=True) for item in datasets]


@router.post("")
def create_dataset(
    payload: DatasetCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    purpose = _normalize(payload.purpose)
    media_type = _normalize(payload.media_type)
    annotation_type = _normalize(payload.annotation_type)
    if purpose not in PURPOSES:
        raise HTTPException(status_code=400, detail="数据集分类仅支持 TEST 或 TRAIN。")
    if media_type not in MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="数据类型仅支持 IMAGE 或 VIDEO。")
    if annotation_type not in ANNOTATION_TYPES:
        raise HTTPException(status_code=400, detail="不支持的标注类型。")
    if purpose == "TEST" and annotation_type != "NONE":
        raise HTTPException(status_code=400, detail="评测数据集只需维护图片 OK/NG，不需要训练标注。")
    if purpose == "TRAIN" and annotation_type == "NONE":
        raise HTTPException(status_code=400, detail="YOLO 训练数据集需要选择目标检测、目标分割或分类标注。")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="数据集名称不能为空。")
    code = (payload.code or "").strip() or _new_dataset_code(purpose, media_type)
    if database.scalar(select(Dataset.id).where(Dataset.code == code)):
        raise HTTPException(status_code=409, detail="数据集编码已存在。")
    labels = _normalize_labels(payload.labels)
    collection_scope = _normalize(payload.collection_scope)
    _validate_collection_configuration(
        database,
        media_type=media_type,
        collection_scope=collection_scope,
        scenario_version_id=payload.collection_scenario_version_id,
        recipe_id=payload.collection_recipe_id,
        enabled=payload.auto_collect_enabled,
    )
    dataset = Dataset(
        code=code,
        name=name,
        description=payload.description,
        purpose=purpose,
        media_type=media_type,
        annotation_type=annotation_type,
        label_schema_json=labels,
        collection_scenario_version_id=(
            payload.collection_scenario_version_id
            if collection_scope == "ROI"
            else None
        ),
        collection_scope=collection_scope,
        collection_recipe_id=(
            payload.collection_recipe_id
            if collection_scope == "ORIGINAL"
            else None
        ),
        auto_collect_enabled=payload.auto_collect_enabled,
        auto_collect_limit=payload.auto_collect_limit,
    )
    database.add(dataset)
    database.commit()
    database.refresh(dataset)
    return _dataset_payload(dataset)


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.scalar(
        select(Dataset)
        .options(selectinload(Dataset.items))
        .where(Dataset.id == dataset_id)
    )
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    return _dataset_payload(dataset, include_items=True)


@router.put("/{dataset_id}")
def update_dataset(
    dataset_id: int,
    payload: DatasetUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.get(Dataset, dataset_id)
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    values = payload.model_dump(exclude_unset=True)
    next_media_type = _normalize(dataset.media_type)
    next_collection_id = values.get(
        "collection_scenario_version_id", dataset.collection_scenario_version_id
    )
    next_collection_scope = _normalize(
        str(values.get("collection_scope", dataset.collection_scope or "ROI"))
    )
    next_recipe_id = values.get("collection_recipe_id", dataset.collection_recipe_id)
    next_auto_collect = values.get(
        "auto_collect_enabled", dataset.auto_collect_enabled
    )
    _validate_collection_configuration(
        database,
        media_type=next_media_type,
        collection_scope=next_collection_scope,
        scenario_version_id=next_collection_id,
        recipe_id=next_recipe_id,
        enabled=bool(next_auto_collect),
    )
    values["collection_scope"] = next_collection_scope
    if next_collection_scope == "ROI":
        values["collection_recipe_id"] = None
    else:
        values["collection_scenario_version_id"] = None
    if "labels" in values:
        values["label_schema_json"] = _normalize_labels(values.pop("labels"))
    for key, value in values.items():
        setattr(dataset, key, value.strip() if isinstance(value, str) else value)
    database.commit()
    return _dataset_payload(dataset)


def _normalized_ground_truth(dataset: Dataset, ground_truth: str | None) -> str | None:
    if ground_truth:
        if dataset.purpose == "TRAIN":
            raise HTTPException(status_code=400, detail="YOLO 训练数据集请在标注器中维护类别和检测区域。")
        normalized_truth = _normalize(ground_truth)
        if normalized_truth not in GROUND_TRUTHS:
            raise HTTPException(status_code=400, detail="测试真值仅支持 OK 或 NG。")
    else:
        return None
    return normalized_truth


def _store_dataset_item(
    database: Session,
    dataset: Dataset,
    file: UploadFile,
    *,
    ground_truth: str | None,
    split: str | None,
) -> DatasetItem | None:
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if not suffix:
        suffix = ".bin"
    target_dir = Path(PROJECT_ROOT / "uploads" / "datasets" / dataset.code)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    duplicate = database.scalar(
        select(DatasetItem.id).where(
            DatasetItem.dataset_id == dataset.id,
            DatasetItem.content_hash == content_hash,
            DatasetItem.is_deleted.is_(False),
        )
    )
    if duplicate:
        target.unlink(missing_ok=True)
        return None
    item = DatasetItem(
        dataset_id=dataset.id,
        media_path=str(target),
        original_name=file.filename or target.name,
        media_type=dataset.media_type,
        ground_truth=ground_truth,
        annotation_status="LABELED" if ground_truth else "PENDING",
        split=split.strip().upper() if split else None,
        content_hash=content_hash,
    )
    database.add(item)
    return item


def _archive_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    raw_path = (info.filename or "").replace("\\", "/").strip("/")
    path = PurePosixPath(raw_path)
    if (
        not raw_path
        or path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise HTTPException(status_code=400, detail="离线标注压缩包包含不安全的文件路径。")
    return path


def _archive_path_key(path: PurePosixPath) -> str:
    return path.as_posix().casefold()


def _read_archive_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > OFFLINE_YOLO_MAX_LABEL_BYTES:
        raise HTTPException(status_code=400, detail=f"标签文件 {info.filename} 超过 5MB。")
    return archive.read(info).decode("utf-8-sig", errors="replace")


def _clean_yolo_label_name(value: str) -> str:
    return value.split("#", 1)[0].strip().strip(",").strip().strip("'\"")


def _labels_from_data_yaml(text: str) -> list[str]:
    inline = re.search(r"^\s*names\s*:\s*\[([^\]]+)\]\s*(?:#.*)?$", text, re.MULTILINE)
    if inline:
        return _normalize_labels(
            [_clean_yolo_label_name(value) for value in inline.group(1).split(",")]
        )

    indexed_names: dict[int, str] = {}
    list_names: list[str] = []
    in_names = False
    names_indent = 0
    for raw_line in text.splitlines():
        if not in_names:
            match = re.match(r"^(\s*)names\s*:\s*$", raw_line)
            if match:
                in_names = True
                names_indent = len(match.group(1))
            continue
        if raw_line.strip() and len(raw_line) - len(raw_line.lstrip()) <= names_indent:
            break
        indexed = re.match(r"^\s*(\d+)\s*:\s*(.+?)\s*$", raw_line)
        if indexed:
            label = _clean_yolo_label_name(indexed.group(2))
            if label:
                indexed_names[int(indexed.group(1))] = label
            continue
        listed = re.match(r"^\s*-\s*(.+?)\s*$", raw_line)
        if listed:
            label = _clean_yolo_label_name(listed.group(1))
            if label:
                list_names.append(label)

    if indexed_names:
        last_index = max(indexed_names)
        if set(indexed_names) != set(range(last_index + 1)):
            return []
        return _normalize_labels([indexed_names[index] for index in range(last_index + 1)])
    return _normalize_labels(list_names)


def _read_yolo_archive_labels(
    archive: zipfile.ZipFile,
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]],
) -> list[str]:
    classes_files = sorted(
        (
            (info, path)
            for info, path in members
            if path.name.casefold() == "classes.txt"
        ),
        key=lambda item: (len(item[1].parts), item[1].as_posix().casefold()),
    )
    for info, _path in classes_files:
        labels = _normalize_labels(
            [_clean_yolo_label_name(line) for line in _read_archive_text(archive, info).splitlines()]
        )
        if labels:
            return labels

    yaml_files = sorted(
        (
            (info, path)
            for info, path in members
            if path.name.casefold() in {"data.yaml", "data.yml"}
        ),
        key=lambda item: (len(item[1].parts), item[1].as_posix().casefold()),
    )
    for info, _path in yaml_files:
        labels = _labels_from_data_yaml(_read_archive_text(archive, info))
        if labels:
            return labels
    return []


def _validate_yolo_label_schema(
    existing_labels: list[str],
    imported_labels: list[str],
) -> list[str]:
    if existing_labels and imported_labels:
        existing_keys = [label.casefold() for label in existing_labels]
        imported_keys = [label.casefold() for label in imported_labels]
        if existing_keys != imported_keys:
            raise HTTPException(
                status_code=400,
                detail=(
                    "压缩包中的类别顺序与当前数据集不一致。请保持 classes.txt/data.yaml "
                    "与数据集类别完全相同，避免类别编号错位。"
                ),
            )
    return list(existing_labels or imported_labels)


def _yolo_label_candidates(image_path: PurePosixPath) -> list[str]:
    candidates: set[str] = {_archive_path_key(image_path.with_suffix(".txt"))}
    parts = list(image_path.parts)
    for index, part in enumerate(parts):
        if part.casefold() != "images":
            continue
        label_parts = [*parts[:index], "labels", *parts[index + 1 :]]
        candidates.add(_archive_path_key(PurePosixPath(*label_parts).with_suffix(".txt")))
    return list(candidates)


def _find_yolo_label_entry(
    image_path: PurePosixPath,
    labels_by_path: dict[str, tuple[zipfile.ZipInfo, PurePosixPath]],
    labels_by_stem: dict[str, list[tuple[zipfile.ZipInfo, PurePosixPath]]],
) -> tuple[zipfile.ZipInfo, PurePosixPath] | None:
    for candidate in _yolo_label_candidates(image_path):
        label_entry = labels_by_path.get(candidate)
        if label_entry is not None:
            return label_entry
    same_name_entries = labels_by_stem.get(image_path.stem.casefold(), [])
    if len(same_name_entries) == 1:
        return same_name_entries[0]
    if len(same_name_entries) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"图片 {image_path.as_posix()} 找到多个同名标签文件。"
                "请使用 images/ 与 labels/ 对应目录，避免标签配对错误。"
            ),
        )
    return None


def _append_or_resolve_yolo_label(token: str, labels: list[str], *, filename: str) -> str:
    value = token.strip()
    if re.fullmatch(r"[+-]?\d+", value):
        index = int(value)
        if index < 0 or index >= len(labels):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"标签文件 {filename} 使用类别编号 {index}，但未找到对应类别。"
                    "请在压缩包中放入 classes.txt 或 data.yaml，或者先在数据集维护类别。"
                ),
            )
        return labels[index]
    label = _annotation_label(value, "类别")
    if not any(existing.casefold() == label.casefold() for existing in labels):
        labels.append(label)
    return label


def _yolo_coordinate(value: str, *, field: str, filename: str, line_number: int) -> float:
    try:
        coordinate = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"标签文件 {filename} 第 {line_number} 行的 {field} 不是数字。",
        ) from exc
    if not math.isfinite(coordinate) or coordinate < 0 or coordinate > 1:
        raise HTTPException(
            status_code=400,
            detail=f"标签文件 {filename} 第 {line_number} 行的 {field} 必须在 0 到 1 之间。",
        )
    return coordinate


def _classification_label_from_path(image_path: PurePosixPath) -> str | None:
    ignored_parts = {"images", "labels", "train", "val", "valid", "test", "tests", "data", "dataset"}
    for part in reversed(image_path.parts[:-1]):
        if part.casefold() not in ignored_parts:
            return part
    return None


def _parse_yolo_annotation(
    archive: zipfile.ZipFile,
    *,
    image_path: PurePosixPath,
    label_entry: tuple[zipfile.ZipInfo, PurePosixPath] | None,
    annotation_type: str,
    labels: list[str],
) -> dict[str, Any]:
    normalized_type = _normalize(annotation_type)
    if label_entry is None:
        if normalized_type == "DETECTION":
            return {"boxes": []}
        if normalized_type == "SEGMENTATION":
            return {"segments": []}
        fallback_label = _classification_label_from_path(image_path)
        if fallback_label:
            return {
                "label": _append_or_resolve_yolo_label(
                    fallback_label,
                    labels,
                    filename=image_path.as_posix(),
                )
            }
        raise HTTPException(
            status_code=400,
            detail=f"分类图片 {image_path.name} 缺少同名 .txt 标签，也无法从父目录推断类别。",
        )

    label_info, _label_path = label_entry
    filename = label_info.filename
    lines = [
        (line_number, line.split("#", 1)[0].strip())
        for line_number, line in enumerate(_read_archive_text(archive, label_info).splitlines(), start=1)
    ]
    meaningful_lines = [(line_number, line) for line_number, line in lines if line]

    if normalized_type == "DETECTION":
        boxes: list[dict[str, Any]] = []
        for line_number, line in meaningful_lines:
            values = line.split()
            if len(values) != 5:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"标签文件 {filename} 第 {line_number} 行不是 YOLO 目标检测格式："
                        "类别 中心X 中心Y 宽 高。"
                    ),
                )
            label = _append_or_resolve_yolo_label(values[0], labels, filename=filename)
            center_x = _yolo_coordinate(values[1], field="中心X", filename=filename, line_number=line_number)
            center_y = _yolo_coordinate(values[2], field="中心Y", filename=filename, line_number=line_number)
            width = _yolo_coordinate(values[3], field="宽", filename=filename, line_number=line_number)
            height = _yolo_coordinate(values[4], field="高", filename=filename, line_number=line_number)
            x = center_x - width / 2
            y = center_y - height / 2
            if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1 or y + height > 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"标签文件 {filename} 第 {line_number} 行的检测框超出图片边界。",
                )
            boxes.append({"label": label, "x": x, "y": y, "width": width, "height": height})
        return {"boxes": boxes}

    if normalized_type == "SEGMENTATION":
        segments: list[dict[str, Any]] = []
        for line_number, line in meaningful_lines:
            values = line.split()
            coordinates = values[1:]
            if len(coordinates) < 6 or len(coordinates) % 2:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"标签文件 {filename} 第 {line_number} 行不是 YOLO 分割格式："
                        "类别 X1 Y1 X2 Y2 X3 Y3 ...。"
                    ),
                )
            label = _append_or_resolve_yolo_label(values[0], labels, filename=filename)
            points = [
                [
                    _yolo_coordinate(coordinates[index], field=f"X{index // 2 + 1}", filename=filename, line_number=line_number),
                    _yolo_coordinate(coordinates[index + 1], field=f"Y{index // 2 + 1}", filename=filename, line_number=line_number),
                ]
                for index in range(0, len(coordinates), 2)
            ]
            segments.append({"label": label, "points": points})
        return {"segments": segments}

    if len(meaningful_lines) != 1 or len(meaningful_lines[0][1].split()) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"分类标签文件 {filename} 必须只包含一个类别编号或类别名称。",
        )
    _line_number, line = meaningful_lines[0]
    return {"label": _append_or_resolve_yolo_label(line, labels, filename=filename)}


def _prepare_yolo_archive_import(
    archive: zipfile.ZipFile,
    dataset: Dataset,
) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath, dict[str, Any]]], list[str]]:
    all_members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    image_members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    labels_by_path: dict[str, tuple[zipfile.ZipInfo, PurePosixPath]] = {}
    labels_by_stem: dict[str, list[tuple[zipfile.ZipInfo, PurePosixPath]]] = {}
    seen_paths: set[str] = set()
    total_size = 0

    for info in archive.infolist():
        if info.is_dir() or info.filename.startswith("__MACOSX/"):
            continue
        total_size += info.file_size
        if total_size > OFFLINE_YOLO_MAX_UNCOMPRESSED_BYTES:
            raise HTTPException(status_code=400, detail="离线标注压缩包解压后超过 1GB，请拆分后导入。")
        path = _archive_member_path(info)
        path_key = _archive_path_key(path)
        if path_key in seen_paths:
            raise HTTPException(status_code=400, detail=f"压缩包存在重复文件路径：{path.as_posix()}。")
        seen_paths.add(path_key)
        all_members.append((info, path))
        suffix = path.suffix.casefold()
        if suffix in IMAGE_SUFFIXES:
            image_members.append((info, path))
        elif suffix == ".txt" and path.name.casefold() != "classes.txt":
            labels_by_path[path_key] = (info, path)
            labels_by_stem.setdefault(path.stem.casefold(), []).append((info, path))

    if not image_members:
        raise HTTPException(status_code=400, detail="压缩包内未找到可导入的图片文件。")
    if len(image_members) > OFFLINE_YOLO_MAX_IMAGES:
        raise HTTPException(status_code=400, detail="单次最多导入 2000 张图片，请拆分压缩包。")

    imported_labels = _read_yolo_archive_labels(archive, all_members)
    labels = _validate_yolo_label_schema(
        _normalize_labels(dataset.label_schema_json or []),
        imported_labels,
    )
    prepared: list[tuple[zipfile.ZipInfo, PurePosixPath, dict[str, Any]]] = []
    for image_info, image_path in image_members:
        annotation = _parse_yolo_annotation(
            archive,
            image_path=image_path,
            label_entry=_find_yolo_label_entry(image_path, labels_by_path, labels_by_stem),
            annotation_type=dataset.annotation_type,
            labels=labels,
        )
        _validate_training_annotation(dataset, annotation)
        prepared.append((image_info, image_path, annotation))
    return prepared, _normalize_labels(labels)


def _store_yolo_archive_item(
    database: Session,
    dataset: Dataset,
    archive: zipfile.ZipFile,
    image_info: zipfile.ZipInfo,
    image_path: PurePosixPath,
    annotation: dict[str, Any],
    seen_hashes: set[str],
) -> tuple[DatasetItem | None, Path | None]:
    suffix = image_path.suffix.lower() or ".bin"
    target_dir = Path(PROJECT_ROOT / "uploads" / "datasets" / dataset.code)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    with archive.open(image_info, "r") as source, target.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    content_hash = digest.hexdigest()
    duplicate = content_hash in seen_hashes or database.scalar(
        select(DatasetItem.id).where(
            DatasetItem.dataset_id == dataset.id,
            DatasetItem.content_hash == content_hash,
            DatasetItem.is_deleted.is_(False),
        )
    )
    if duplicate:
        target.unlink(missing_ok=True)
        return None, None
    seen_hashes.add(content_hash)
    item = DatasetItem(
        dataset_id=dataset.id,
        media_path=str(target),
        original_name=image_path.name,
        media_type=dataset.media_type,
        annotation_status="LABELED",
        annotation_json=annotation,
        split=None,
        source="OFFLINE_YOLO",
        content_hash=content_hash,
    )
    database.add(item)
    return item, target


@router.post("/{dataset_id}/imports/yolo")
def import_offline_yolo_dataset(
    dataset_id: int,
    archive: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.get(Dataset, dataset_id)
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if dataset.purpose != "TRAIN" or dataset.media_type != "IMAGE":
        raise HTTPException(status_code=400, detail="离线 YOLO 标注导入仅支持图片训练数据集。")
    if _normalize(dataset.annotation_type) not in {"DETECTION", "SEGMENTATION", "CLASSIFICATION"}:
        raise HTTPException(status_code=400, detail="当前数据集未选择可导入的 YOLO 标注类型。")
    if Path(archive.filename or "").suffix.casefold() != ".zip":
        raise HTTPException(status_code=400, detail="请上传包含图片和标签文件的 .zip 压缩包。")

    created: list[DatasetItem] = []
    skipped: list[dict[str, str]] = []
    created_paths: list[Path] = []
    try:
        archive.file.seek(0)
        with zipfile.ZipFile(archive.file) as zip_archive:
            prepared_items, labels = _prepare_yolo_archive_import(zip_archive, dataset)
            seen_hashes: set[str] = set()
            for image_info, image_path, annotation in prepared_items:
                item, target = _store_yolo_archive_item(
                    database,
                    dataset,
                    zip_archive,
                    image_info,
                    image_path,
                    annotation,
                    seen_hashes,
                )
                if item is None:
                    skipped.append({"filename": image_path.name, "reason": "重复"})
                else:
                    created.append(item)
                    if target is not None:
                        created_paths.append(target)
            if created:
                _merge_dataset_labels(dataset, labels)
                dataset.revision += 1
            database.commit()
    except HTTPException:
        database.rollback()
        for target in created_paths:
            target.unlink(missing_ok=True)
        raise
    except zipfile.BadZipFile as exc:
        database.rollback()
        for target in created_paths:
            target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传文件不是有效的 ZIP 压缩包。") from exc
    except Exception:
        database.rollback()
        for target in created_paths:
            target.unlink(missing_ok=True)
        raise

    negative_count = sum(
        1
        for item in created
        if not (item.annotation_json or {}).get("boxes", (item.annotation_json or {}).get("segments", [True]))
    )
    return {
        "created": [_item_payload(item) for item in created],
        "created_count": len(created),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "labels": list(dataset.label_schema_json or []),
        "negative_count": negative_count,
        "annotation_type": dataset.annotation_type,
    }


@router.post("/{dataset_id}/items")
def upload_dataset_item(
    dataset_id: int,
    file: UploadFile = File(...),
    ground_truth: str | None = Form(default=None),
    split: str | None = Form(default=None),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.get(Dataset, dataset_id)
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    normalized_truth = _normalized_ground_truth(dataset, ground_truth)
    item = _store_dataset_item(
        database,
        dataset,
        file,
        ground_truth=normalized_truth,
        split=split,
    )
    if item is None:
        raise HTTPException(status_code=409, detail="该文件已存在于当前数据集。")
    dataset.revision += 1
    database.commit()
    database.refresh(item)
    return _item_payload(item)


@router.post("/{dataset_id}/items/batch")
def upload_dataset_items_batch(
    dataset_id: int,
    files: list[UploadFile] = File(...),
    ground_truth: str | None = Form(default=None),
    split: str | None = Form(default=None),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Upload multiple images, including a browser-selected folder.

    Browser folder selection is still sent as a normal list of files.  The
    server intentionally stores only generated names, so source folder paths
    never leak into training artifacts or filesystem paths.
    """

    dataset = database.get(Dataset, dataset_id)
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个素材文件。")
    if len(files) > 2000:
        raise HTTPException(status_code=400, detail="单次最多上传 2000 个文件，请分批上传。")
    normalized_truth = _normalized_ground_truth(dataset, ground_truth)
    created: list[DatasetItem] = []
    skipped: list[dict[str, str]] = []
    try:
        for file in files:
            item = _store_dataset_item(
                database,
                dataset,
                file,
                ground_truth=normalized_truth,
                split=split,
            )
            if item is None:
                skipped.append({"filename": file.filename or "未命名文件", "reason": "重复"})
            else:
                created.append(item)
        if created:
            dataset.revision += 1
        database.commit()
    except Exception:
        database.rollback()
        raise
    return {
        "created": [_item_payload(item) for item in created],
        "created_count": len(created),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


@router.post("/{dataset_id}/items/{item_id}/pre-annotations")
def pre_annotate_dataset_item(
    dataset_id: int,
    item_id: int,
    payload: DatasetItemPreAnnotationRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Generate editable AI annotation candidates from a published YOLO model.

    Nothing is written to ``DatasetItem`` here.  That makes the contract safe
    for production datasets: an operator may review, edit, reject or save the
    suggestions in the annotation workspace before they become training truth.
    """

    dataset = database.get(Dataset, dataset_id)
    item = database.get(DatasetItem, item_id)
    if dataset is None or dataset.is_deleted or item is None or item.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="数据集样本不存在。")
    if dataset.purpose != "TRAIN" or dataset.media_type != "IMAGE" or item.media_type != "IMAGE":
        raise HTTPException(status_code=400, detail="自动预标注仅支持图片训练数据集。")

    annotation_type = _normalize(dataset.annotation_type)
    required_task_type = ANNOTATION_MODEL_TASKS.get(annotation_type)
    if required_task_type is None:
        raise HTTPException(status_code=400, detail="当前数据集的标注方式不支持自动预标注。")

    model_row = database.execute(
        select(VisionModel, VisionModelVersion)
        .join(VisionModelVersion, VisionModelVersion.vision_model_id == VisionModel.id)
        .where(
            VisionModelVersion.id == payload.vision_model_version_id,
            VisionModel.is_deleted.is_(False),
            VisionModel.enabled.is_(True),
            VisionModelVersion.is_deleted.is_(False),
            VisionModelVersion.status == "PUBLISHED",
        )
    ).first()
    if model_row is None:
        raise HTTPException(status_code=404, detail="未找到可用的已发布模型版本。")
    model, version = model_row
    if _normalize(model.task_type) != required_task_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"当前数据集需要 {required_task_type} 模型，"
                f"所选模型为 {_normalize(model.task_type)}。"
            ),
        )
    try:
        specification = build_published_model_spec(version, model)
        inference = run_yolo_inference(
            specification,
            item.media_path,
            config={
                "confidence": payload.confidence,
                "iou": payload.iou,
                "max_detections": payload.max_detections,
            },
        )
    except VisionModelRuntimeError as exc:
        raise HTTPException(status_code=422, detail=f"自动预标注失败：{exc}") from exc

    annotation, warnings = _pre_annotation_from_inference(dataset, inference)
    annotation["_meta"] = {
        **dict(annotation.get("_meta") or {}),
        "model_version_id": version.id,
        "model_name": model.name,
        "model_version": version.version,
        "task_type": model.task_type,
        "confidence_threshold": payload.confidence,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    candidate_count = (
        len(annotation.get("boxes") or [])
        if annotation_type == "DETECTION"
        else len(annotation.get("segments") or [])
        if annotation_type == "SEGMENTATION"
        else 1
    )
    labels = _annotation_labels(dataset, annotation)
    existing_labels = {label.casefold() for label in dataset.label_schema_json or []}
    unknown_labels = sorted({label for label in labels if label.casefold() not in existing_labels})
    if unknown_labels:
        warnings.append("模型建议包含数据集类别字典外的类别；保存前请确认类别名称和编号顺序。")
    return {
        "annotation": annotation,
        "candidate_count": candidate_count,
        "unknown_labels": unknown_labels,
        "warnings": warnings,
        "model": {
            "version_id": version.id,
            "model_id": model.id,
            "code": model.code,
            "name": model.name,
            "version": version.version,
            "task_type": model.task_type,
        },
        "inference": {
            "confidence": inference.get("confidence"),
            "object_count": inference.get("object_count"),
            "classification": inference.get("classification"),
            "elapsed_note": "预标注结果尚未保存为训练真值，请人工确认后保存。",
        },
    }


@router.put("/{dataset_id}/items/{item_id}")
def update_dataset_item(
    dataset_id: int,
    item_id: int,
    payload: DatasetItemUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.get(Dataset, dataset_id)
    item = database.get(DatasetItem, item_id)
    if dataset is None or dataset.is_deleted or item is None or item.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="数据集样本不存在。")
    values = payload.model_dump(exclude_unset=True)
    if dataset.purpose == "TEST" and "annotation_json" in values:
        raise HTTPException(status_code=400, detail="评测数据集只需维护图片 OK/NG，不支持训练标注。")
    if dataset.purpose == "TRAIN" and values.get("ground_truth"):
        raise HTTPException(status_code=400, detail="YOLO 训练数据集不能维护 OK/NG 真值，请使用图片标注器。")
    if dataset.purpose == "TRAIN" and "annotation_json" in values:
        annotation = values["annotation_json"]
        if not isinstance(annotation, dict):
            raise HTTPException(status_code=400, detail="标注内容必须是对象。")
        _validate_training_annotation(dataset, annotation)
        _merge_dataset_labels(dataset, _annotation_labels(dataset, annotation))
        values["annotation_status"] = "LABELED"
    if "ground_truth" in values and values["ground_truth"] is not None:
        values["ground_truth"] = _normalize(values["ground_truth"])
        if values["ground_truth"] not in GROUND_TRUTHS:
            raise HTTPException(status_code=400, detail="测试真值仅支持 OK 或 NG。")
    if "annotation_status" in values and values["annotation_status"]:
        values["annotation_status"] = _normalize(values["annotation_status"])
    if "split" in values and values["split"]:
        values["split"] = _normalize(values["split"])
    for key, value in values.items():
        setattr(item, key, value)
    if item.ground_truth and item.annotation_status == "PENDING":
        item.annotation_status = "LABELED"
    dataset.revision += 1
    database.commit()
    return _item_payload(item)


@router.delete("/{dataset_id}/items/{item_id}")
def remove_dataset_item(
    dataset_id: int,
    item_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = database.get(Dataset, dataset_id)
    item = database.get(DatasetItem, item_id)
    if dataset is None or item is None or item.dataset_id != dataset.id:
        raise HTTPException(status_code=404, detail="数据集样本不存在。")
    item.is_deleted = True
    dataset.revision += 1
    database.commit()
    return {"id": item.id, "deleted": True}
