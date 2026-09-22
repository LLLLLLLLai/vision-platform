"""Local Ultralytics YOLO dataset export and training support.

The platform stores user annotations as JSON so the web layer is independent of
the training framework.  This module validates that JSON, creates an immutable
YOLO-compatible run directory and starts one local Ultralytics training run.
It deliberately has no database dependency; the automation worker passes a
plain snapshot into it so a long-running GPU operation never holds SQLite
objects or transactions open.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from app.core.config import PROJECT_ROOT, settings


class YoloTrainingError(RuntimeError):
    """Raised when a training run cannot safely be exported or executed."""


TASK_ANNOTATION_TYPES = {
    "YOLO_DETECTION": "DETECTION",
    "YOLO_SEGMENTATION": "SEGMENTATION",
    "YOLO_CLASSIFICATION": "CLASSIFICATION",
}

TASK_DEFAULT_WEIGHTS = {
    "YOLO_DETECTION": "yolo11n.pt",
    "YOLO_SEGMENTATION": "yolo11n-seg.pt",
    "YOLO_CLASSIFICATION": "yolo11n-cls.pt",
}


def required_annotation_type(task_type: str) -> str:
    try:
        return TASK_ANNOTATION_TYPES[task_type.upper()]
    except KeyError as exc:
        raise YoloTrainingError(f"不支持的 YOLO 训练任务类型：{task_type}") from exc


def _safe_component(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "._-" else "_" for character in value)
    return cleaned.strip("._") or "unnamed"


def _resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _resolve_base_model(raw_path: str) -> str:
    """Prefer an explicitly supplied or project-local base weight.

    A plain Ultralytics model name such as `yolo11n.pt` is deliberately kept
    unchanged as the final fallback, allowing connected development machines to
    use Ultralytics' standard cache/download behavior. Offline deployments can
    place the same file in `VISION_MODEL_STORAGE_ROOT` instead.
    """

    candidate = Path(raw_path)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise YoloTrainingError(f"配置的基础权重文件不存在：{candidate}")
        return str(candidate)
    project_candidate = PROJECT_ROOT / candidate
    if project_candidate.is_file():
        return str(project_candidate)
    storage_root = Path(settings.vision_model_storage_root)
    if not storage_root.is_absolute():
        storage_root = PROJECT_ROOT / storage_root
    storage_candidate = storage_root / candidate
    if storage_candidate.is_file():
        return str(storage_candidate)
    if "/" in raw_path or "\\" in raw_path:
        raise YoloTrainingError(f"配置的基础权重文件不存在：{project_candidate}")
    return raw_path


def _active_items(items: Iterable[Any]) -> list[Any]:
    return [item for item in items if not getattr(item, "is_deleted", False)]


def _annotation_list(annotation: dict[str, Any], task_type: str) -> list[dict[str, Any]]:
    if task_type == "YOLO_DETECTION":
        values = annotation.get("boxes", [])
    elif task_type == "YOLO_SEGMENTATION":
        values = annotation.get("segments", annotation.get("polygons", []))
    else:
        values = []
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise YoloTrainingError("标注内容格式不正确。")
    return values


def _label_from_annotation(annotation: dict[str, Any]) -> str:
    value = annotation.get("label", annotation.get("class_name", ""))
    return str(value).strip()


def validate_training_dataset(
    *,
    task_type: str,
    labels: list[str],
    dataset_annotation_type: str,
    items: Iterable[Any],
) -> dict[str, Any]:
    """Validate a training set before it is allowed into the GPU queue.

    Empty detection/segmentation lists are valid negative images.  A complete
    dataset must still contain at least one positive object, otherwise an
    apparently successful run would teach the model to detect nothing.
    """

    normalized_task = task_type.upper()
    expected_annotation_type = required_annotation_type(normalized_task)
    normalized_labels = [str(label).strip() for label in labels if str(label).strip()]
    if not normalized_labels:
        raise YoloTrainingError("训练模型至少需要配置一个识别类别。")
    if len(set(normalized_labels)) != len(normalized_labels):
        raise YoloTrainingError("训练模型的识别类别存在重复名称。")
    if str(dataset_annotation_type or "").upper() != expected_annotation_type:
        raise YoloTrainingError(
            f"当前模型需要 {expected_annotation_type} 标注，但数据集标注类型为 "
            f"{dataset_annotation_type or 'NONE'}。"
        )

    active_items = _active_items(items)
    if not active_items:
        raise YoloTrainingError("训练数据集没有可用素材。")

    errors: list[str] = []
    positive_count = 0
    seen_labels: set[str] = set()
    for item in active_items:
        item_name = getattr(item, "original_name", f"样本#{getattr(item, 'id', '?')}")
        item_path = _resolve_path(str(getattr(item, "media_path", "")))
        if not item_path.is_file():
            errors.append(f"{item_name}：素材文件不存在。")
            continue
        if str(getattr(item, "annotation_status", "")).upper() != "LABELED":
            errors.append(f"{item_name}：尚未完成标注。")
            continue
        annotation = getattr(item, "annotation_json", None)
        if not isinstance(annotation, dict):
            errors.append(f"{item_name}：标注内容不是对象。")
            continue
        try:
            if normalized_task == "YOLO_CLASSIFICATION":
                label = _label_from_annotation(annotation)
                if not label:
                    raise YoloTrainingError("缺少 label。")
                if label not in normalized_labels:
                    raise YoloTrainingError(f"类别“{label}”不在模型类别列表中。")
                positive_count += 1
                seen_labels.add(label)
                continue

            annotations = _annotation_list(annotation, normalized_task)
            for entry in annotations:
                label = _label_from_annotation(entry)
                if not label:
                    raise YoloTrainingError("有对象未填写 label。")
                if label not in normalized_labels:
                    raise YoloTrainingError(f"类别“{label}”不在模型类别列表中。")
                if normalized_task == "YOLO_DETECTION":
                    _read_box(entry)
                else:
                    _read_points(entry)
                positive_count += 1
                seen_labels.add(label)
        except YoloTrainingError as exc:
            errors.append(f"{item_name}：{exc}")

    if errors:
        preview = "；".join(errors[:8])
        suffix = "；……" if len(errors) > 8 else ""
        raise YoloTrainingError(f"训练数据校验失败：{preview}{suffix}")
    if positive_count == 0:
        raise YoloTrainingError("训练数据中没有任何已标注对象，不能训练只输出空结果的模型。")
    if normalized_task == "YOLO_CLASSIFICATION" and len(seen_labels) < 2:
        raise YoloTrainingError("YOLO 分类训练至少需要两个已标注类别。")

    return {
        "task_type": normalized_task,
        "annotation_type": expected_annotation_type,
        "sample_count": len(active_items),
        "positive_annotation_count": positive_count,
        "labels_in_samples": sorted(seen_labels),
    }


def build_training_snapshot(items: Iterable[Any]) -> list[dict[str, Any]]:
    """Create a JSON-safe immutable input manifest after split assignment."""

    return [
        {
            "dataset_item_id": int(item.id),
            "media_path": str(item.media_path),
            "original_name": str(item.original_name),
            "annotation_status": str(item.annotation_status),
            "annotation_json": item.annotation_json or {},
            "split": str(item.split or "TRAIN").upper(),
            "content_hash": item.content_hash,
        }
        for item in sorted(_active_items(items), key=lambda item: item.id)
    ]


@dataclass(frozen=True)
class YoloTrainingSpec:
    job_id: int
    vision_model_id: int
    model_code: str
    task_type: str
    base_model: str
    labels: tuple[str, ...]
    dataset_id: int
    dataset_code: str
    version: str
    samples: tuple[dict[str, Any], ...]
    epochs: int
    image_size: int
    batch_size: int | None
    device: str | int | None
    extra_params: dict[str, Any]
    artifact_root: Path

    @property
    def run_dir(self) -> Path:
        return (
            self.artifact_root
            / _safe_component(self.model_code)
            / _safe_component(self.version)
            / f"job_{self.job_id}"
        )

    @property
    def log_path(self) -> Path:
        return self.run_dir / "training.log"


def build_training_spec(
    *,
    job_id: int,
    vision_model_id: int,
    model_code: str,
    task_type: str,
    base_model: str | None,
    labels: list[str],
    dataset_id: int,
    dataset_code: str,
    version: str,
    samples: list[dict[str, Any]],
    config: dict[str, Any],
    device: str | int | None,
    artifact_root: str | Path | None = None,
) -> YoloTrainingSpec:
    normalized_task = task_type.upper()
    required_annotation_type(normalized_task)
    normalized_labels = tuple(str(label).strip() for label in labels if str(label).strip())
    return YoloTrainingSpec(
        job_id=int(job_id),
        vision_model_id=int(vision_model_id),
        model_code=str(model_code),
        task_type=normalized_task,
        base_model=str(base_model or TASK_DEFAULT_WEIGHTS[normalized_task]),
        labels=normalized_labels,
        dataset_id=int(dataset_id),
        dataset_code=str(dataset_code),
        version=str(version),
        samples=tuple(samples),
        epochs=int(config.get("epochs", 100)),
        image_size=int(config.get("image_size", 640)),
        batch_size=(int(config["batch_size"]) if config.get("batch_size") else None),
        device=device,
        extra_params=dict(config.get("extra_params_json") or {}),
        artifact_root=Path(artifact_root or settings.training_artifacts_root),
    )


def _copy_or_link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _read_box(entry: dict[str, Any]) -> tuple[float, float, float, float]:
    if isinstance(entry.get("bbox"), (list, tuple)) and len(entry["bbox"]) == 4:
        values = entry["bbox"]
    else:
        values = [entry.get("x"), entry.get("y"), entry.get("width"), entry.get("height")]
    try:
        x, y, width, height = (float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise YoloTrainingError("检测框必须包含 x、y、width、height。") from exc
    if width <= 0 or height <= 0:
        raise YoloTrainingError("检测框宽高必须大于 0。")
    return x, y, width, height


def _read_points(entry: dict[str, Any]) -> list[tuple[float, float]]:
    raw_points = entry.get("points", entry.get("polygon", []))
    if not isinstance(raw_points, list) or len(raw_points) < 3:
        raise YoloTrainingError("分割标注至少需要三个 points 点。")
    points: list[tuple[float, float]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
            raise YoloTrainingError("分割 points 必须是 [x, y] 数组。")
        try:
            points.append((float(raw_point[0]), float(raw_point[1])))
        except (TypeError, ValueError) as exc:
            raise YoloTrainingError("分割 points 中包含非数字坐标。") from exc
    return points


def _unit_coordinate(value: float, extent: int) -> float:
    if 0.0 <= value <= 1.0:
        return value
    return value / max(extent, 1)


def _normalized_box(entry: dict[str, Any], image_width: int, image_height: int) -> tuple[float, float, float, float]:
    x, y, width, height = _read_box(entry)
    x = _unit_coordinate(x, image_width)
    y = _unit_coordinate(y, image_height)
    width = _unit_coordinate(width, image_width)
    height = _unit_coordinate(height, image_height)
    center_x = max(0.0, min(1.0, x + width / 2))
    center_y = max(0.0, min(1.0, y + height / 2))
    width = max(0.0, min(1.0, width))
    height = max(0.0, min(1.0, height))
    if width <= 0 or height <= 0:
        raise YoloTrainingError("检测框归一化后没有有效面积。")
    return center_x, center_y, width, height


def _normalized_points(entry: dict[str, Any], image_width: int, image_height: int) -> list[tuple[float, float]]:
    points = _read_points(entry)
    normalized = [
        (
            max(0.0, min(1.0, _unit_coordinate(x, image_width))),
            max(0.0, min(1.0, _unit_coordinate(y, image_height))),
        )
        for x, y in points
    ]
    if len({point for point in normalized}) < 3:
        raise YoloTrainingError("分割点归一化后不足三个有效点。")
    return normalized


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception as exc:
        raise YoloTrainingError(f"无法读取图片尺寸：{path}") from exc


def _label_index(label: str, labels: tuple[str, ...]) -> int:
    try:
        return labels.index(label)
    except ValueError as exc:
        raise YoloTrainingError(f"类别“{label}”不在模型类别列表中。") from exc


def _sample_source_path(sample: dict[str, Any]) -> Path:
    source = _resolve_path(str(sample.get("media_path", "")))
    if not source.is_file():
        raise YoloTrainingError(f"训练素材不存在：{source}")
    return source


def _export_detection_or_segmentation(spec: YoloTrainingSpec) -> Path:
    dataset_dir = spec.run_dir / "dataset"
    for split in ("TRAIN", "VAL", "TEST"):
        (dataset_dir / "images" / split.lower()).mkdir(parents=True, exist_ok=True)
        (dataset_dir / "labels" / split.lower()).mkdir(parents=True, exist_ok=True)

    for sample in spec.samples:
        split = str(sample.get("split") or "TRAIN").upper()
        if split not in {"TRAIN", "VAL", "TEST"}:
            raise YoloTrainingError(f"未知训练切分：{split}")
        source = _sample_source_path(sample)
        image_width, image_height = _image_size(source)
        suffix = source.suffix.lower() or ".jpg"
        stem = f"{int(sample['dataset_item_id'])}_{_safe_component(Path(str(sample.get('original_name') or 'image')).stem)}"
        image_target = dataset_dir / "images" / split.lower() / f"{stem}{suffix}"
        label_target = dataset_dir / "labels" / split.lower() / f"{stem}.txt"
        _copy_or_link(source, image_target)

        annotation = sample.get("annotation_json") or {}
        labels_output: list[str] = []
        entries = _annotation_list(annotation, spec.task_type)
        for entry in entries:
            label = _label_from_annotation(entry)
            class_index = _label_index(label, spec.labels)
            if spec.task_type == "YOLO_DETECTION":
                center_x, center_y, width, height = _normalized_box(entry, image_width, image_height)
                labels_output.append(
                    f"{class_index} {center_x:.6f} {center_y:.6f} {width:.6f} {height:.6f}"
                )
            else:
                points = _normalized_points(entry, image_width, image_height)
                point_values = " ".join(f"{x:.6f} {y:.6f}" for x, y in points)
                labels_output.append(f"{class_index} {point_values}")
        label_target.write_text("\n".join(labels_output) + ("\n" if labels_output else ""), encoding="utf-8")

    yaml_path = dataset_dir / "data.yaml"
    path_text = dataset_dir.resolve().as_posix()
    names = "\n".join(f"  {index}: {json.dumps(label, ensure_ascii=False)}" for index, label in enumerate(spec.labels))
    yaml_path.write_text(
        "\n".join(
            [
                f"path: {json.dumps(path_text, ensure_ascii=False)}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                names,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return yaml_path


def _export_classification(spec: YoloTrainingSpec) -> Path:
    dataset_dir = spec.run_dir / "dataset" / "classification"
    for sample in spec.samples:
        split = str(sample.get("split") or "TRAIN").upper()
        if split not in {"TRAIN", "VAL", "TEST"}:
            raise YoloTrainingError(f"未知训练切分：{split}")
        source = _sample_source_path(sample)
        annotation = sample.get("annotation_json") or {}
        label = _label_from_annotation(annotation)
        _label_index(label, spec.labels)
        suffix = source.suffix.lower() or ".jpg"
        target = (
            dataset_dir
            / split.lower()
            / _safe_component(label)
            / f"{int(sample['dataset_item_id'])}_{_safe_component(Path(str(sample.get('original_name') or 'image')).stem)}{suffix}"
        )
        _copy_or_link(source, target)
    return dataset_dir


def export_yolo_dataset(spec: YoloTrainingSpec) -> Path:
    """Export the frozen manifest to a YOLO run directory and return its data path."""

    validation_items = [
        _SnapshotItem(sample)
        for sample in spec.samples
    ]
    validate_training_dataset(
        task_type=spec.task_type,
        labels=list(spec.labels),
        dataset_annotation_type=required_annotation_type(spec.task_type),
        items=validation_items,
    )
    if spec.task_type == "YOLO_CLASSIFICATION":
        return _export_classification(spec)
    return _export_detection_or_segmentation(spec)


@dataclass(frozen=True)
class _SnapshotItem:
    data: dict[str, Any]

    @property
    def id(self) -> int:
        return int(self.data["dataset_item_id"])

    @property
    def media_path(self) -> str:
        return str(self.data["media_path"])

    @property
    def original_name(self) -> str:
        return str(self.data.get("original_name") or f"item-{self.id}")

    @property
    def annotation_status(self) -> str:
        return str(self.data.get("annotation_status") or "")

    @property
    def annotation_json(self) -> dict[str, Any]:
        return dict(self.data.get("annotation_json") or {})

    @property
    def is_deleted(self) -> bool:
        return False


def _numeric_metrics_from_csv(run_dir: Path) -> dict[str, Any]:
    results_csv = run_dir / "results.csv"
    if not results_csv.is_file():
        return {}
    try:
        with results_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {}
    if not rows:
        return {}
    metrics: dict[str, Any] = {}
    for key, value in rows[-1].items():
        if value is None:
            continue
        cleaned_key = str(key).strip()
        cleaned_value = str(value).strip()
        try:
            metrics[cleaned_key] = float(cleaned_value)
        except ValueError:
            metrics[cleaned_key] = cleaned_value
    return metrics


def _artifact_paths(run_dir: Path) -> dict[str, str]:
    candidates = {
        "run_dir": run_dir,
        "best_weights": run_dir / "weights" / "best.pt",
        "last_weights": run_dir / "weights" / "last.pt",
        "metrics_csv": run_dir / "results.csv",
        "training_curve": run_dir / "results.png",
        "confusion_matrix": run_dir / "confusion_matrix.png",
        "labels_preview": run_dir / "labels.jpg",
    }
    return {key: str(path) for key, path in candidates.items() if path.exists()}


def run_yolo_training(spec: YoloTrainingSpec) -> dict[str, Any]:
    """Run one local Ultralytics training job and return persisted artifact paths."""

    spec.run_dir.mkdir(parents=True, exist_ok=True)
    try:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise YoloTrainingError(
                "未安装 ultralytics。请在平台运行环境执行依赖安装后重新提交训练任务。"
            ) from exc

        dataset_path = export_yolo_dataset(spec)
        blocked_parameters = {
            "data",
            "model",
            "project",
            "name",
            "exist_ok",
            "epochs",
            "imgsz",
            "batch",
            "device",
        }
        extra_params = {
            str(key): value
            for key, value in spec.extra_params.items()
            if str(key) not in blocked_parameters
        }
        train_kwargs: dict[str, Any] = {
            "data": str(dataset_path),
            "epochs": spec.epochs,
            "imgsz": spec.image_size,
            "project": str(spec.run_dir.parent),
            "name": spec.run_dir.name,
            "exist_ok": True,
            "plots": True,
            "workers": settings.training_default_workers,
            **extra_params,
        }
        if spec.batch_size is not None:
            train_kwargs["batch"] = spec.batch_size
        if spec.device is not None:
            train_kwargs["device"] = spec.device

        with spec.log_path.open("a", encoding="utf-8") as log:
            resolved_base_model = _resolve_base_model(spec.base_model)
            log.write(
                f"开始 YOLO 训练：task={spec.task_type}, base={resolved_base_model}, "
                f"dataset={dataset_path}, epochs={spec.epochs}\n"
            )
            model = YOLO(resolved_base_model)
            train_result = model.train(**train_kwargs)
            save_dir = Path(getattr(train_result, "save_dir", spec.run_dir))
            if not save_dir.is_absolute():
                save_dir = spec.run_dir
            best_weights = save_dir / "weights" / "best.pt"
            if not best_weights.is_file():
                raise YoloTrainingError("训练结束但未产出 weights/best.pt，请检查训练日志。")
            metrics = _numeric_metrics_from_csv(save_dir)
            result_metrics = getattr(train_result, "results_dict", None)
            if isinstance(result_metrics, dict):
                metrics.update(
                    {
                        str(key): float(value) if isinstance(value, (int, float)) else value
                        for key, value in result_metrics.items()
                    }
                )
            log.write(f"训练完成，最佳权重：{best_weights}\n")
        artifacts = _artifact_paths(save_dir)
        return {
            "weights_path": str(best_weights),
            "metrics_json": metrics,
            "artifact_paths_json": artifacts,
            "run_dir": str(save_dir),
            "log_path": str(spec.log_path),
            "base_model_path": resolved_base_model,
        }
    except YoloTrainingError:
        raise
    except Exception as exc:
        with spec.log_path.open("a", encoding="utf-8") as log:
            log.write("\n训练异常：\n")
            log.write(traceback.format_exc())
        raise YoloTrainingError(f"YOLO 训练失败：{exc}") from exc
