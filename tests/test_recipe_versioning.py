from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import configuration as configuration_module
from app.api.routes.configuration import (
    create_recipe_draft,
    list_recipe_versions,
    publish_recipe,
    rollback_recipe_version,
)
from app.db.base import Base
from app.models.inspection import InspectionItem
from app.models.recipe import Recipe, RecipeFeatureAnchor, RegionOfInterest
from app.models.system import Product, Station


class RecipeVersioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.temporary_directory = TemporaryDirectory()
        self.temporary_root = Path(self.temporary_directory.name)
        self.original_project_root = configuration_module.PROJECT_ROOT
        configuration_module.PROJECT_ROOT = self.temporary_root

        product = Product(code="MAT01", name="物料 MAT01")
        station = Station(code="L01_OP10", name="L01 · OP10")
        self.database.add_all((product, station))
        self.database.flush()
        reference_image = self.temporary_root / "reference.png"
        Image.new("RGB", (96, 64), "white").save(reference_image)
        self.source = Recipe(
            code="L01_MAT01_OP10_CAM01_P01",
            name="线束检测",
            version="V1",
            version_no=1,
            status="PUBLISHED",
            product_id=product.id,
            station_id=station.id,
            line_code="L01",
            material_code="MAT01",
            process_code="OP10",
            camera_code="CAM01",
            capture_index=1,
            base_image_path=str(reference_image),
            reference_width=96,
            reference_height=64,
        )
        self.database.add(self.source)
        self.database.flush()
        roi = RegionOfInterest(
            recipe_id=self.source.id,
            code="ROI_HARNESS",
            name="线束锁付",
            object_type="HARNESS",
            x_ratio=0.2,
            y_ratio=0.2,
            width_ratio=0.3,
            height_ratio=0.3,
            pixel_coordinates={"x": 19, "y": 13, "width": 29, "height": 19},
        )
        self.database.add(roi)
        self.database.flush()
        self.database.add(
            InspectionItem(
                roi_id=roi.id,
                code="HARNESS_EXIST",
                name="线束存在",
                inspection_type="EXISTENCE",
                capability="REFERENCE_SIMILARITY",
                expected_json={"expected": True},
                rule_json={"minimum": 0.8},
            )
        )
        self.database.add(
            RecipeFeatureAnchor(
                recipe_id=self.source.id,
                code="ANCHOR_01",
                name="固定螺钉",
                x_ratio=0.05,
                y_ratio=0.05,
                width_ratio=0.1,
                height_ratio=0.1,
            )
        )
        self.database.commit()

    def tearDown(self) -> None:
        configuration_module.PROJECT_ROOT = self.original_project_root
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _recipe(self, recipe_id: int) -> Recipe:
        return self.database.get(Recipe, recipe_id)

    def test_draft_publish_and_rollback_keep_history_immutable(self) -> None:
        draft_result = create_recipe_draft(self.source.id, database=self.database)
        draft = self._recipe(draft_result["id"])
        source = self._recipe(self.source.id)

        self.assertFalse(draft_result["reused"])
        self.assertEqual(source.status, "PUBLISHED")
        self.assertEqual(source.recipe_family_code, source.code)
        self.assertEqual(draft.status, "DRAFT")
        self.assertEqual(draft.recipe_family_code, source.code)
        self.assertEqual(draft.source_recipe_id, source.id)
        self.assertNotEqual(draft.base_image_path, source.base_image_path)
        self.assertTrue(Path(draft.base_image_path).is_file())
        self.assertEqual(len(draft.rois), 1)
        self.assertEqual(draft.rois[0].code, "ROI_HARNESS")
        self.assertEqual(len(draft.rois[0].inspection_items), 1)
        self.assertIsNotNone(draft.feature_anchor)

        second_open = create_recipe_draft(self.source.id, database=self.database)
        self.assertTrue(second_open["reused"])
        self.assertEqual(second_open["id"], draft.id)

        published = publish_recipe(draft.id, database=self.database)
        self.database.refresh(source)
        self.database.refresh(draft)
        self.assertEqual(source.status, "ARCHIVED")
        self.assertEqual(draft.status, "PUBLISHED")
        self.assertEqual(draft.version_no, 2)
        self.assertEqual(draft.version, "V2")
        self.assertEqual(published["production_recipe_id"], draft.id)

        history = list_recipe_versions(source.id, database=self.database)
        self.assertEqual(len(history["versions"]), 2)
        self.assertEqual({row["status"] for row in history["versions"]}, {"ARCHIVED", "PUBLISHED"})

        rollback = rollback_recipe_version(source.id, database=self.database)
        rollback_draft = self._recipe(rollback["id"])
        self.database.refresh(source)
        self.database.refresh(draft)
        self.assertEqual(rollback_draft.status, "DRAFT")
        self.assertEqual(rollback_draft.source_recipe_id, source.id)
        self.assertEqual(source.status, "ARCHIVED")
        self.assertEqual(draft.status, "PUBLISHED")
        self.assertEqual(len(rollback_draft.rois), 1)


if __name__ == "__main__":
    unittest.main()
