import asyncio
import json
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import configuration, datasets, inspection
from app.db.base import Base
from app.db.init_db import _upgrade_report_selection_schema
from app.db.session import get_db
from app.models.inspection import DetectionApiCall, DetectionTask
from app.models.intelligence import Dataset, DatasetItem, InspectionScenario, InspectionScenarioVersion, ScenarioExecution
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services.smb_storage import SmbStorage, SmbStorageError


class ProductionControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine, expire_on_commit=False)
        self.patches = [patch.object(module, "PROJECT_ROOT", self.root) for module in (configuration, datasets, inspection)]
        for patched in self.patches:
            patched.start()
        app = FastAPI()
        app.include_router(configuration.router, prefix="/configuration")
        app.include_router(datasets.router, prefix="/datasets")
        app.include_router(inspection.router, prefix="/inspection")
        app.include_router(inspection.public_router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.database
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        product = Product(code="MAT", name="Material")
        station = Station(code="ST", name="Station")
        self.database.add_all([product, station])
        self.database.flush()
        self.recipe = Recipe(code="L_MAT_OP_CAMERA1_P1", recipe_family_code="L_MAT_OP_CAMERA1_P1",
                             name="Test", status="PUBLISHED", version="V1", version_no=1,
                             product_id=product.id, station_id=station.id, line_code="L", material_code="MAT",
                             process_code="OP", camera_code="CAMERA1", capture_index=1)
        self.database.add(self.recipe)
        self.database.flush()
        self.roi = RegionOfInterest(recipe_id=self.recipe.id, code="ROI1", name="ROI1", object_type="WIRE",
                                    x_ratio=0, y_ratio=0, width_ratio=0.5, height_ratio=0.5)
        self.database.add(self.roi)
        self.image = self.root / "uploads" / "reference.png"
        self.image.parent.mkdir()
        Image.new("RGB", (50, 50), "white").save(self.image)
        self.recipe.base_image_path = str(self.image)
        self.database.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.engine.dispose()
        for patched in self.patches:
            patched.stop()
        self.temporary.cleanup()

    def paths(self):
        paths = [self.root / f"OP-CAMERA1PICTURE1-SN-{index}.png" for index in (1, 2)]
        for path in paths:
            Image.new("RGB", (20, 20), "blue").save(path)
        return [str(path) for path in paths]

    def payload(self, paths):
        return {"sn": "SN", "line": "L", "materialCode": "MAT", "operation": "OP", "times": 1, "image_paths": paths}

    async def fake_execute(self, database, recipe, **values):
        path = Path(values["artifact_root"]) / "result.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 20), "green").save(path)
        return {"result": "OK", "image_paths": [str(path)], "request_id": values["request_id"],
                "image_results": [{"result": "OK", "inspection_items": []}]}

    async def test_two_images_return_two_results_in_order(self):
        paths = self.paths()
        with patch.object(inspection.engine, "execute", new=AsyncMock(side_effect=self.fake_execute)) as execute:
            response = await self.client.post("/api/detect", json=self.payload(paths))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"], "OK")
        self.assertEqual(body["image_paths"], [SmbStorage.result_path_for_source(path) for path in paths])
        self.assertEqual(execute.await_count, 2)
        self.assertEqual(len(self.database.scalars(select(DetectionApiCall)).all()), 1)

    async def test_one_download_failure_keeps_both_paths_and_other_image_runs(self):
        paths = self.paths()
        stage = SmbStorage.stage_input

        def stage_one(storage, source, destination):
            if source == paths[0]:
                raise SmbStorageError("模拟相机 1 下载失败")
            return stage(storage, source, destination)

        with patch.object(SmbStorage, "stage_input", stage_one), patch.object(
            inspection.engine, "execute", new=AsyncMock(side_effect=self.fake_execute)
        ) as execute:
            body = (await self.client.post("/api/detect", json=self.payload(paths))).json()
        self.assertEqual(body["code"], 1003)
        self.assertEqual(body["image_paths"], [paths[0], SmbStorage.result_path_for_source(paths[1])])
        self.assertEqual(execute.await_count, 1)
        self.assertEqual(len(body["inspection_results"]), 2)
        self.assertEqual(body["result"], "ERROR")

    async def test_validation_failure_retains_nonempty_paths(self):
        paths = self.paths()
        payload = self.payload(paths)
        payload.pop("operation")
        body = (await self.client.post("/api/detect", json=payload)).json()
        self.assertNotEqual(body["code"], 200)
        self.assertEqual(body["image_paths"], paths)

    async def test_empty_model_result_is_not_silent_success(self):
        paths = self.paths()
        with patch.object(inspection.engine, "execute", new=AsyncMock(return_value={"result": "OK", "image_paths": []})):
            body = (await self.client.post("/api/detect", json=self.payload(paths))).json()
        self.assertEqual(body["image_paths"], paths)
        self.assertEqual(body["result"], "ERROR")

    async def test_withdraw_v1_reuses_draft_and_preserves_snapshot(self):
        created = (await self.client.post(f"/configuration/recipes/{self.recipe.id}/draft")).json()
        withdrawn = await self.client.post(f"/configuration/recipes/{self.recipe.id}/unpublish")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["id"], created["id"])
        self.database.refresh(self.recipe)
        self.assertEqual(self.recipe.status, "ARCHIVED")
        self.assertEqual(len(self.recipe.rois), 1)
        self.assertIsNone(inspection.match_published_recipe_by_values(
            self.database, line="L", materialcode="MAT", operation="OP", camera="CAMERA1", picture=1))

    async def test_edit_switch_to_passthrough_only_changes_production_on_publish(self):
        draft = (await self.client.post(f"/configuration/recipes/{self.recipe.id}/draft")).json()["id"]
        payload = {"code": self.recipe.code, "name": self.recipe.name,
                   "product_id": self.recipe.product_id, "station_id": self.recipe.station_id,
                   "line_code": "L", "material_code": "MAT", "process_code": "OP", "camera_code": "CAMERA1",
                   "capture_index": 1, "execution_mode": "PASS_THROUGH", "skip_reason": "该相机不检测"}
        updated = await self.client.put(f"/configuration/recipes/{draft}", json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.database.refresh(self.recipe)
        self.assertEqual(self.recipe.execution_mode, "INSPECT")
        published = await self.client.post(f"/configuration/recipes/{draft}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["version_no"], 2)
        version = self.database.get(Recipe, draft)
        self.database.refresh(version)
        self.assertEqual(len(version.rois), 1)
        body = (await self.client.post("/api/detect", json=self.payload(self.paths()))).json()
        self.assertEqual(body["image_paths"], self.paths())
        self.assertEqual([item["result"] for item in body["inspection_results"]], ["SKIPPED", "SKIPPED"])

    async def test_delete_published_family_keeps_record_snapshot(self):
        task = DetectionTask(request_id="old", sn="SN", recipe_id=self.recipe.id, recipe_version="V1")
        self.database.add(task)
        record = DetectionApiCall(caller_ip="127.0.0.1", sn="SN", response_code=200, call_status="SUCCESS",
                                  response_payload={"inspection_results": [{"request_id": "old", "recipe_code": self.recipe.code, "image_results": []}]})
        self.database.add(record)
        self.database.commit()
        await self.client.post(f"/configuration/recipes/{self.recipe.id}/draft")
        deleted = await self.client.delete(f"/configuration/recipes/{self.recipe.id}/family")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual((await self.client.get("/configuration/recipes")).json(), [])
        details = (await self.client.get(f"/inspection/call-records/{record.id}")).json()
        self.assertEqual(details["recipes"][0]["recipe_version"], "V1")
        self.assertTrue(self.image.is_file())

    async def test_export_download_has_unique_names_annotations_and_skips_deleted(self):
        dataset = Dataset(code="DATA", name="Images", purpose="TRAIN", media_type="IMAGE", annotation_type="CLASSIFICATION")
        self.database.add(dataset)
        self.database.flush()
        for index in range(3):
            self.database.add(DatasetItem(dataset_id=dataset.id, media_path=str(self.image), original_name="same.png",
                                          media_type="IMAGE", is_deleted=index == 2, annotation_json={"label": "OK"}))
        self.database.commit()
        response = await self.client.get(f"/datasets/{dataset.id}/export")
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        self.assertIn("attachment", response.headers["content-disposition"])
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(len(manifest["items"]), 2)
            names = [item["file"] for item in manifest["items"]]
            self.assertEqual(len(set(names)), 2)
            self.assertEqual(manifest["items"][0]["annotation"], {"label": "OK"})

    async def test_report_exclusion_covers_all_camera_tasks_and_can_be_restored(self):
        scenario = InspectionScenario(code="S", name="Scene", mode="VLM_DIRECT")
        self.database.add(scenario)
        self.database.flush()
        version = InspectionScenarioVersion(scenario_id=scenario.id, version="V1", status="PUBLISHED")
        self.database.add(version)
        self.database.flush()
        for index in (1, 2):
            task = DetectionTask(request_id=f"request-{index}", sn="SN", recipe_id=self.recipe.id, recipe_version="V1")
            self.database.add(task)
            self.database.flush()
            self.database.add(ScenarioExecution(scenario_version_id=version.id, detection_task_id=task.id,
                                                source="PRODUCTION", status="COMPLETED", result="NG"))
        record = DetectionApiCall(caller_ip="127.0.0.1", sn="SN", response_code=200, call_status="SUCCESS",
                                  response_payload={"inspection_results": [{"request_id": f"request-{index}"} for index in (1, 2)]})
        self.database.add(record)
        self.database.commit()
        self.assertEqual((await self.client.get("/inspection/reports")).json()["overall"]["total"], 2)
        for excluded, total in [(True, 0), (False, 2)]:
            response = await self.client.put(f"/inspection/call-records/{record.id}/report-selection",
                                             json={"excluded": excluded, "reason": "调试记录"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self.client.get("/inspection/reports")).json()["overall"]["total"], total)
            self.assertEqual((await self.client.get("/inspection/call-records")).json()[0]["report_excluded"], excluded)
        self.assertEqual([item.result for item in self.database.scalars(select(ScenarioExecution)).all()], ["NG", "NG"])

    def test_report_schema_upgrade_is_repeatable_and_preserves_data(self):
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE detection_tasks (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE detection_api_calls (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO detection_tasks (id) VALUES (1)"))
        with patch("app.db.init_db.engine", engine):
            _upgrade_report_selection_schema()
            _upgrade_report_selection_schema()
        self.assertIn("report_note", [item["name"] for item in inspect(engine).get_columns("detection_api_calls")])
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT report_excluded FROM detection_tasks WHERE id=1")).scalar(), 0)
        engine.dispose()
