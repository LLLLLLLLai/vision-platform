from pathlib import Path

from sqlalchemy import inspect, text

from app.core.config import PROJECT_ROOT, settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.world import ModelRegistry


def _upgrade_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "recipes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("recipes")}
    additions = {
        "line_code": "VARCHAR(100)",
        "material_code": "VARCHAR(100)",
        "process_code": "VARCHAR(100)",
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


def init_database() -> None:
    for directory in (
        "data",
        "uploads",
        "embeddings",
        "detection_results",
        "logs",
    ):
        Path(PROJECT_ROOT / directory).mkdir(parents=True, exist_ok=True)

    _upgrade_sqlite_schema()
    Base.metadata.create_all(bind=engine)
    _seed_model_registry()


def _seed_model_registry() -> None:
    defaults = (
        {
            "code": "DINOV2_REFERENCE",
            "name": "DINOv2 参考图相似度",
            "capability": "REFERENCE_SIMILARITY",
            "runtime": "TRANSFORMERS",
            "service_url": settings.dinov2_service_url,
            "config_json": {"role": "PRIMARY", "batch_supported": True},
        },
        {
            "code": "QWEN3_VL_INVENTORY",
            "name": "Qwen3-VL 物体清单解析",
            "capability": "SCENE_INVENTORY",
            "runtime": "TRANSFORMERS_4BIT",
            "service_url": settings.qwen_vl_service_url,
            "config_json": {"role": "DISCOVERY", "coordinates_enabled": False},
        },
        {
            "code": "GROUNDING_DINO_LOCALIZER",
            "name": "Grounding DINO 开放词汇定位",
            "capability": "OBJECT_LOCALIZATION",
            "runtime": "TRANSFORMERS_FP16",
            "service_url": settings.grounding_service_url,
            "config_json": {"role": "DISCOVERY", "box_threshold": 0.22},
        },
        {
            "code": "QWEN3_VL_REVIEW",
            "name": "Qwen3-VL 低置信度复核",
            "capability": "VLM_JUDGEMENT",
            "runtime": "TRANSFORMERS_4BIT",
            "service_url": settings.qwen_vl_service_url,
            "config_json": {
                "role": "FALLBACK_ONLY",
                "uncertain_result": "NG",
                "max_images": 1,
            },
        },
    )
    with SessionLocal() as database:
        existing_codes = {
            code
            for (code,) in database.query(ModelRegistry.code).all()
        }
        for values in defaults:
            if values["code"] not in existing_codes:
                database.add(ModelRegistry(**values))
        database.commit()
