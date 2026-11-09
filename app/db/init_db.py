from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Index, inspect, select, text
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.recipe import RecipeFeatureAnchor, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceObjectType
from app.models.intelligence import Dataset, DatasetItem


DEFAULT_REFERENCE_OBJECT_TYPES = (
    ("FUSE", "保险丝"),
    ("SCREW", "螺丝"),
    ("CONNECTOR", "连接器"),
    ("HARNESS", "线束"),
    ("PCBA", "PCBA"),
    ("BUSBAR", "铜排"),
    ("LABEL", "标签"),
    ("OBJECT", "其他对象"),
)


def _upgrade_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "recipes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("recipes")}
    additions = {
        "project_name": "VARCHAR(200)",
        "line_code": "VARCHAR(100)",
        "material_code": "VARCHAR(100)",
        "process_code": "VARCHAR(100)",
        "recipe_family_code": "VARCHAR(100) NOT NULL DEFAULT ''",
        "version_no": "INTEGER NOT NULL DEFAULT 0",
        "source_recipe_id": "INTEGER",
        "execution_mode": "VARCHAR(30) NOT NULL DEFAULT 'INSPECT'",
        "skip_reason": "VARCHAR(500)",
    }
    with engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE recipes ADD COLUMN {column_name} {column_type}"
                    )
                )
        connection.execute(
            text(
                """
                UPDATE recipes
                SET material_code = COALESCE(
                    material_code,
                    (SELECT code FROM products WHERE products.id = recipes.product_id)
                ),
                    line_code = COALESCE(
                    line_code,
                    (SELECT line_code FROM stations WHERE stations.id = recipes.station_id)
                ),
                    process_code = COALESCE(
                    process_code,
                    (SELECT process_code FROM stations WHERE stations.id = recipes.station_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE recipes
                SET recipe_family_code = CASE
                    WHEN recipe_family_code IS NULL OR recipe_family_code = '' THEN code
                    ELSE recipe_family_code
                END
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE recipes
                SET version_no = CASE
                    WHEN version_no IS NULL OR version_no = 0
                    THEN CASE WHEN status = 'PUBLISHED' THEN 1 ELSE 0 END
                    ELSE version_no
                END
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE recipes
                SET execution_mode = CASE
                    WHEN execution_mode IS NULL OR execution_mode = '' THEN 'INSPECT'
                    ELSE UPPER(execution_mode)
                END
                """
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_recipes_recipe_family_code "
                "ON recipes (recipe_family_code)"
            )
        )

    inspector = inspect(engine)
    if "datasets" in inspector.get_table_names():
        dataset_columns = {
            column["name"] for column in inspector.get_columns("datasets")
        }
        dataset_additions = {
            "label_schema_json": "JSON NOT NULL DEFAULT '[]'",
            "collection_scenario_version_id": "INTEGER",
            "collection_scope": "VARCHAR(30) NOT NULL DEFAULT 'ROI'",
            "collection_recipe_id": "INTEGER",
            "auto_collect_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "auto_collect_limit": "INTEGER NOT NULL DEFAULT 1000",
        }
        with engine.begin() as connection:
            for column_name, column_type in dataset_additions.items():
                if column_name not in dataset_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE datasets "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_datasets_collection_scenario_version_id "
                    "ON datasets (collection_scenario_version_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_datasets_collection_recipe_id "
                    "ON datasets (collection_recipe_id)"
                )
            )

    inspector = inspect(engine)
    if "reference_groups" in inspector.get_table_names():
        group_columns = {
            column["name"] for column in inspector.get_columns("reference_groups")
        }
        group_additions = {
            "embedding_set_version": "INTEGER NOT NULL DEFAULT 0",
            "embedding_matrix_path": "VARCHAR(500)",
            "embedding_manifest_path": "VARCHAR(500)",
            "embedding_count": "INTEGER NOT NULL DEFAULT 0",
        }
        with engine.begin() as connection:
            for column_name, column_type in group_additions.items():
                if column_name not in group_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE reference_groups "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )

    inspector = inspect(engine)
    if "reference_images" in inspector.get_table_names():
        image_columns = {
            column["name"] for column in inspector.get_columns("reference_images")
        }
        if "embedding_index" not in image_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE reference_images "
                        "ADD COLUMN embedding_index INTEGER"
                    )
                )

    inspector = inspect(engine)
    if "regions_of_interest" not in inspector.get_table_names():
        return
    roi_columns = {
        column["name"] for column in inspector.get_columns("regions_of_interest")
    }
    with engine.begin() as connection:
        if "scene_object_id" not in roi_columns:
            connection.execute(
                text(
                    "ALTER TABLE regions_of_interest "
                    "ADD COLUMN scene_object_id INTEGER"
                )
            )
        if "alignment_anchor" not in roi_columns:
            connection.execute(
                text(
                    "ALTER TABLE regions_of_interest "
                    "ADD COLUMN alignment_anchor BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_regions_of_interest_scene_object_id "
                "ON regions_of_interest (scene_object_id)"
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_recipe_business_key
                ON recipes (
                    line_code,
                    material_code,
                    process_code,
                    camera_code,
                    capture_index,
                    status
                )
                """
            )
        )


def _upgrade_dataset_collection_schema() -> None:
    inspector = inspect(engine)
    if "datasets" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("datasets")}
    additions = {
        "collection_scope": "VARCHAR(30) NOT NULL DEFAULT 'ROI'",
        "collection_recipe_id": "INTEGER",
    }
    with engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE datasets "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
    Index(
        "ix_datasets_collection_recipe_id",
        Dataset.__table__.c.collection_recipe_id,
    ).create(bind=engine, checkfirst=True)


def _upgrade_report_selection_schema() -> None:
    inspector = inspect(engine)
    additions = {
        "detection_tasks": {"report_excluded": "BOOLEAN NOT NULL DEFAULT 0"},
        "detection_api_calls": {
            "report_excluded": "BOOLEAN NOT NULL DEFAULT 0",
            "report_note": "VARCHAR(500)",
            "report_updated_at": "DATETIME",
        },
    }
    for table, fields in additions.items():
        if table not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        with engine.begin() as connection:
            for name, definition in fields.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def init_database() -> None:
    for directory in (
        "data",
        "uploads",
        "embeddings",
        "detection_results",
        "data/inspection_cache",
        "logs",
    ):
        Path(PROJECT_ROOT / directory).mkdir(parents=True, exist_ok=True)
    embedding_root = Path(settings.embedding_storage_root).expanduser()
    if not embedding_root.is_absolute():
        embedding_root = PROJECT_ROOT / embedding_root
    embedding_root.mkdir(parents=True, exist_ok=True)

    _upgrade_sqlite_schema()
    _upgrade_dataset_collection_schema()
    _upgrade_report_selection_schema()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as database:
        migrate_ai_annotations(database)
    _migrate_legacy_alignment_anchors()
    _seed_reference_object_types()


def migrate_ai_annotations(database: Session) -> int:
    from fastapi import HTTPException
    from app.api.routes.datasets import (
        _annotation_labels,
        _merge_dataset_labels,
        _resolve_dataset_classification_label,
        _validate_training_annotation,
    )

    rows = database.execute(
        select(DatasetItem, Dataset).join(Dataset, Dataset.id == DatasetItem.dataset_id).where(
            DatasetItem.annotation_status == "AI_SUGGESTED",
            DatasetItem.is_deleted.is_(False),
            DatasetItem.media_type == "IMAGE",
            Dataset.is_deleted.is_(False),
            Dataset.purpose == "TRAIN",
        )
    )
    migrated = 0
    for item, dataset in rows:
        raw_annotation = item.annotation_json
        annotation = dict(raw_annotation) if isinstance(raw_annotation, dict) else {"_legacy_annotation": raw_annotation}
        raw_metadata = annotation.get("_meta")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        try:
            _validate_training_annotation(dataset, annotation)
            if not _annotation_labels(dataset, annotation):
                raise ValueError("历史 AI 标注没有可用目标，请重新标注。")
            if dataset.annotation_type == "CLASSIFICATION":
                annotation["label"] = _resolve_dataset_classification_label(dataset, annotation["label"])
            _merge_dataset_labels(dataset, _annotation_labels(dataset, annotation))
        except (HTTPException, ValueError, TypeError) as exc:
            item.annotation_status = "PENDING"
            metadata["migration_error"] = str(getattr(exc, "detail", exc))
        else:
            item.annotation_status = "LABELED"
            metadata.update({
                "source": "AI_PREANNOTATION",
                "auto_accepted": True,
                "accepted_at": datetime.now(UTC).isoformat(),
            })
            metadata.pop("ai_candidate_label", None)
            migrated += 1
        metadata["review_required"] = False
        annotation["_meta"] = metadata
        item.annotation_json = annotation
        dataset.revision += 1
    database.commit()
    return migrated


def _seed_reference_object_types() -> None:
    with SessionLocal() as database:
        defaults = dict(DEFAULT_REFERENCE_OBJECT_TYPES)
        existing_codes = {
            code
            for (code,) in database.execute(
                select(ReferenceObjectType.code)
            ).all()
        }
        legacy_codes = {
            str(code).strip().upper()
            for (code,) in database.execute(
                select(RegionOfInterest.object_type).where(
                    RegionOfInterest.object_type.is_not(None)
                )
            ).all()
            if str(code).strip()
        }
        legacy_codes.update(
            str(code).strip().upper()
            for (code,) in database.execute(
                select(ReferenceGroup.object_type).where(
                    ReferenceGroup.object_type.is_not(None)
                )
            ).all()
            if str(code).strip()
        )
        for code in sorted(set(defaults) | legacy_codes):
            if code in existing_codes:
                continue
            database.add(
                ReferenceObjectType(
                    code=code,
                    name=defaults.get(code, code),
                    description="视觉标准库统一物体类型",
                )
            )
        database.commit()


def _migrate_legacy_alignment_anchors() -> None:
    """Keep existing recipes usable after moving anchors out of inspection ROIs."""

    with SessionLocal() as database:
        legacy_anchors = database.scalars(
            select(RegionOfInterest).where(RegionOfInterest.alignment_anchor.is_(True))
        ).all()
        changed = False
        for roi in legacy_anchors:
            existing = database.scalar(
                select(RecipeFeatureAnchor).where(
                    RecipeFeatureAnchor.recipe_id == roi.recipe_id
                )
            )
            if existing is None:
                database.add(
                    RecipeFeatureAnchor(
                        recipe_id=roi.recipe_id,
                        code="FEATURE_ANCHOR",
                        name="由旧 ROI 迁移的图像定位特征点",
                        x_ratio=roi.x_ratio,
                        y_ratio=roi.y_ratio,
                        width_ratio=roi.width_ratio,
                        height_ratio=roi.height_ratio,
                        padding=roi.padding,
                        enabled=roi.enabled,
                    )
                )
            roi.alignment_anchor = False
            changed = True
        if changed:
            database.commit()
