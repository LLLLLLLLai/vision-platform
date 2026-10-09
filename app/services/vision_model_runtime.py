"""In-process inference adapter for published local YOLO model versions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.core.config import PROJECT_ROOT


class VisionModelRuntimeError(RuntimeError):
    """Raised when a published vision model cannot provide a valid result."""


_GENERIC_OUTPUT_PROFILE: dict[str, Any] = {
    "task_type": "UNKNOWN",
    "display_name": "通用模型",
    "summary": "仅声明通用结果、置信度和说明；未声明定位框能力。",
    "supports_objects": False,
    "supports_bbox": False,
    "supports_mask": False,
    "supports_classification": False,
    "output_keys": ("result", "confidence", "reason", "task_type"),
}

# This contract is intentionally adapter-owned rather than inferred from a model
# name.  A segmentation model from another runtime, for example, must explicitly
# declare whether its adapter can provide a bounding box before a crop node may
# consume it.
_TASK_OUTPUT_PROFILES: dict[str, dict[str, Any]] = {
    "YOLO_DETECTION": {
        "task_type": "YOLO_DETECTION",
        "display_name": "YOLO 目标检测",
        "summary": "输出目标列表、类别、置信度、数量和 bbox。",
        "supports_objects": True,
        "supports_bbox": True,
        "supports_mask": False,
        "supports_classification": False,
        "output_keys": (
            "result",
            "confidence",
            "reason",
            "task_type",
            "image",
            "image.width",
            "image.height",
            "detection_count",
            "object_count",
            "objects",
            "objects.0.class_id",
            "objects.0.label",
            "objects.0.confidence",
            "objects.0.bbox",
            "objects.0.bbox_format",
        ),
    },
    "YOLO_SEGMENTATION": {
        "task_type": "YOLO_SEGMENTATION",
        "display_name": "YOLO 目标分割",
        "summary": "当前 YOLO 分割适配器输出目标列表、bbox 和 mask。",
        "supports_objects": True,
        "supports_bbox": True,
        "supports_mask": True,
        "supports_classification": False,
        "output_keys": (
            "result",
            "confidence",
            "reason",
            "task_type",
            "image",
            "image.width",
            "image.height",
            "detection_count",
            "object_count",
            "objects",
            "objects.0.class_id",
            "objects.0.label",
            "objects.0.confidence",
            "objects.0.bbox",
            "objects.0.bbox_format",
            "objects.0.mask",
            "objects.0.mask_format",
        ),
    },
    "YOLO_CLASSIFICATION": {
        "task_type": "YOLO_CLASSIFICATION",
        "display_name": "YOLO 图像分类",
        "summary": "输出 Top1 / Top5 类别和置信度，不输出 bbox。",
        "supports_objects": False,
        "supports_bbox": False,
        "supports_mask": False,
        "supports_classification": True,
        "output_keys": (
            "result",
            "confidence",
            "reason",
            "task_type",
            "image",
            "image.width",
            "image.height",
            "classification",
            "classification.top1_class_id",
            "classification.top1_label",
            "classification.top1_confidence",
            "classification.top5",
        ),
    },
}


def task_output_profile(task_type: str | None) -> dict[str, Any]:
    """Return the UI/runtime contract guaranteed by the active adapter.

    Unknown task types deliberately fall back to the generic profile.  This is
    safer than exposing a bbox token which a future model may never produce.
    """

    profile = _TASK_OUTPUT_PROFILES.get(str(task_type or "").upper(), _GENERIC_OUTPUT_PROFILE)
    return {
        **profile,
        "output_keys": list(profile["output_keys"]),
    }


@dataclass(frozen=True)
class PublishedVisionModelSpec:
    version_id: int
    model_id: int
    model_code: str
    model_name: str
    task_type: str
    labels: tuple[str, ...]
    weights_path: Path


_MODEL_CACHE: dict[str, tuple[int, Any]] = {}

_ULTRALYTICS_TASK_TYPES = {
    "detect": "YOLO_DETECTION",
    "segment": "YOLO_SEGMENTATION",
    "classify": "YOLO_CLASSIFICATION",
}


def resolve_weights_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_published_model_spec(version: Any, model: Any) -> PublishedVisionModelSpec:
    weights_path = resolve_weights_path(getattr(version, "weights_path", None))
    if weights_path is None or not weights_path.is_file():
        raise VisionModelRuntimeError("已发布模型的权重文件不存在，请重新训练或修正权重路径。")
    return PublishedVisionModelSpec(
        version_id=int(version.id),
        model_id=int(model.id),
        model_code=str(model.code),
        model_name=str(model.name),
        task_type=str(model.task_type).upper(),
        labels=tuple(str(label).strip() for label in (model.labels_json or []) if str(label).strip()),
        weights_path=weights_path,
    )


def clear_model_cache() -> None:
    _MODEL_CACHE.clear()


def _load_yolo(weights_path: Path) -> Any:
    cache_key = str(weights_path.resolve())
    marker = weights_path.stat().st_mtime_ns
    cached = _MODEL_CACHE.get(cache_key)
    if cached and cached[0] == marker:
        return cached[1]
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise VisionModelRuntimeError(
            "未安装 ultralytics，无法执行已发布 YOLO 模型。"
        ) from exc
    model = YOLO(str(weights_path))
    _MODEL_CACHE[cache_key] = (marker, model)
    return model


def validate_yolo_weights_for_task(
    weights_path: Path,
    expected_task_type: str,
) -> dict[str, Any]:
    """Load an imported Ultralytics checkpoint and verify its task contract.

    Importing a checkpoint is intentionally separate from publishing it.  This
    lightweight check catches the common and dangerous case where a detection
    model is accidentally registered as a classifier (or vice versa), before a
    workflow can consume its output contract.
    """

    if not weights_path.is_file():
        raise VisionModelRuntimeError("模型权重文件不存在。")
    if weights_path.suffix.lower() != ".pt":
        raise VisionModelRuntimeError("当前仅支持导入 Ultralytics YOLO 的 .pt 权重文件。")

    model = _load_yolo(weights_path)
    detected_task = str(getattr(model, "task", "") or "").strip().lower()
    actual_task_type = _ULTRALYTICS_TASK_TYPES.get(detected_task)
    if actual_task_type is None:
        raise VisionModelRuntimeError(
            "无法从权重识别 YOLO 任务类型；请确认该文件是兼容 Ultralytics 的检测、分割或分类 .pt 权重。"
        )

    required_task_type = str(expected_task_type or "").upper()
    if actual_task_type != required_task_type:
        labels = {
            "YOLO_DETECTION": "目标检测",
            "YOLO_SEGMENTATION": "目标分割",
            "YOLO_CLASSIFICATION": "图像分类",
        }
        raise VisionModelRuntimeError(
            f"导入权重实际是 {labels.get(actual_task_type, actual_task_type)} 模型，"
            f"与当前模型卡片的 {labels.get(required_task_type, required_task_type)} 类型不一致。"
        )

    raw_names = getattr(model, "names", None)
    if isinstance(raw_names, dict):
        class_names = [str(raw_names[key]) for key in sorted(raw_names)]
    elif isinstance(raw_names, (list, tuple)):
        class_names = [str(item) for item in raw_names]
    else:
        class_names = []
    return {
        "detected_task": detected_task,
        "task_type": actual_task_type,
        "class_names": class_names,
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return value if isinstance(value, list) else list(value)


def _label_for(class_id: int, result_names: Any, labels: tuple[str, ...]) -> str:
    if class_id < len(labels):
        return labels[class_id]
    if isinstance(result_names, dict):
        return str(result_names.get(class_id, class_id))
    if isinstance(result_names, (list, tuple)) and class_id < len(result_names):
        return str(result_names[class_id])
    return str(class_id)


def _mask_points(masks: Any, index: int, point_limit: int) -> list[list[float]] | None:
    if masks is None:
        return None
    normalized = getattr(masks, "xyn", None)
    if normalized is None:
        return None
    values = _as_list(normalized)
    if index >= len(values):
        return None
    points = values[index]
    if hasattr(points, "tolist"):
        points = points.tolist()
    if not isinstance(points, (list, tuple)):
        return None
    if len(points) > point_limit:
        step = max(1, len(points) // point_limit)
        points = points[::step][:point_limit]
    return [
        [round(float(point[0]), 6), round(float(point[1]), 6)]
        for point in points
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]


def _inspection_decision(
    *,
    task_type: str,
    objects: list[dict[str, Any]],
    classification: dict[str, Any] | None,
    config: dict[str, Any],
) -> tuple[str, str]:
    expected_label = str(config.get("expected_label") or config.get("expected_class") or "").strip()
    if task_type == "YOLO_CLASSIFICATION":
        if not expected_label:
            return "UNCERTAIN", "分类节点未配置 expected_label，已返回模型原始预测。"
        actual_label = str((classification or {}).get("top1_label") or "")
        return (
            ("OK", "分类结果符合期望类别。")
            if actual_label == expected_label
            else ("NG", f"期望类别为 {expected_label}，实际类别为 {actual_label or '无'}。")
        )

    filtered = [
        item for item in objects
        if not expected_label or str(item.get("label")) == expected_label
    ]
    actual_count = len(filtered)
    has_expectation = any(
        key in config for key in ("expected_count", "expected_min_count", "expected_max_count")
    )
    if not has_expectation:
        return "UNCERTAIN", "模型已执行；请在节点中配置数量期望或追加规则节点后再决定 OK/NG。"
    expected_count = config.get("expected_count")
    minimum = config.get("expected_min_count")
    maximum = config.get("expected_max_count")
    if expected_count is not None and actual_count != int(expected_count):
        return "NG", f"目标数量为 {actual_count}，期望为 {int(expected_count)}。"
    if minimum is not None and actual_count < int(minimum):
        return "NG", f"目标数量为 {actual_count}，低于最小值 {int(minimum)}。"
    if maximum is not None and actual_count > int(maximum):
        return "NG", f"目标数量为 {actual_count}，高于最大值 {int(maximum)}。"
    return "OK", f"目标数量为 {actual_count}，符合节点期望。"


def _image_metadata(source: Path) -> dict[str, Any]:
    """Read image dimensions once so geometry outputs have an explicit space."""

    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            width, height = image.size
    except Exception as exc:
        raise VisionModelRuntimeError(f"无法读取待检测图片尺寸：{exc}") from exc
    return {
        "path": str(source),
        "width": int(width),
        "height": int(height),
        "coordinate_space": "PIXEL",
    }


def run_yolo_inference(
    spec: PublishedVisionModelSpec,
    image_path: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a published YOLO version and return a workflow-friendly JSON result."""

    source = Path(image_path)
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    if not source.is_file():
        raise VisionModelRuntimeError("待检测图片不存在或当前服务无法读取。")
    image_metadata = _image_metadata(source)
    parameters = dict(config or {})
    confidence_threshold = float(parameters.get("confidence", parameters.get("conf", 0.25)))
    iou_threshold = float(parameters.get("iou", 0.45))
    max_detections = int(parameters.get("max_detections", parameters.get("max_det", 300)))
    point_limit = max(8, int(parameters.get("mask_points_limit", 128)))
    predict_kwargs: dict[str, Any] = {
        "source": str(source),
        "conf": confidence_threshold,
        "iou": iou_threshold,
        "max_det": max_detections,
        "verbose": False,
    }
    if parameters.get("device") not in (None, ""):
        predict_kwargs["device"] = parameters["device"]
    try:
        result_list = _load_yolo(spec.weights_path).predict(**predict_kwargs)
        if not result_list:
            raise VisionModelRuntimeError("YOLO 未返回任何推理结果。")
        result = result_list[0]
    except VisionModelRuntimeError:
        raise
    except Exception as exc:
        raise VisionModelRuntimeError(f"YOLO 推理失败：{exc}") from exc

    objects: list[dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    if boxes is not None:
        coordinates = _as_list(getattr(boxes, "xyxy", None))
        confidences = _as_list(getattr(boxes, "conf", None))
        classes = _as_list(getattr(boxes, "cls", None))
        for index, bbox in enumerate(coordinates):
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            class_id = int(float(classes[index])) if index < len(classes) else -1
            score = float(confidences[index]) if index < len(confidences) else 0.0
            entry: dict[str, Any] = {
                "index": index,
                "class_id": class_id,
                "label": _label_for(class_id, getattr(result, "names", None), spec.labels),
                "confidence": round(score, 6),
                "bbox": [round(float(value), 2) for value in bbox[:4]],
                "bbox_format": "XYXY_PIXEL",
            }
            mask = _mask_points(getattr(result, "masks", None), index, point_limit)
            if mask:
                entry["mask"] = mask
                entry["mask_format"] = "POLYGON_NORMALIZED"
            objects.append(entry)

    classification: dict[str, Any] | None = None
    probabilities = getattr(result, "probs", None)
    if probabilities is not None:
        top1 = int(getattr(probabilities, "top1", -1))
        top1_confidence = float(getattr(probabilities, "top1conf", 0.0))
        classification = {
            "top1_class_id": top1,
            "top1_label": _label_for(top1, getattr(result, "names", None), spec.labels),
            "top1_confidence": round(top1_confidence, 6),
        }
        top5 = _as_list(getattr(probabilities, "top5", None))
        top5_confidence = _as_list(getattr(probabilities, "top5conf", None))
        if top5:
            classification["top5"] = [
                {
                    "class_id": int(class_id),
                    "label": _label_for(int(class_id), getattr(result, "names", None), spec.labels),
                    "confidence": round(float(top5_confidence[index]), 6)
                    if index < len(top5_confidence)
                    else None,
                }
                for index, class_id in enumerate(top5)
            ]

    decision, reason = _inspection_decision(
        task_type=spec.task_type,
        objects=objects,
        classification=classification,
        config=parameters,
    )
    confidence = (
        float(classification["top1_confidence"])
        if classification
        else max((float(item["confidence"]) for item in objects), default=0.0)
    )
    return {
        "result": decision,
        "status": "COMPLETED",
        "reason": reason,
        "confidence": round(confidence, 6),
        "model_version_id": spec.version_id,
        "model_code": spec.model_code,
        "task_type": spec.task_type,
        "output_profile": task_output_profile(spec.task_type),
        "image": image_metadata,
        "objects": objects,
        "detection_count": len(objects),
        "object_count": len(objects),
        "classification": classification,
        "parameters": {
            "confidence": confidence_threshold,
            "iou": iou_threshold,
            "max_detections": max_detections,
        },
    }
