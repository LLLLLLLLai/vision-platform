import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import automation, configuration, datasets, inspection, scenarios
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models.inspection import DetectionApiCall
from app.models.intelligence import (
    AutomationJob, Dataset, DatasetItem, InspectionScenario, InspectionScenarioVersion,
    RoiScenarioBinding, ScenarioExecution, VlmModelConfig,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services import dataset_collection_service, inspection_engine, scenario_runtime


class CollectionOnlyScenesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine, expire_on_commit=False)
        self.patches = [patch.object(module, "PROJECT_ROOT", self.root) for module in (
            scenarios, inspection, configuration, datasets, inspection_engine, dataset_collection_service,
        )]
        self.patches.append(patch.object(settings, "image_alignment_enabled", False))
        self.patches.append(patch.object(settings, "scene_api_key", ""))
        for patched in self.patches:
            patched.start()
        app = FastAPI()
        for module, prefix in ((scenarios, "/scenarios"), (inspection, "/inspection"),
                               (datasets, "/datasets"), (automation, "/jobs"), (configuration, "/configuration")):
            app.include_router(module.router, prefix=prefix)
        app.include_router(inspection.public_router, prefix="/api")
        app.include_router(scenarios.public_router, prefix="/scenes")
        app.dependency_overrides[get_db] = lambda: self.database
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        product = Product(code="MAT", name="物料")
        station = Station(code="ST", name="工位")
        self.database.add_all([product, station])
        self.database.flush()
        self.recipe = Recipe(code="L_MAT_OP_CAMERA1_P1", recipe_family_code="L_MAT_OP_CAMERA1_P1",
                             name="采集配方", status="PUBLISHED", version="V1", version_no=1,
                             product_id=product.id, station_id=station.id, line_code="L", material_code="MAT",
                             process_code="OP", camera_code="CAMERA1", capture_index=1)
        self.database.add(self.recipe)
        self.database.flush()
        self.roi = RegionOfInterest(recipe_id=self.recipe.id, code="ROI1", name="线束", object_type="WIRE",
                                    x_ratio=0.2, y_ratio=0.2, width_ratio=0.5, height_ratio=0.5)
        self.database.add(self.roi)
        self.paths = []
        for index, color in enumerate(("blue", "red"), start=1):
            path = self.root / f"OP-CAMERA1PICTURE1-SN-{index}.png"
            Image.new("RGB", (80, 60), color).save(path)
            self.paths.append(str(path))
        self.recipe.base_image_path = self.paths[0]
        self.database.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.engine.dispose()
        for patched in reversed(self.patches):
            patched.stop()
        self.temporary.cleanup()

    async def create_scene(self, mode="VLM_DIRECT", bind=True, target=True):
        response = await self.client.post("/scenarios", json={
            "name": "线束采集", "mode": mode, "run_mode": "COLLECT_ONLY",
        })
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()
        version = self.database.get(InspectionScenarioVersion, created["version_id"])
        response = await self.client.post(f"/scenarios/versions/{version.id}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        if bind:
            self.database.add(RoiScenarioBinding(roi_id=self.roi.id, scenario_version_id=version.id))
        if target:
            self.database.add(Dataset(code=f"DS{version.id}", name="线束素材", purpose="TRAIN", media_type="IMAGE",
                                      annotation_type="CLASSIFICATION", collection_scope="ROI",
                                      collection_scenario_version_id=version.id, auto_collect_enabled=True,
                                      auto_collect_limit=10))
        self.database.commit()
        return version

    async def detect(self, paths=None):
        return await self.client.post("/api/detect", json={
            "sn": "SN", "line": "L", "materialCode": "MAT", "operation": "OP", "times": 1,
            "image_paths": self.paths if paths is None else paths,
        })

    def collection_statuses(self, body):
        return [target["status"] for group in body["inspection_results"] for image in group["image_results"]
                for item in image["inspection_items"] for target in item["actual"].get("result", {}).get("collection", {}).get("datasets", [])]

    async def test_two_images_collect_cropped_unlabeled_inputs_and_return_ng_original_paths(self):
        await self.create_scene()
        with patch.object(scenario_runtime.scenario_runtime, "_run_definition", new=AsyncMock(side_effect=AssertionError("must not infer"))) as inference:
            response = await self.detect()
        body = response.json()
        self.assertEqual(body["code"], settings.public_detect_success_code, body)
        self.assertEqual(body["result"], "NG")
        self.assertEqual(body["image_paths"], self.paths)
        self.assertIn("未执行质量检测", body["message"])
        inference.assert_not_awaited()
        items = self.database.scalars(select(DatasetItem)).all()
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item.annotation_status, "PENDING")
            self.assertIsNone(item.ground_truth)
            with Image.open(item.media_path) as image:
                self.assertEqual(image.size, (40, 30))
            self.assertEqual(item.annotation_json["collection"]["roi_code"], "ROI1")
        records = self.database.scalars(select(DetectionApiCall)).all()
        self.assertEqual(len(records), 1)
        detail = (await self.client.get(f"/inspection/call-records/{records[0].id}")).json()
        self.assertIn("COLLECTION", json.dumps(detail))
        self.assertEqual(self.database.scalars(select(AutomationJob)).all(), [])
        report = (await self.client.get("/inspection/reports")).json()
        self.assertNotIn("线束采集", json.dumps(report, ensure_ascii=False))

    async def test_workflow_collection_publish_and_preview_without_models(self):
        version = await self.create_scene(mode="WORKFLOW")
        with open(self.paths[0], "rb") as image:
            response = await self.client.post(f"/scenarios/versions/{version.id}/preview-upload",
                                              files={"image": ("test.png", image, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"], "NOT_INSPECTED")
        self.assertEqual(response.json()["output_json"]["result"]["collection"]["status"], "PREVIEW")
        self.assertEqual(self.database.scalars(select(DatasetItem)).all(), [])
        self.recipe.status = "SAVED"
        self.database.commit()
        response = await self.client.post(f"/configuration/recipes/{self.recipe.id}/publish")
        self.assertEqual(response.status_code, 200, response.text)

    async def test_disabled_or_missing_dataset_fails_visibly_but_still_returns_ng(self):
        await self.create_scene(target=False)
        body = (await self.detect()).json()
        self.assertNotEqual(body["code"], settings.public_detect_success_code)
        self.assertEqual(body["result"], "NG")
        self.assertEqual(body["image_paths"], self.paths)
        self.assertIn("数据集", body["message"])
        self.assertNotIn("线束采集", json.dumps((await self.client.get("/inspection/reports")).json(), ensure_ascii=False))

    async def test_duplicate_and_limit_are_not_false_saved_statuses(self):
        await self.create_scene()
        dataset = self.database.scalar(select(Dataset))
        dataset.auto_collect_limit = 1
        self.database.commit()
        first = (await self.detect()).json()
        self.assertEqual(self.collection_statuses(first), ["COLLECTED", "LIMIT_REACHED"])
        second = (await self.detect([self.paths[0], ""])).json()
        self.assertEqual(self.collection_statuses(second), ["DUPLICATE"])
        self.assertEqual(second["image_paths"], [self.paths[0]])
        self.assertEqual(len(self.database.scalars(select(DatasetItem)).all()), 1)

    async def test_disabled_collection_never_adds_material(self):
        await self.create_scene()
        dataset = self.database.scalar(select(Dataset))
        dataset.auto_collect_enabled = False
        self.database.commit()
        body = (await self.detect()).json()
        self.assertEqual(self.collection_statuses(body), ["DISABLED", "DISABLED"])
        self.assertNotEqual(body["code"], settings.public_detect_success_code)
        self.assertEqual(self.database.scalars(select(DatasetItem)).all(), [])

    async def test_recipe_test_does_not_collect_production_material(self):
        await self.create_scene()
        with open(self.paths[0], "rb") as image:
            response = await self.client.post("/inspection/test", data={"recipe_id": self.recipe.id},
                                              files={"file": ("sample.png", image, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"], "NG")
        self.assertEqual(self.database.scalars(select(DatasetItem)).all(), [])
        execution = self.database.scalar(select(ScenarioExecution))
        self.assertEqual(execution.source, "RECIPE_TEST")

    async def test_invalid_mode_is_rejected_and_existing_scenes_default_to_detection(self):
        response = await self.client.post("/scenarios", json={"name": "原检测场景", "mode": "VLM_DIRECT"})
        version_id = response.json()["version_id"]
        self.assertEqual((await self.client.get(f"/scenarios/versions/{version_id}")).json()["run_mode"], "DETECT")
        self.assertEqual((await self.client.post(f"/scenarios/versions/{version_id}/publish")).status_code, 400)
        response = await self.client.put(f"/scenarios/versions/{version_id}", json={"definition_json": {"run_mode": "BAD"}})
        self.assertEqual(response.status_code, 422)

    async def test_switch_to_detection_and_back_preserves_collected_data_and_old_report_exclusion(self):
        collection_version = await self.create_scene()
        await self.detect([self.paths[0]])
        original_item = self.database.scalar(select(DatasetItem))
        model = VlmModelConfig(code="MODEL", name="模型", base_url="http://unused/v1", model_name="test")
        self.database.add(model)
        self.database.commit()
        created = (await self.client.post(f"/scenarios/{collection_version.scenario_id}/versions", json={
            "version": "2", "source_version_id": collection_version.id,
            "primary_vlm_model_id": model.id, "prompt_template": "检查图片",
            "definition_json": {"run_mode": "DETECT"},
        })).json()
        response = await self.client.post(f"/scenarios/versions/{created['id']}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        self.database.expire_all()
        self.assertEqual(self.database.scalar(select(RoiScenarioBinding.scenario_version_id)), created["id"])
        self.assertEqual(self.database.scalar(select(Dataset.collection_scenario_version_id)), created["id"])
        with patch.object(scenario_runtime.scenario_runtime, "_run_definition", new=AsyncMock(return_value={"result": "OK"})):
            body = (await self.detect([self.paths[1]])).json()
        self.assertEqual(body["result"], "OK", body)
        self.assertTrue(Path(original_item.media_path).is_file())
        response = await self.client.post(f"/scenarios/{collection_version.scenario_id}/versions", json={
            "version": "3", "source_version_id": created["id"], "definition_json": {"run_mode": "COLLECT_ONLY"},
        })
        next_version = response.json()["id"]
        self.assertEqual((await self.client.post(f"/scenarios/versions/{next_version}/publish")).status_code, 200)
        with patch.object(scenario_runtime.scenario_runtime, "_run_definition", new=AsyncMock()) as inference:
            body = (await self.detect([self.paths[0]])).json()
        self.assertEqual(body["result"], "NG")
        inference.assert_not_awaited()
        report = (await self.client.get("/inspection/reports")).json()
        self.assertIn("线束采集", json.dumps(report, ensure_ascii=False))
        self.assertEqual(self.database.get(InspectionScenarioVersion, collection_version.id).definition_json["run_mode"], "COLLECT_ONLY")

    async def test_download_or_crop_failure_keeps_ng_and_path_count(self):
        await self.create_scene()
        Path(self.paths[1]).unlink()
        body = (await self.detect()).json()
        self.assertNotEqual(body["code"], settings.public_detect_success_code)
        self.assertEqual(body["result"], "NG")
        self.assertEqual(body["image_paths"], self.paths)
        self.assertEqual(len(self.database.scalars(select(DatasetItem)).all()), 1)

    async def test_storage_failure_reports_error_and_rolls_back_sample(self):
        await self.create_scene()
        write_bytes = Path.write_bytes

        def deny_temp(path, data):
            if path.suffix == ".tmp":
                raise OSError("模拟磁盘写入失败")
            return write_bytes(path, data)

        with patch.object(Path, "write_bytes", deny_temp):
            body = (await self.detect()).json()
        self.assertEqual(body["result"], "NG")
        self.assertNotEqual(body["code"], settings.public_detect_success_code)
        self.assertEqual(self.collection_statuses(body), ["FAILED", "FAILED"])
        self.assertEqual(self.database.scalars(select(DatasetItem)).all(), [])

    async def test_scene_api_collects_without_review_and_original_path(self):
        version = await self.create_scene()
        body = (await self.client.post(f"/scenes/invoke/{version.scenario.code}", json={
            "image_path": self.paths[0], "inputs": {}, "enqueue_review": True,
        })).json()
        self.assertEqual(body["code"], 0, body)
        self.assertEqual(body["result"], "NG")
        self.assertEqual(body["image_paths"], [self.paths[0]])
        self.assertFalse(body["review_queued"])
        self.assertEqual(len(self.database.scalars(select(DatasetItem)).all()), 1)

    async def test_version_switch_preserves_production_until_publish_and_normal_validation(self):
        version = await self.create_scene()
        response = await self.client.post(f"/scenarios/{version.scenario_id}/versions", json={
            "version": "2.0", "source_version_id": version.id,
        })
        draft_id = response.json()["id"]
        draft = self.database.get(InspectionScenarioVersion, draft_id)
        self.assertEqual(draft.definition_json["run_mode"], "COLLECT_ONLY")
        await self.client.put(f"/scenarios/versions/{draft_id}", json={"definition_json": {"run_mode": "DETECT"}})
        response = await self.client.post(f"/scenarios/versions/{draft_id}/publish")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(version.scenario.published_version_id, version.id)
        self.assertEqual((await self.detect()).json()["result"], "NG")
        response = await self.client.put(f"/scenarios/versions/{version.id}", json={"definition_json": {"run_mode": "DETECT"}})
        self.assertEqual(response.status_code, 409)

    async def test_collection_executions_cannot_be_reviewed_or_evaluated(self):
        version = await self.create_scene()
        await self.detect([self.paths[0]])
        execution = self.database.scalar(select(ScenarioExecution))
        for method, endpoint, payload in (
            ("POST", f"/scenarios/executions/{execution.id}/review", None),
            ("PUT", f"/scenarios/executions/{execution.id}/manual-review", {"verdict": "NG"}),
            ("POST", "/jobs/scene-evaluations", {"scenario_version_id": version.id, "dataset_id": self.database.scalar(select(Dataset.id))}),
        ):
            response = await self.client.request(method, endpoint, json=payload)
            self.assertEqual(response.status_code, 400, response.text)

    async def test_mixed_detection_and_collection_keeps_true_detection_metrics(self):
        await self.create_scene()
        scene = InspectionScenario(code="REAL_DETECT", name="正常检测", mode="VLM_DIRECT")
        version = InspectionScenarioVersion(scenario=scene, version="1", status="PUBLISHED")
        self.database.add(version)
        self.database.flush()
        roi = RegionOfInterest(recipe_id=self.recipe.id, code="ROI2", name="正常检测区", object_type="WIRE",
                               x_ratio=0, y_ratio=0, width_ratio=0.2, height_ratio=0.2)
        self.database.add(roi)
        self.database.flush()
        self.database.add(RoiScenarioBinding(roi_id=roi.id, scenario_version_id=version.id))
        self.database.commit()
        with patch.object(scenario_runtime.scenario_runtime, "_run_definition", new=AsyncMock(return_value={"result": "OK", "confidence": 0.99})) as inference:
            body = (await self.detect([self.paths[0]])).json()
        self.assertEqual(inference.await_count, 1)
        self.assertEqual(body["result"], "NG")
        self.assertTrue(body["image_paths"][0].endswith("_result.png"), body)
        report = json.dumps((await self.client.get("/inspection/reports")).json(), ensure_ascii=False)
        self.assertIn("正常检测", report)
        self.assertNotIn("线束采集", report)
