from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.intelligence import (
    Dataset,
    DatasetItem,
    InspectionScenario,
    InspectionScenarioVersion,
)
from app.models.recipe import Recipe
from app.models.system import Product, Station
from app.services import dataset_collection_service as collection_module


class DatasetCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.temporary_directory = TemporaryDirectory()
        self.temporary_root = Path(self.temporary_directory.name)
        self.original_project_root = collection_module.PROJECT_ROOT
        collection_module.PROJECT_ROOT = self.temporary_root

        scenario = InspectionScenario(
            code="SCENE_HARNESS",
            name="线束检测场景",
            category="HARNESS",
            mode="VLM_DIRECT",
        )
        self.database.add(scenario)
        self.database.flush()
        self.scenario_version = InspectionScenarioVersion(
            scenario_id=scenario.id,
            version="1.0",
            status="PUBLISHED",
        )
        self.database.add(self.scenario_version)
        self.database.flush()
        self.dataset = Dataset(
            code="HARNESS_AUTO",
            name="线束自动采集",
            purpose="TRAIN",
            media_type="IMAGE",
            annotation_type="DETECTION",
            collection_scenario_version_id=self.scenario_version.id,
            collection_scope="ROI",
            auto_collect_enabled=True,
            auto_collect_limit=1,
            label_schema_json=["harness", "connector"],
        )
        product = Product(code="MAT_HARNESS", name="线束物料")
        station = Station(code="AS01", name="线束工序", line_code="L1", process_code="AS01")
        self.database.add_all((self.dataset, product, station))
        self.database.flush()
        self.recipe = Recipe(
            code="L1_MAT_HARNESS_AS01_CAMERA1_P01",
            recipe_family_code="L1_MAT_HARNESS_AS01_CAMERA1_P01",
            name="线束相机一配方",
            version="V1",
            status="PUBLISHED",
            product_id=product.id,
            station_id=station.id,
            line_code="L1",
            material_code="MAT_HARNESS",
            process_code="AS01",
            camera_code="CAMERA1",
            capture_index=1,
        )
        self.database.add(self.recipe)
        self.database.flush()
        self.original_dataset = Dataset(
            code="HARNESS_CAMERA_AUTO",
            name="线束相机原图自动采集",
            purpose="TRAIN",
            media_type="IMAGE",
            annotation_type="DETECTION",
            collection_scope="ORIGINAL",
            collection_recipe_id=self.recipe.id,
            auto_collect_enabled=True,
            auto_collect_limit=2,
            label_schema_json=["harness"],
        )
        self.database.add(self.original_dataset)
        self.database.commit()
        self.roi_path = self.temporary_root / "roi.png"
        Image.new("RGB", (48, 32), "orange").save(self.roi_path)
        self.original_path = self.temporary_root / "camera1.png"
        Image.new("RGB", (160, 100), "blue").save(self.original_path)

    def tearDown(self) -> None:
        collection_module.PROJECT_ROOT = self.original_project_root
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_collects_only_production_roi_as_pending_and_deduplicates(self) -> None:
        collected = collection_module.collect_roi_for_matching_datasets(
            self.database,
            scenario_version_id=self.scenario_version.id,
            execution_id=101,
            roi_id=9,
            source="PRODUCTION",
            roi_image_path=str(self.roi_path),
        )
        self.database.commit()

        self.assertEqual(len(collected), 1)
        item = self.database.get(DatasetItem, collected[0])
        self.assertEqual(item.source, "AUTO_ROI")
        self.assertEqual(item.annotation_status, "PENDING")
        self.assertIsNone(item.ground_truth)
        self.assertTrue(Path(item.media_path).is_file())
        self.assertEqual(item.annotation_json["collection"]["roi_id"], 9)

        duplicated = collection_module.collect_roi_for_matching_datasets(
            self.database,
            scenario_version_id=self.scenario_version.id,
            execution_id=102,
            roi_id=9,
            source="PRODUCTION",
            roi_image_path=str(self.roi_path),
        )
        non_production = collection_module.collect_roi_for_matching_datasets(
            self.database,
            scenario_version_id=self.scenario_version.id,
            execution_id=103,
            roi_id=9,
            source="TEST",
            roi_image_path=str(self.roi_path),
        )
        self.assertEqual(duplicated, [])
        self.assertEqual(non_production, [])
        self.assertEqual(
            len(
                self.database.scalars(
                    select(DatasetItem).where(DatasetItem.dataset_id == self.dataset.id)
                ).all()
            ),
            1,
        )

    def test_collection_follows_the_logical_scene_after_a_new_version_is_published(self) -> None:
        """Dataset collection must survive normal scene version publication."""

        scene = self.database.get(InspectionScenario, self.scenario_version.scenario_id)
        self.scenario_version.status = "ARCHIVED"
        next_version = InspectionScenarioVersion(
            scenario_id=scene.id,
            version="1.1",
            status="PUBLISHED",
        )
        self.dataset.auto_collect_limit = 3
        self.database.add(next_version)
        self.database.commit()

        collected = collection_module.collect_roi_for_matching_datasets(
            self.database,
            scenario_version_id=next_version.id,
            execution_id=201,
            roi_id=9,
            source="PRODUCTION",
            roi_image_path=str(self.roi_path),
        )
        self.database.commit()

        self.assertEqual(len(collected), 1)
        item = self.database.get(DatasetItem, collected[0])
        self.assertEqual(
            item.annotation_json["collection"]["configured_scenario_version_id"],
            self.scenario_version.id,
        )
        self.assertEqual(item.annotation_json["collection"]["scenario_version_id"], next_version.id)

    def test_collects_original_camera_image_by_published_recipe_and_deduplicates(self) -> None:
        collected = collection_module.collect_original_image_for_matching_datasets(
            self.database,
            recipe_id=self.recipe.id,
            detection_task_id=301,
            image_index=1,
            source="PRODUCTION",
            image_path=str(self.original_path),
            source_image_path="//camera-share/AS01-CAMERA1PICTURE1-SN001.png",
            inspection_result="OK",
        )
        self.database.commit()

        self.assertEqual(len(collected), 1)
        item = self.database.get(DatasetItem, collected[0])
        self.assertEqual(item.dataset_id, self.original_dataset.id)
        self.assertEqual(item.source, "AUTO_ORIGINAL")
        self.assertEqual(item.annotation_status, "PENDING")
        self.assertTrue(Path(item.media_path).is_file())
        self.assertEqual(item.original_name, "AS01-CAMERA1PICTURE1-SN001.png")
        self.assertEqual(item.annotation_json["collection"]["scope"], "ORIGINAL")
        self.assertEqual(item.annotation_json["collection"]["recipe_id"], self.recipe.id)
        self.assertEqual(item.annotation_json["collection"]["inspection_result"], "OK")

        duplicated = collection_module.collect_original_image_for_matching_datasets(
            self.database,
            recipe_id=self.recipe.id,
            detection_task_id=302,
            image_index=1,
            source="PRODUCTION",
            image_path=str(self.original_path),
        )
        mismatched_recipe = collection_module.collect_original_image_for_matching_datasets(
            self.database,
            recipe_id=self.recipe.id + 1,
            detection_task_id=303,
            image_index=1,
            source="PRODUCTION",
            image_path=str(self.original_path),
        )
        self.assertEqual(duplicated, [])
        self.assertEqual(mismatched_recipe, [])


if __name__ == "__main__":
    unittest.main()
