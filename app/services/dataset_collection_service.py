"""Safe production ROI collection for scene-linked datasets.

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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.intelligence import Dataset, DatasetItem, InspectionScenarioVersion


logger = logging.getLogger(__name__)


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


def try_collect_roi_for_matching_datasets(**kwargs: object) -> list[int]:
    """Non-blocking wrapper for the production execution path."""

    try:
        return collect_roi_for_matching_datasets(**kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Automatic ROI collection failed; production execution continues")
        return []
