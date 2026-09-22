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
            auto_collect_enabled=True,
            auto_collect_limit=1,
            label_schema_json=["harness", "connector"],
        )
        self.database.add(self.dataset)
        self.database.commit()
        self.roi_path = self.temporary_root / "roi.png"
        Image.new("RGB", (48, 32), "orange").save(self.roi_path)

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


if __name__ == "__main__":
    unittest.main()
