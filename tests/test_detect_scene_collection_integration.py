from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi import Request
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import inspection as inspection_route
from app.api.routes.inspection import PublicDetectRequest, public_detect
from app.db.base import Base
from app.models.inspection import DetectionApiCall, DetectionTask
from app.models.intelligence import (
    Dataset,
    DatasetItem,
    InspectionScenario,
    InspectionScenarioVersion,
    RoiScenarioBinding,
    ScenarioExecution,
    VlmModelConfig,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services import dataset_collection_service, inspection_engine, scenario_runtime


class DetectSceneCollectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_executes_scene_records_result_and_collects_bound_dataset(self) -> None:
        """Exercise the real detect orchestration without SMB or a live VLM."""

        with TemporaryDirectory() as directory, ExitStack() as cleanup:
            root = Path(directory)
            engine = create_engine(f"sqlite:///{root / 'detect-integration.db'}")
            Base.metadata.create_all(engine)
            database = Session(engine)
            previous_roots = (
                inspection_route.PROJECT_ROOT,
                inspection_engine.PROJECT_ROOT,
                dataset_collection_service.PROJECT_ROOT,
            )

            def restore_project_roots() -> None:
                (
                    inspection_route.PROJECT_ROOT,
                    inspection_engine.PROJECT_ROOT,
                    dataset_collection_service.PROJECT_ROOT,
                ) = previous_roots

            cleanup.callback(engine.dispose)
            cleanup.callback(database.close)
            cleanup.callback(restore_project_roots)
            inspection_route.PROJECT_ROOT = root
            inspection_engine.PROJECT_ROOT = root
            dataset_collection_service.PROJECT_ROOT = root

            source_path = root / (
                "AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635901.png"
            )
            second_source_path = root / (
                "AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635902.png"
            )
            Image.new("RGB", (160, 100), "orange").save(source_path)
            Image.new("RGB", (160, 100), "orange").save(second_source_path)
            product = Product(code="CN000798", name="测试物料")
            station = Station(code="AS10", name="测试工序", line_code="L4", process_code="AS10")
            database.add_all((product, station))
            database.flush()
            recipe = Recipe(
                code="L4-CN000798-AS10-CAMERA1-P1",
                recipe_family_code="L4-CN000798-AS10-CAMERA1-P1",
                name="相机一检测配方",
                version="1.0",
                status="PUBLISHED",
                product_id=product.id,
                station_id=station.id,
                line_code="L4",
                material_code="CN000798",
                process_code="AS10",
                camera_code="CAMERA1",
                capture_index=1,
            )
            model = VlmModelConfig(
                code="DETECT_PIPELINE_VLM",
                name="检测链路测试 VLM",
                base_url="http://vlm.test/v1",
                model_name="pipeline-test-vlm",
            )
            scenario = InspectionScenario(
                code="SCENE_HARNESS_VLM",
                name="线束 VLM 测试",
                mode="VLM_DIRECT",
            )
            database.add_all((recipe, model, scenario))
            database.flush()
            scenario_version = InspectionScenarioVersion(
                scenario_id=scenario.id,
                version="1.0",
                status="PUBLISHED",
                primary_vlm_model_id=model.id,
                prompt_template="检查线束，期望文字 {{ input.expected_text }}。",
                input_schema_json={
                    "fields": [
                        {
                            "name": "expected_text",
                            "label": "期望文字",
                            "type": "TEXT",
                            "required": False,
                        }
                    ]
                },
            )
            database.add(scenario_version)
            database.flush()
            scenario.published_version_id = scenario_version.id
            roi = RegionOfInterest(
                recipe_id=recipe.id,
                code="ROI_HARNESS",
                name="线束区域",
                object_type="线束",
                x_ratio=0.1,
                y_ratio=0.1,
                width_ratio=0.5,
                height_ratio=0.6,
            )
            database.add(roi)
            database.flush()
            database.add(
                RoiScenarioBinding(
                    roi_id=roi.id,
                    scenario_version_id=scenario_version.id,
                    input_mapping_json={"expected_text": "FU5"},
                )
            )
            dataset = Dataset(
                code="HARNESS_VLM_AUTO",
                name="线束 VLM 测试",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="NONE",
                collection_scenario_version_id=scenario_version.id,
                collection_scope="ROI",
                auto_collect_enabled=True,
                auto_collect_limit=10,
            )
            original_dataset = Dataset(
                code="HARNESS_CAMERA_ORIGINAL_AUTO",
                name="线束相机原图自动采集",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="DETECTION",
                collection_scope="ORIGINAL",
                collection_recipe_id=recipe.id,
                auto_collect_enabled=True,
                auto_collect_limit=10,
                label_schema_json=["harness"],
            )
            database.add_all((dataset, original_dataset))
            database.commit()

            prompts: list[str] = []

            async def fake_judge(_model, *, prompt: str, **_kwargs):
                prompts.append(prompt)
                await asyncio.sleep(0.01)
                return {"result": "OK", "confidence": 0.99, "reason": "集成测试模拟结果"}

            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/detect",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "scheme": "http",
                    "query_string": b"",
                }
            )
            payload = PublicDetectRequest(
                line="L4",
                materialCode="CN000798",
                operation="AS10",
                times=1,
                image_paths=[str(source_path), str(second_source_path)],
            )
            original_judge = scenario_runtime.vlm_client.judge
            scenario_runtime.vlm_client.judge = fake_judge
            try:
                response = await public_detect(payload, request, database)
            finally:
                scenario_runtime.vlm_client.judge = original_judge

            self.assertEqual(response["code"], 200)
            self.assertEqual(response["result"], "OK")
            self.assertEqual(len(response["image_paths"]), 2)
            self.assertTrue(all(Path(path).is_file() for path in response["image_paths"]))
            self.assertEqual(len(prompts), 2)
            self.assertTrue(all("FU5" in prompt for prompt in prompts))
            self.assertEqual(
                len(database.scalars(select(DetectionApiCall)).all()),
                1,
            )
            call = database.scalar(select(DetectionApiCall))
            tasks = database.scalars(select(DetectionTask)).all()
            executions = database.scalars(select(ScenarioExecution)).all()
            collected = database.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
            ).all()
            original_collected = database.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == original_dataset.id)
            ).all()
            self.assertEqual(call.sn, "CN000798263700002")
            self.assertEqual(call.call_status, "SUCCESS")
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task.status == "OK" for task in tasks))
            self.assertEqual(len(executions), 2)
            self.assertTrue(all(execution.result == "OK" for execution in executions))
            self.assertEqual(len(collected), 1)
            self.assertEqual(collected[0].source, "AUTO_ROI")
            self.assertEqual(collected[0].annotation_status, "PENDING")
            self.assertTrue(Path(collected[0].media_path).is_file())
            self.assertEqual(len(original_collected), 1)
            self.assertEqual(original_collected[0].source, "AUTO_ORIGINAL")
            self.assertEqual(original_collected[0].annotation_status, "PENDING")
            self.assertTrue(Path(original_collected[0].media_path).is_file())
            self.assertEqual(
                original_collected[0].annotation_json["collection"]["recipe_id"],
                recipe.id,
            )


if __name__ == "__main__":
    unittest.main()
