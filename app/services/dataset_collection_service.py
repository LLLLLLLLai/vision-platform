"""Safe production image collection for datasets linked to recipes or scenes.

This service deliberately collects *inputs*, not truth.  Every collected ROI
is marked ``PENDING`` so it can later be annotated for training or evaluation.
Collection failures are intentionally non-blocking for the production detect
path; a missing destination directory must never turn a product into NG.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.intelligence import Dataset, DatasetItem, InspectionScenarioVersion


logger = logging.getLogger(__name__)


def is_collection_only(version: InspectionScenarioVersion) -> bool:
    return (version.definition_json or {}).get("run_mode", "DETECT") == "COLLECT_ONLY"


def scene_collection_targets(database: Session, version: InspectionScenarioVersion) -> list[Dataset]:
    return list(database.scalars(
        select(Dataset).join(
            InspectionScenarioVersion,
            Dataset.collection_scenario_version_id == InspectionScenarioVersion.id,
        ).where(
            Dataset.is_deleted.is_(False), Dataset.collection_scope == "ROI",
            Dataset.media_type == "IMAGE",
            InspectionScenarioVersion.scenario_id == version.scenario_id,
        ).order_by(Dataset.id)
    ).all())


def collect_scene_image(
    database: Session, version: InspectionScenarioVersion, *, execution_id: int,
    roi_id: int | None, source: str, image_path: str, context: dict,
) -> dict:
    source_path = Path(image_path)
    with Image.open(source_path) as image:
        image.verify()
    payload = source_path.read_bytes()
    content_hash = hashlib.sha256(payload).hexdigest()
    preview = source not in {"PRODUCTION", "SCENE_API"}
    outcomes = []
    active_count = 0
    for candidate in scene_collection_targets(database, version):
        outcome = {"dataset_id": candidate.id, "dataset_name": candidate.name}
        outcomes.append(outcome)
        target_path = None
        temporary_path = None
        try:
            with database.begin_nested():
                dataset = database.scalar(select(Dataset).where(
                    Dataset.id == candidate.id,
                ).with_for_update().execution_options(populate_existing=True))
                configured_version = database.get(InspectionScenarioVersion, dataset.collection_scenario_version_id) if dataset.collection_scenario_version_id else None
                if (not dataset.enabled or not dataset.auto_collect_enabled or dataset.is_deleted
                        or dataset.collection_scope != "ROI" or dataset.media_type != "IMAGE"
                        or configured_version is None or configured_version.scenario_id != version.scenario_id):
                    outcome.update(status="DISABLED", message="数据集自动采集未启用")
                    continue
                active_count += 1
                duplicate = database.scalar(select(DatasetItem.id).where(
                    DatasetItem.dataset_id == dataset.id,
                    DatasetItem.content_hash == content_hash,
                    DatasetItem.is_deleted.is_(False),
                ))
                if duplicate is not None:
                    outcome.update(status="DUPLICATE", item_id=duplicate, message="重复图片，未新增")
                    continue
                count = database.scalar(select(func.count(DatasetItem.id)).where(
                    DatasetItem.dataset_id == dataset.id, DatasetItem.source == "AUTO_ROI",
                    DatasetItem.is_deleted.is_(False),
                )) or 0
                if count >= max(1, dataset.auto_collect_limit):
                    outcome.update(status="LIMIT_REACHED", message="已达到自动采集上限，未新增")
                    continue
                if preview:
                    outcome.update(status="PREVIEW", message="采集检查通过；测试图片不写入生产数据集")
                    continue
                target_dir = (PROJECT_ROOT / "uploads" / "datasets" / dataset.code / "auto").resolve()
                allowed_root = (PROJECT_ROOT / "uploads" / "datasets").resolve()
                if not target_dir.is_relative_to(allowed_root):
                    raise ValueError("数据集存储路径不合法")
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / f"collection_{execution_id}_{uuid4().hex}{source_path.suffix.lower()}"
                temporary_path = target_path.with_suffix(".tmp")
                temporary_path.write_bytes(payload)
                temporary_path.replace(target_path)
                item = DatasetItem(
                    dataset_id=dataset.id, media_path=str(target_path),
                    original_name=source_path.name, media_type="IMAGE", source="AUTO_ROI",
                    content_hash=content_hash, annotation_status="PENDING",
                    annotation_json={"collection": {
                        "run_mode": "COLLECT_ONLY", "scenario_version_id": version.id,
                        "configured_scenario_version_id": dataset.collection_scenario_version_id,
                        "execution_id": execution_id, "roi_id": roi_id,
                        "source": source, "source_image_path": context.get("source_image_path"),
                        "recipe_code": context.get("recipe_code"), "recipe_version": context.get("recipe_version"),
                        "roi_code": context.get("roi_code"), "roi_bbox": context.get("roi_bbox"),
                        "sn": context.get("sn"),
                    }},
                )
                database.add(item)
                dataset.revision += 1
                database.flush()
                outcome.update(status="COLLECTED", item_id=item.id, message="已采集，待标注")
        except Exception as exc:
            for path in (temporary_path, target_path):
                if path is not None:
                    path.unlink(missing_ok=True)
            outcome.update(status="FAILED", message=f"保存素材失败：{exc}")
            logger.exception("Collection-only execution %s failed for dataset %s", execution_id, candidate.id)
    failed = not active_count or any(item["status"] == "FAILED" for item in outcomes)
    return {
        "status": "FAILED" if failed else "PREVIEW" if preview else "COMPLETED",
        "message": "未关联已启用自动采集的数据集" if not active_count else (
            "部分素材保存失败，请查看数据集处理明细" if failed else
            "仅采集图片，未执行质量检测，按配置返回 NG"
        ),
        "datasets": outcomes,
        "collected_item_ids": [item["item_id"] for item in outcomes if item["status"] == "COLLECTED"],
    }


def collect_roi_for_matching_datasets(
    database: Session,
    *,
    scenario_version_id: int,
    execution_id: int,
    roi_id: int | None,
    source: str,
    roi_image_path: str,
) -> list[int]:
    """Copy a production ROI into eligible scene-linked datasets.

    The caller owns the database transaction.  Only ``flush`` is used here so
    the ROI execution and the collection item remain atomic when possible.
    """

    if source != "PRODUCTION" or roi_id is None:
        return []
    source_path = Path(roi_image_path)
    if not source_path.is_file():
        return []
    # A dataset is configured against a scenario's published version in the UI.
    # Publishing a newer version must not silently stop collection for every
    # recipe that uses the same logical scenario.  Match by parent scenario as
    # well as the exact version, while retaining the configured version in the
    # collection metadata for traceability.
    executed_version = database.get(InspectionScenarioVersion, scenario_version_id)
    if executed_version is None:
        return []
    bound_version = InspectionScenarioVersion.__table__.alias("collection_bound_version")
    datasets = database.scalars(
        select(Dataset)
        .join(
            bound_version,
            Dataset.collection_scenario_version_id == bound_version.c.id,
        )
        .where(
            Dataset.is_deleted.is_(False),
            Dataset.enabled.is_(True),
            Dataset.auto_collect_enabled.is_(True),
            Dataset.collection_scope == "ROI",
            Dataset.media_type == "IMAGE",
            bound_version.c.scenario_id == executed_version.scenario_id,
        )
    ).all()
    if not datasets:
        return []

    payload = source_path.read_bytes()
    content_hash = hashlib.sha256(payload).hexdigest()
    collected_ids: list[int] = []
    for dataset in datasets:
        collected_count = database.scalar(
            select(func.count(DatasetItem.id)).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.source == "AUTO_ROI",
                DatasetItem.is_deleted.is_(False),
            )
        ) or 0
        if collected_count >= max(1, dataset.auto_collect_limit):
            continue
        duplicate = database.scalar(
            select(DatasetItem.id).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.content_hash == content_hash,
                DatasetItem.is_deleted.is_(False),
            )
        )
        if duplicate is not None:
            continue

        target_dir = Path(PROJECT_ROOT / "uploads" / "datasets" / dataset.code / "auto")
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_path.suffix.lower() or ".jpg"
        target_path = target_dir / f"roi_{roi_id}_execution_{execution_id}{suffix}"
        if target_path.exists():
            target_path = target_dir / f"roi_{roi_id}_{execution_id}_{content_hash[:10]}{suffix}"
        shutil.copy2(source_path, target_path)
        item = DatasetItem(
            dataset_id=dataset.id,
            media_path=str(target_path),
            original_name=target_path.name,
            media_type="IMAGE",
            annotation_status="PENDING",
            annotation_json={
                "collection": {
                    "scenario_version_id": scenario_version_id,
                    "configured_scenario_version_id": dataset.collection_scenario_version_id,
                    "roi_id": roi_id,
                    "execution_id": execution_id,
                }
            },
            source="AUTO_ROI",
            content_hash=content_hash,
        )
        database.add(item)
        dataset.revision += 1
        database.flush()
        collected_ids.append(item.id)
    return collected_ids


def collect_recipe_roi_for_matching_datasets(
    database: Session,
    *,
    recipe_id: int,
    detection_task_id: int,
    image_index: int,
    roi_id: int,
    source: str,
    roi_image_path: str,
    source_image_path: str | None = None,
) -> list[int]:
    """Collect production ROI crops by recipe, even when no scene is bound."""

    if source != "PRODUCTION":
        return []
    source_path = Path(roi_image_path)
    if not source_path.is_file():
        return []
    datasets = database.scalars(
        select(Dataset).where(
            Dataset.is_deleted.is_(False),
            Dataset.enabled.is_(True),
            Dataset.auto_collect_enabled.is_(True),
            Dataset.collection_scope == "ROI_RECIPE",
            Dataset.collection_recipe_id == recipe_id,
            Dataset.media_type == "IMAGE",
        )
    ).all()
    if not datasets:
        return []

    payload = source_path.read_bytes()
    content_hash = hashlib.sha256(payload).hexdigest()
    collected_ids: list[int] = []
    for dataset in datasets:
        collected_count = database.scalar(
            select(func.count(DatasetItem.id)).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.source == "AUTO_ROI_RECIPE",
                DatasetItem.is_deleted.is_(False),
            )
        ) or 0
        if collected_count >= max(1, dataset.auto_collect_limit):
            continue
        duplicate_candidates = database.scalars(
            select(DatasetItem).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.content_hash == content_hash,
                DatasetItem.source == "AUTO_ROI_RECIPE",
                DatasetItem.is_deleted.is_(False),
            )
        ).all()
        if any(
            int(((item.annotation_json or {}).get("collection") or {}).get("roi_id") or 0) == roi_id
            for item in duplicate_candidates
        ):
            continue

        target_dir = Path(PROJECT_ROOT / "uploads" / "datasets" / dataset.code / "auto" / "roi")
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_path.suffix.lower() or ".jpg"
        target_path = target_dir / (
            f"recipe_{recipe_id}_roi_{roi_id}_task_{detection_task_id}_"
            f"image_{image_index}_{content_hash[:10]}{suffix}"
        )
        shutil.copy2(source_path, target_path)
        item = DatasetItem(
            dataset_id=dataset.id,
            media_path=str(target_path),
            original_name=target_path.name,
            media_type="IMAGE",
            annotation_status="PENDING",
            annotation_json={
                "collection": {
                    "scope": "ROI_RECIPE",
                    "recipe_id": recipe_id,
                    "detection_task_id": detection_task_id,
                    "image_index": image_index,
                    "roi_id": roi_id,
                    "source_image_path": source_image_path,
                }
            },
            source="AUTO_ROI_RECIPE",
            content_hash=content_hash,
        )
        database.add(item)
        dataset.revision += 1
        database.flush()
        collected_ids.append(item.id)
    return collected_ids


def collect_original_image_for_matching_datasets(
    database: Session,
    *,
    recipe_id: int,
    detection_task_id: int,
    image_index: int,
    source: str,
    image_path: str,
    source_image_path: str | None = None,
    inspection_result: str | None = None,
) -> list[int]:
    if source != "PRODUCTION":
        return []
    source_path = Path(image_path)
    if not source_path.is_file():
        return []
    datasets = database.scalars(
        select(Dataset).where(
            Dataset.is_deleted.is_(False),
            Dataset.enabled.is_(True),
            Dataset.auto_collect_enabled.is_(True),
            Dataset.collection_scope == "ORIGINAL",
            Dataset.collection_recipe_id == recipe_id,
            Dataset.media_type == "IMAGE",
        )
    ).all()
    if not datasets:
        return []

    payload = source_path.read_bytes()
    content_hash = hashlib.sha256(payload).hexdigest()
    filename = str(source_image_path or image_path).replace("\\", "/").rsplit("/", 1)[-1]
    collected_ids: list[int] = []
    for dataset in datasets:
        collected_count = database.scalar(
            select(func.count(DatasetItem.id)).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.source == "AUTO_ORIGINAL",
                DatasetItem.is_deleted.is_(False),
            )
        ) or 0
        if collected_count >= max(1, dataset.auto_collect_limit):
            continue
        duplicate = database.scalar(
            select(DatasetItem.id).where(
                DatasetItem.dataset_id == dataset.id,
                DatasetItem.content_hash == content_hash,
                DatasetItem.is_deleted.is_(False),
            )
        )
        if duplicate is not None:
            continue

        target_dir = Path(PROJECT_ROOT / "uploads" / "datasets" / dataset.code / "auto" / "original")
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_path.suffix.lower() or ".jpg"
        target_path = target_dir / f"image_{image_index}_task_{detection_task_id}{suffix}"
        if target_path.exists():
            target_path = target_dir / f"image_{image_index}_{detection_task_id}_{content_hash[:10]}{suffix}"
        shutil.copy2(source_path, target_path)
        item = DatasetItem(
            dataset_id=dataset.id,
            media_path=str(target_path),
            original_name=filename or target_path.name,
            media_type="IMAGE",
            annotation_status="PENDING",
            annotation_json={
                "collection": {
                    "scope": "ORIGINAL",
                    "recipe_id": recipe_id,
                    "detection_task_id": detection_task_id,
                    "image_index": image_index,
                    "source_image_path": source_image_path,
                    "inspection_result": inspection_result,
                }
            },
            source="AUTO_ORIGINAL",
            content_hash=content_hash,
        )
        database.add(item)
        dataset.revision += 1
        database.flush()
        collected_ids.append(item.id)
    return collected_ids


def try_collect_roi_for_matching_datasets(**kwargs: object) -> list[int]:
    """Non-blocking wrapper for the production execution path."""

    try:
        return collect_roi_for_matching_datasets(**kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Automatic ROI collection failed; production execution continues")
        return []


def try_collect_recipe_roi_for_matching_datasets(**kwargs: object) -> list[int]:
    try:
        return collect_recipe_roi_for_matching_datasets(**kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Automatic recipe ROI collection failed; production execution continues")
        return []


def try_collect_original_image_for_matching_datasets(**kwargs: object) -> list[int]:
    try:
        return collect_original_image_for_matching_datasets(**kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Automatic original-image collection failed; production execution continues")
        return []
