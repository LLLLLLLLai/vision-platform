from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes.datasets import (
    DatasetAnnotationTypeChange, DatasetItemUpdate, DatasetUpdate,
    change_dataset_annotation_type, update_dataset, update_dataset_item,
    _save_pre_annotation,
)
from app.db.base import Base
from app.models.intelligence import (
    Dataset,
    DatasetItem,
    InspectionScenario,
    InspectionScenarioVersion,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.inspection import DetectionTask
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

    def test_collection_switch_controls_production_capture(self) -> None:
        update_dataset(
            self.dataset.id,
            DatasetUpdate(auto_collect_enabled=False),
            database=self.database,
        )
        self.assertEqual(
            collection_module.collect_roi_for_matching_datasets(
                self.database,
                scenario_version_id=self.scenario_version.id,
                execution_id=400,
                roi_id=9,
                source="PRODUCTION",
                roi_image_path=str(self.roi_path),
            ),
            [],
        )
        update_dataset(
            self.dataset.id,
            DatasetUpdate(auto_collect_enabled=True),
            database=self.database,
        )
        collected = collection_module.collect_roi_for_matching_datasets(
            self.database,
            scenario_version_id=self.scenario_version.id,
            execution_id=401,
            roi_id=9,
            source="PRODUCTION",
            roi_image_path=str(self.roi_path),
        )
        self.assertEqual(len(collected), 1)

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
        self.assertIn("生产检测_相机原图_配方线束相机一配方", item.original_name)
        self.assertIn("相机CAMERA1_拍照1", item.original_name)
        self.assertIn("T301_IMG1", item.original_name)
        self.assertEqual(item.annotation_json["collection"]["source_filename"], "AS01-CAMERA1PICTURE1-SN001.png")
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

    def test_recipe_roi_names_capture_location_and_preserve_images(self) -> None:
        self.original_dataset.collection_scope = "ROI_RECIPE"
        roi = RegionOfInterest(
            recipe_id=self.recipe.id, code="ROI6", name="线束端子",
            x_ratio=0, y_ratio=0, width_ratio=1, height_ratio=1,
        )
        task = DetectionTask(request_id="collection-name-test", sn="CN123456",
                             recipe_id=self.recipe.id, recipe_version=self.recipe.version)
        self.database.add_all((roi, task))
        self.database.flush()
        collected = collection_module.collect_recipe_roi_for_matching_datasets(
            self.database, recipe_id=self.recipe.id, detection_task_id=task.id,
            image_index=2, roi_id=roi.id, source="PRODUCTION",
            roi_image_path=str(self.roi_path), source_image_path=r"\\server\share\camera-source.png",
        )
        item = self.database.get(DatasetItem, collected[0])
        for part in ("配方ROI", "配方线束相机一配方", "版本V1", "拉线L1", "物料MAT_HARNESS",
                     "工序AS01", "相机CAMERA1", "区域ROI6", "条码CN123456", "IMG2"):
            self.assertIn(part, item.original_name)
        provenance = item.annotation_json["collection"]
        self.assertEqual(provenance["source_filename"], "camera-source.png")
        self.assertEqual(provenance["roi_name"], "线束端子")
        self.assertEqual(Path(item.media_path).read_bytes(), self.roi_path.read_bytes())
        self.assertIn(f"recipe_{self.recipe.id}_roi_{roi.id}_task_", Path(item.media_path).name)
        before_name, before_path = item.original_name, item.media_path
        update_dataset_item(self.original_dataset.id, item.id, DatasetItemUpdate(
            annotation_json={"boxes": [{"label": "harness", "x": 0, "y": 0, "width": 0.5, "height": 0.5}]},
        ), database=self.database)
        self.assertEqual(item.annotation_json["collection"], provenance)
        change_dataset_annotation_type(self.original_dataset.id, DatasetAnnotationTypeChange(
            annotation_type="CLASSIFICATION", confirm_clear_annotations=True,
            expected_revision=self.original_dataset.revision,
        ), database=self.database)
        self.assertEqual(item.annotation_json, {"collection": provenance})
        self.assertEqual(item.annotation_status, "PENDING")
        self.assertEqual((item.original_name, item.media_path), (before_name, before_path))
        self.original_dataset.label_schema_json = ["harness"]
        _save_pre_annotation(
            self.database, self.original_dataset, item, {"label": "harness"},
            model=SimpleNamespace(name="自动标注模型", task_type="YOLO_CLASSIFICATION"),
            version=SimpleNamespace(id=1, version="V1"), warnings=[], confidence=0.8,
        )
        self.assertEqual(item.annotation_json["label"], "harness")
        self.assertEqual(item.annotation_json["collection"], provenance)

    def test_collection_only_scene_api_is_named_without_a_recipe(self) -> None:
        result = collection_module.collect_scene_image(
            self.database, self.scenario_version, execution_id=900, roi_id=None,
            source="SCENE_API", image_path=str(self.roi_path), context={"sn": "API001"},
        )
        item = self.database.get(DatasetItem, result["collected_item_ids"][0])
        self.assertIn("场景接口_场景ROI_场景线束检测场景", item.original_name)
        self.assertIn("条码API001", item.original_name)
        self.assertIn("E900", item.original_name)
        self.assertNotIn("配方", item.original_name)
        self.assertEqual(item.annotation_json["collection"]["source_filename"], "roi.png")
        self.assertIsNone(item.annotation_json["collection"]["recipe_id"])

    def test_safe_display_names_are_bounded_and_keep_full_metadata(self) -> None:
        self.recipe.name = '../bad\\name:*?"<>|\n' + "线束" * 100
        metadata = collection_module._collection_metadata(
            self.database, scope="ORIGINAL", source="PRODUCTION", image_path=str(self.original_path),
            recipe_id=self.recipe.id, detection_task_id=301, context={"sn": "产品" * 200},
        )
        name = collection_module._collection_name(metadata, str(self.original_path))
        self.assertLessEqual(len(name), 500)
        self.assertNotRegex(name, r'[<>:"/\\|?*\x00-\x1f]')
        self.assertEqual(metadata["recipe_name"], self.recipe.name)
        self.assertEqual(metadata["sn"], "产品" * 200)
        self.assertTrue(name.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
