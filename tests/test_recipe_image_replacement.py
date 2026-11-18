import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import configuration
from app.db.base import Base
from app.db.session import get_db
from app.models.inspection import DetectionItemResult, DetectionTask, InspectionItem
from app.models.intelligence import InspectionScenario, InspectionScenarioVersion, RoiScenarioBinding, ScenarioExecution
from app.models.recipe import Recipe, RecipeFeatureAnchor, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceImage
from app.models.system import Product, Station


class RecipeImageReplacementTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.root_patch = patch.object(configuration, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="IMG_MAT", name="换图测试")
        station = Station(code="IMG_STATION", name="换图测试")
        self.database.add_all([product, station])
        self.database.flush()
        self.source_image = self.root / "uploads/recipes/source.png"
        self.source_image.parent.mkdir(parents=True)
        Image.new("RGB", (100, 80), "orange").save(self.source_image)
        self.source = Recipe(code="IMAGE_REPLACE", name="已发布配方", status="PUBLISHED", version="V1", version_no=1,
                             product_id=product.id, station_id=station.id, base_image_path=str(self.source_image),
                             reference_width=100, reference_height=80, line_code="L1", material_code="IMG_MAT",
                             process_code="OP1", camera_code="CAMERA1")
        self.database.add(self.source)
        self.database.flush()
        roi = RegionOfInterest(recipe_id=self.source.id, code="ROI1", name="线束", x_ratio=0.1, y_ratio=0.1,
                               width_ratio=0.4, height_ratio=0.4)
        group = ReferenceGroup(code="IMAGE_REPLACE_ROI1_AUTO", name="历史参考图", object_type="WIRE", class_code="WIRE")
        scene = InspectionScenario(code="IMAGE_SCENE", name="线束场景")
        self.database.add_all([roi, group, scene])
        self.database.flush()
        self.reference_path = self.root / f"uploads/references/{group.id}/standard.png"
        self.reference_path.parent.mkdir(parents=True)
        Image.new("RGB", (40, 32), "orange").save(self.reference_path)
        version = InspectionScenarioVersion(scenario_id=scene.id, status="PUBLISHED", version="1.0")
        self.database.add(version)
        self.database.flush()
        self.version_id = version.id
        self.group_id = group.id
        self.source_roi_id = roi.id
        self.database.add_all([
            InspectionItem(roi_id=roi.id, code="EXIST", name="存在", inspection_type="EXISTENCE",
                           capability="REFERENCE_SIMILARITY", reference_group_id=group.id),
            ReferenceImage(group_id=group.id, image_path=str(self.reference_path)),
            RoiScenarioBinding(roi_id=roi.id, scenario_version_id=version.id, input_mapping_json={"type": "线束"}),
            RecipeFeatureAnchor(recipe_id=self.source.id, x_ratio=0, y_ratio=0, width_ratio=0.1, height_ratio=0.1),
        ])
        self.database.commit()
        draft = configuration.create_recipe_draft(self.source.id, self.database)
        self.draft_id = draft["id"]
        draft_row = self.database.get(Recipe, self.draft_id)
        self.old_draft_image = Path(draft_row.base_image_path)
        self.draft_roi_id = draft_row.rois[0].id
        self.draft_item_id = draft_row.rois[0].inspection_items[0].id

        def session():
            with Session(self.engine) as database:
                yield database

        application = FastAPI()
        application.include_router(configuration.router, prefix="/api/v1/configuration")
        application.dependency_overrides[get_db] = session
        self.client = TestClient(application, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.database.close()
        self.engine.dispose()
        self.root_patch.stop()
        self.directory.cleanup()

    def upload(self, recipe_id=None, content=None):
        if content is None:
            stream = io.BytesIO()
            Image.new("RGB", (240, 160), "blue").save(stream, format="PNG")
            content = stream.getvalue()
        return self.client.post(f"/api/v1/configuration/recipes/{recipe_id or self.draft_id}/image",
                                files={"file": ("replacement.png", content, "image/png")})

    def test_replace_draft_image_with_scene_binding_preserves_published_version(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cleared_roi_count"], 1)
        self.assertEqual((response.json()["width"], response.json()["height"]), (240, 160))
        self.database.expire_all()
        draft = self.database.get(Recipe, self.draft_id)
        self.assertEqual(draft.rois, [])
        self.assertIsNone(draft.feature_anchor)
        retired = self.database.get(RegionOfInterest, self.draft_roi_id)
        self.assertTrue(retired.is_deleted)
        self.assertFalse(retired.enabled)
        binding = self.database.scalar(select(RoiScenarioBinding).where(RoiScenarioBinding.roi_id == retired.id))
        self.assertFalse(binding.enabled)
        source = self.database.get(Recipe, self.source.id)
        self.assertEqual(source.status, "PUBLISHED")
        self.assertEqual(len(source.rois), 1)
        self.assertIsNotNone(source.feature_anchor)
        source_binding = self.database.scalar(select(RoiScenarioBinding).where(RoiScenarioBinding.roi_id == self.source_roi_id))
        self.assertTrue(source_binding.enabled)
        self.assertTrue(self.source_image.is_file())
        self.assertTrue(self.reference_path.is_file())
        self.assertIsNotNone(self.database.get(ReferenceGroup, self.group_id))
        detail = self.client.get(f"/api/v1/configuration/recipes/{self.draft_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["rois"], [])

    def test_replace_image_preserves_draft_test_records_and_image_files(self):
        task = DetectionTask(request_id="draft-preview", sn="TEST", recipe_id=self.draft_id, recipe_version="draft", status="OK")
        self.database.add(task)
        self.database.flush()
        result = DetectionItemResult(task_id=task.id, image_path=str(self.old_draft_image), roi_id=self.draft_roi_id,
                                     inspection_item_id=self.draft_item_id, status="OK")
        execution = ScenarioExecution(scenario_version_id=self.version_id, detection_task_id=task.id,
                                      roi_id=self.draft_roi_id, result="OK", status="COMPLETED")
        self.database.add_all([result, execution])
        self.database.commit()
        result_id, execution_id = result.id, execution.id
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.database.expire_all()
        self.assertEqual(self.database.get(DetectionItemResult, result_id).roi_id, self.draft_roi_id)
        self.assertEqual(self.database.get(ScenarioExecution, execution_id).result, "OK")
        self.assertIsNotNone(self.database.get(InspectionItem, self.draft_item_id))
        self.assertTrue(self.old_draft_image.is_file())

    def test_published_upload_is_rejected_without_changing_image(self):
        before = self.source_image.read_bytes()
        response = self.upload(self.source.id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("草稿", response.json()["detail"])
        self.assertEqual(self.source_image.read_bytes(), before)

    def test_invalid_image_keeps_existing_roi_and_file(self):
        before = set((self.root / "uploads").rglob("*.png"))
        response = self.upload(content=b"not an image")
        self.assertEqual(response.status_code, 400, response.text)
        self.database.expire_all()
        draft = self.database.get(Recipe, self.draft_id)
        self.assertEqual(Path(draft.base_image_path), self.old_draft_image)
        self.assertEqual(len(draft.rois), 1)
        self.assertIsNotNone(draft.feature_anchor)
        self.assertEqual(before, set((self.root / "uploads").rglob("*.png")))

    def test_saved_draft_can_replace_image_repeatedly_without_reusing_old_rois(self):
        draft = self.database.get(Recipe, self.draft_id)
        draft.status = "SAVED"
        self.database.commit()
        first = self.upload()
        second = self.upload()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["cleared_roi_count"], 0)
        self.assertNotEqual(first.json()["image_path"], second.json()["image_path"])
        self.database.expire_all()
        self.assertEqual(self.database.get(Recipe, self.draft_id).status, "DRAFT")
        self.assertEqual(self.database.get(Recipe, self.source.id).status, "PUBLISHED")

    def test_database_failure_rolls_back_image_roi_binding_and_anchor(self):
        before = set((self.root / "uploads").rglob("*.png"))
        failure = OperationalError("COMMIT", {}, RuntimeError("test database failure"))
        with patch.object(Session, "commit", side_effect=failure), self.assertLogs(configuration.logger, level="ERROR"):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertIn("原图片和 ROI 配置已保留", response.json()["detail"])
        self.database.expire_all()
        draft = self.database.get(Recipe, self.draft_id)
        self.assertEqual(Path(draft.base_image_path), self.old_draft_image)
        self.assertEqual(len(draft.rois), 1)
        self.assertIsNotNone(draft.feature_anchor)
        binding = self.database.scalar(select(RoiScenarioBinding).where(RoiScenarioBinding.roi_id == self.draft_roi_id))
        self.assertTrue(binding.enabled)
        self.assertEqual(before, set((self.root / "uploads").rglob("*.png")))


if __name__ == "__main__":
    unittest.main()
