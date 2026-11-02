from datetime import datetime
from io import BytesIO
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
from app.services.scene_s3_storage import SceneS3Storage, SceneS3StorageError


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
        self.original_s3_settings = (
            settings.scene_result_s3_enabled,
            settings.scene_result_s3_auto_publish,
            settings.scene_result_s3_bucket,
            settings.scene_result_s3_prefix,
        )
        settings.scene_api_key = ""
        settings.scene_result_s3_enabled = False
        settings.scene_result_s3_auto_publish = False

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
        (
            settings.scene_result_s3_enabled,
            settings.scene_result_s3_auto_publish,
            settings.scene_result_s3_bucket,
            settings.scene_result_s3_prefix,
        ) = self.original_s3_settings
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

    async def test_s3_result_upload_draws_image_and_returns_destination(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_auto_publish = True
        settings.scene_result_s3_bucket = "inspection-results"
        settings.scene_result_s3_prefix = "published-scenes"
        captured_uploads: list[tuple[bytes, str, str, dict]] = []

        class FakeS3Client:
            def upload_fileobj(self, source, bucket, key, ExtraArgs):
                captured_uploads.append((source.read(), bucket, key, ExtraArgs))

        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            with (
                patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock),
                patch("app.services.scene_s3_storage.SceneS3Storage._client", return_value=FakeS3Client()),
                patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)),
            ):
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    response = await client.post(
                        "/api/v1/scenarios/invoke/API_DIRECT",
                        json={
                            "image_path": str(input_image),
                            "inputs": {"expected_text": "FUS"},
                            "result_s3_key": "orders/001_result.jpg",
                        },
                    )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["result_image_path"], "s3://inspection-results/published-scenes/orders/001_result.jpg")
        self.assertEqual(payload["image_paths"], [payload["result_image_path"]])
        self.assertEqual(len(captured_uploads), 1)
        image_bytes, bucket, key, extra_args = captured_uploads[0]
        self.assertTrue(image_bytes.startswith(b"\xff\xd8"))
        self.assertEqual(bucket, "inspection-results")
        self.assertEqual(key, "published-scenes/orders/001_result.jpg")
        self.assertEqual(extra_args["ContentType"], "image/jpeg")

    async def test_s3_upload_failure_does_not_claim_success(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_auto_publish = True
        settings.scene_result_s3_bucket = "inspection-results"
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            with (
                patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock),
                patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)),
                patch(
                    "app.services.scene_s3_storage.SceneS3Storage.publish_result",
                    side_effect=SceneS3StorageError("S3 服务不可用"),
                ),
            ):
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    response = await client.post(
                        "/api/v1/scenarios/invoke/API_DIRECT",
                        json={"image_path": str(input_image), "inputs": {"expected_text": "FUS"}},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 5002)
        self.assertEqual(response.json()["result"], "OK")
        self.assertEqual(response.json()["image_paths"], [])
        self.assertIn("结果图上传失败", response.json()["message"])

    async def test_s3_result_key_defaults_to_scene_and_execution(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_auto_publish = True
        settings.scene_result_s3_bucket = "inspection-results"
        settings.scene_result_s3_prefix = "published-scenes"
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            with (
                patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock),
                patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)),
                patch("app.services.scene_s3_storage.SceneS3Storage.publish_result", autospec=True) as publish,
            ):
                publish.side_effect = lambda storage, path, **kwargs: storage.destination(
                    kwargs["scene_code"], kwargs["execution_id"], kwargs["requested_key"]
                )[2]
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    response = await client.post(
                        "/api/v1/scenarios/invoke/API_DIRECT",
                        json={"image_path": str(input_image), "inputs": {"expected_text": "FUS"}},
                    )
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.json()["result_image_path"],
            r"^s3://inspection-results/published-scenes/API_DIRECT/\d{4}/\d{2}/\d{2}/\d+_result\.jpg$",
        )

    async def test_s3_key_is_rejected_before_scene_execution(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_auto_publish = True
        settings.scene_result_s3_bucket = "inspection-results"
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/v1/scenarios/invoke/API_DIRECT",
                    json={
                        "image_path": "C:/images/direct.jpg",
                        "inputs": {"expected_text": "FUS"},
                        "result_s3_key": "../outside.jpg",
                    },
                )
        self.assertEqual(response.status_code, 422)
        runtime_mock.assert_not_awaited()

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
                sort_order=4,
                config_json={
                    "output": {
                        "result_image_path": "{{ nodes.s3_1.result_image_path }}",
                        "object_count": "{{ nodes.detector.object_count }}",
                    }
                },
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="s3_1",
                name="上传结果图",
                node_type="S3_UPLOAD",
                sort_order=3,
                config_json={
                    "image_path": "{{ nodes.annotate.image_path }}",
                    "key": "{{ input.result_s3_key }}",
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
                target_node_key="s3_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="s3_1",
                target_node_key="end",
            ),
        ))
        scene.published_version_id = version.id
        self.database.commit()

        original_selector = runtime_module._select_published_vision_model
        original_inference = runtime_module.run_yolo_inference
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_bucket = "inspection-results"
        uploaded_images: list[tuple[bytes, str]] = []
        test_case = self

        class FakeS3Client:
            def upload_fileobj(self, source, _bucket, key, ExtraArgs):
                test_case.assertEqual(ExtraArgs["ContentType"], "image/jpeg")
                uploaded_images.append((source.read(), key))

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
                with (
                    patch("app.services.scenario_runtime.PROJECT_ROOT", Path(directory)),
                    patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)),
                    patch("app.services.scene_s3_storage.SceneS3Storage._client", return_value=FakeS3Client()),
                ):
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        contract_response = await client.get("/api/v1/scenarios/invoke/API_ANNOTATE")
                        response = await client.post(
                            "/api/v1/scenarios/invoke/API_ANNOTATE",
                            json={
                                "request_id": "annotate-001",
                                "image_path": str(input_image),
                                "inputs": {},
                                "result_s3_key": "external/annotate-001_result.jpg",
                            },
                        )
                self.assertEqual(contract_response.status_code, 200)
                self.assertTrue(contract_response.json()["result_image"]["upload_node"])
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["code"], 0, payload)
                self.assertEqual(payload["result"], "UNCERTAIN")
                self.assertEqual(payload["output"]["result"], "UNCERTAIN")
                self.assertEqual(payload["output"]["object_count"], 1)
                self.assertEqual(payload["output"]["result_image_path"], payload["result_image_path"])
                self.assertEqual(payload["image_paths"], [payload["result_image_path"]])
                self.assertEqual(len(uploaded_images), 1)
                self.assertEqual(payload["result_image_path"].split("/")[2], "inspection-results")
                self.assertEqual(uploaded_images[0][1], "scene-results/external/annotate-001_result.jpg")
                with Image.open(BytesIO(uploaded_images[0][0])) as annotated:
                    self.assertEqual(annotated.size, (64, 48))
                    self.assertLess(min(annotated.getpixel((46, 34))), 240)
                with patch(
                    "app.services.scene_s3_storage.SceneS3Storage.publish_result",
                    side_effect=SceneS3StorageError("S3 目标桶不可用"),
                ):
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        failed = await client.post(
                            "/api/v1/scenarios/invoke/API_ANNOTATE",
                            json={"image_path": str(input_image), "inputs": {}},
                        )
                self.assertEqual(failed.status_code, 200)
                self.assertEqual(failed.json()["code"], 5001)
                self.assertEqual(failed.json()["result"], "ERROR")
                self.assertIn("S3 目标桶不可用", failed.json()["message"])
                failed_execution = self.database.get(ScenarioExecution, failed.json()["execution_id"])
                self.assertEqual(failed_execution.output_json["traces"][-1]["node_type"], "S3_UPLOAD")
        finally:
            runtime_module._select_published_vision_model = original_selector
            runtime_module.run_yolo_inference = original_inference

    async def test_s3_enabled_without_upload_node_does_not_upload(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_bucket = "inspection-results"
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with (
            patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock),
            patch("app.services.scene_s3_storage.SceneS3Storage.publish_result") as publish,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/v1/scenarios/invoke/API_DIRECT",
                    json={"image_path": "C:/images/input.jpg", "inputs": {"expected_text": "FUS"}},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 0)
        self.assertEqual(response.json()["image_paths"], [])
        publish.assert_not_called()

    async def test_request_key_without_upload_node_is_rejected(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_bucket = "inspection-results"
        runtime_mock = AsyncMock(side_effect=self._fake_execute)
        transport = httpx.ASGITransport(app=app)
        with patch("app.api.routes.scenarios.scenario_runtime.execute", runtime_mock):
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/v1/scenarios/invoke/API_DIRECT",
                    json={
                        "image_path": "C:/images/input.jpg",
                        "inputs": {"expected_text": "FUS"},
                        "result_s3_key": "external/result.jpg",
                    },
                )
        self.assertEqual(response.status_code, 422)
        runtime_mock.assert_not_awaited()

    async def test_s3_upload_converts_png_input_to_jpeg(self) -> None:
        settings.scene_result_s3_enabled = True
        settings.scene_result_s3_bucket = "inspection-results"
        captured: list[bytes] = []
        test_case = self

        class FakeS3Client:
            def upload_fileobj(self, source, _bucket, _key, ExtraArgs):
                test_case.assertEqual(ExtraArgs["ContentType"], "image/jpeg")
                captured.append(source.read())

        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "input.png"
            Image.new("RGB", (16, 12), "orange").save(image_path)
            with patch("app.services.scene_s3_storage.SceneS3Storage._client", return_value=FakeS3Client()):
                uri = SceneS3Storage().publish_result(
                    image_path,
                    scene_code="API_DIRECT",
                    execution_id=42,
                    requested_key=None,
                )

        self.assertIn("API_DIRECT/", uri)
        self.assertTrue(captured[0].startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
