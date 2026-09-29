from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

import httpx
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.scenarios import get_db
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioEdge,
    ScenarioExecution,
    ScenarioNode,
)
from app.services import scenario_runtime as runtime_module


class PublishedSceneApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.original_scene_api_key = settings.scene_api_key
        settings.scene_api_key = ""

        direct_scene = InspectionScenario(
            code="API_DIRECT",
            name="外部 VLM 场景",
            category="TEST",
            mode="VLM_DIRECT",
        )
        workflow_scene = InspectionScenario(
            code="API_WORKFLOW",
            name="外部流程场景",
            category="TEST",
            mode="WORKFLOW",
        )
        self.database.add_all((direct_scene, workflow_scene))
        self.database.flush()

        self.direct_version = InspectionScenarioVersion(
            scenario_id=direct_scene.id,
            version="1.0",
            status="PUBLISHED",
            input_schema_json={
                "fields": [
                    {
                        "name": "expected_text",
                        "label": "期望文字",
                        "type": "TEXT",
                        "required": True,
                    }
                ]
            },
        )
        self.workflow_version = InspectionScenarioVersion(
            scenario_id=workflow_scene.id,
            version="2.0",
            status="PUBLISHED",
        )
        self.database.add_all((self.direct_version, self.workflow_version))
        self.database.flush()
        self.database.add(
            ScenarioNode(
                scenario_version_id=self.workflow_version.id,
                node_key="start",
                name="开始",
                node_type="START",
                config_json={
                    "inputs": [
                        {
                            "name": "expected_state",
                            "label": "期望状态",
                            "type": "TEXT",
                            "required": True,
                        },
                        {"name": "image_path", "label": "图片路径", "type": "IMAGE"},
                    ]
                },
                sort_order=1,
            )
        )
        direct_scene.published_version_id = self.direct_version.id
        workflow_scene.published_version_id = self.workflow_version.id
        self.database.commit()

        self.previous_override = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = lambda: self.database

    async def asyncTearDown(self) -> None:
        if self.previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = self.previous_override
        settings.scene_api_key = self.original_scene_api_key
        self.database.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    async def _fake_execute(
        self,
        database: Session,
        version: InspectionScenarioVersion,
        *,
        image_path: str,
        source: str,
        context: dict[str, object] | None = None,
        **_: object,
    ) -> ScenarioExecution:
        execution = ScenarioExecution(
            scenario_version_id=version.id,
            source=source,
            status="COMPLETED",
            result="OK",
            score=0.97,
            input_json={"image_path": image_path, **(context or {})},
            output_json={
                "result": {
                    "result": "OK",
                    "scene_mode": version.scenario.mode,
                    "received_inputs": context or {},
                },
                "nodes": {},
                "traces": [],
            },
            elapsed_ms=12.5,
            completed_at=datetime.utcnow(),
        )
        database.add(execution)
        database.flush()
        return execution

    async def test_published_direct_and_workflow_scenes_expose_callable_api(self) -> None:
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                contract = await client.get("/api/v1/scenarios/invoke/API_DIRECT")
                self.assertEqual(contract.status_code, 200)
                contract_payload = contract.json()
                self.assertEqual(contract_payload["method"], "POST")
                self.assertEqual(
                    contract_payload["endpoint_path"],
                    "/api/v1/scenarios/invoke/API_DIRECT",
                )
                self.assertEqual(contract_payload["image_input"]["field_name"], "image_path")
                self.assertTrue(contract_payload["image_input"]["required"])
                self.assertFalse(contract_payload["batch_images"]["supported"])
                self.assertEqual(contract_payload["input_fields"][0]["name"], "expected_text")

                direct_response = await client.post(
                    "/api/v1/scenarios/invoke/API_DIRECT",
                    json={
                        "request_id": "direct-001",
                        "image_path": "C:/images/direct.jpg",
                        "inputs": {"expected_text": "FUS"},
                    },
                )
                self.assertEqual(direct_response.status_code, 200)
                self.assertEqual(direct_response.json()["code"], 0)
                self.assertEqual(direct_response.json()["result"], "OK")
                self.assertEqual(direct_response.json()["scene"]["mode"], "VLM_DIRECT")
                self.assertEqual(
                    direct_response.json()["output"]["received_inputs"],
                    {"expected_text": "FUS"},
                )

                workflow_response = await client.post(
                    "/api/v1/scenarios/invoke/API_WORKFLOW",
                    json={
                        "request_id": "workflow-001",
                        "image_path": "C:/images/workflow.jpg",
                        "inputs": {"expected_state": "LOCKED"},
                    },
                )
                self.assertEqual(workflow_response.status_code, 200)
                self.assertEqual(workflow_response.json()["code"], 0)
                self.assertEqual(workflow_response.json()["scene"]["mode"], "WORKFLOW")
                self.assertEqual(
                    workflow_response.json()["output"]["received_inputs"],
                    {"expected_state": "LOCKED"},
                )

        self.assertEqual(runtime_mock.await_count, 2)
        self.assertEqual(runtime_mock.await_args_list[0].kwargs["source"], "SCENE_API")
        self.assertEqual(runtime_mock.await_args_list[1].kwargs["source"], "SCENE_API")

    async def test_scene_api_rejects_missing_or_unknown_declared_inputs(self) -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing = await client.post(
                "/api/v1/scenarios/invoke/API_DIRECT",
                json={"image_path": "C:/images/direct.jpg", "inputs": {}},
            )
            unknown = await client.post(
                "/api/v1/scenarios/invoke/API_DIRECT",
                json={
                    "image_path": "C:/images/direct.jpg",
                    "inputs": {"unexpected": "value", "expected_text": "FUS"},
                },
            )

        self.assertEqual(missing.status_code, 422)
        self.assertIn("期望文字", missing.json()["detail"])
        self.assertEqual(unknown.status_code, 422)
        self.assertIn("unexpected", unknown.json()["detail"])

    async def test_published_workflow_api_runs_detector_then_annotation_node(self) -> None:
        scene = InspectionScenario(
            code="API_ANNOTATE",
            name="外部画框流程场景",
            category="TEST",
            mode="WORKFLOW",
        )
        self.database.add(scene)
        self.database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scene.id,
            version="1.0",
            status="PUBLISHED",
        )
        self.database.add(version)
        self.database.flush()
        self.database.add_all((
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="start",
                name="开始",
                node_type="START",
                sort_order=0,
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="detector",
                name="线束检测",
                node_type="VISION_MODEL",
                sort_order=1,
                config_json={"model_version_id": 999},
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="annotate",
                name="绘制检测结果",
                node_type="IMAGE_ANNOTATE",
                sort_order=2,
                config_json={
                    "image_path": "{{ input.image_path }}",
                    "objects": "{{ nodes.detector.objects }}",
                },
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="end",
                name="结束",
                node_type="END",
                sort_order=3,
                config_json={
                    "output": {
                        "result": "{{ nodes.detector.result }}",
                        "result_image_path": "{{ nodes.annotate.image_path }}",
                        "object_count": "{{ nodes.detector.object_count }}",
                    }
                },
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="start",
                target_node_key="detector",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="detector",
                target_node_key="annotate",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="annotate",
                target_node_key="end",
            ),
        ))
        scene.published_version_id = version.id
        self.database.commit()

        original_selector = runtime_module._select_published_vision_model
        original_inference = runtime_module.run_yolo_inference
        artifact_path: Path | None = None

        def fake_inference(_spec, _image_path, *, config):
            self.assertNotIn("expected_min_count", config)
            return {
                "result": "OK",
                "confidence": 0.97,
                "reason": "检测到线束端子",
                "task_type": "YOLO_DETECTION",
                "objects": [{
                    "index": 0,
                    "label": "harness_terminal",
                    "confidence": 0.97,
                    "bbox": [8, 10, 46, 34],
                    "bbox_format": "XYXY_PIXEL",
                }],
                "detection_count": 1,
                "object_count": 1,
            }

        runtime_module._select_published_vision_model = lambda _database, _node: SimpleNamespace(
            version_id=999,
            model_code="AI_HARNESS",
            task_type="YOLO_DETECTION",
        )
        runtime_module.run_yolo_inference = fake_inference
        try:
            with TemporaryDirectory() as directory:
                input_image = Path(directory) / "input.jpg"
                Image.new("RGB", (64, 48), "white").save(input_image)
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    response = await client.post(
                        "/api/v1/scenarios/invoke/API_ANNOTATE",
                        json={"request_id": "annotate-001", "image_path": str(input_image), "inputs": {}},
                    )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["code"], 0, payload)
            self.assertEqual(payload["result"], "OK")
            self.assertEqual(payload["output"]["object_count"], 1)
            artifact_path = Path(payload["output"]["result_image_path"])
            self.assertTrue(artifact_path.is_file())
            with Image.open(artifact_path) as annotated:
                self.assertEqual(annotated.size, (64, 48))
        finally:
            runtime_module._select_published_vision_model = original_selector
            runtime_module.run_yolo_inference = original_inference
            if artifact_path and artifact_path.exists():
                artifact_path.unlink()
                try:
                    artifact_path.parent.rmdir()
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
