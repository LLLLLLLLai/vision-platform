from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.scenarios import _validate_ocr_node, _workflow_preflight, publish_scenario_version
from app.db.base import Base
from app.models.intelligence import InspectionScenario, InspectionScenarioVersion, ScenarioEdge, ScenarioNode
from app.services import paddle_ocr_client
from app.services.scenario_runtime import ScenarioRuntime


class OcrClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_and_text_contract(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            Image.new("RGB", (10, 10), "white").save(source)

            def respond(request):
                self.assertEqual(request.url.path, "/ocr")
                self.assertIn(b"sample.png", request.content)
                return httpx.Response(200, json={"status": "COMPLETED", "lines": [
                    {"text": "FU5", "confidence": 0.98, "polygon": [[0, 0], [8, 8]]},
                    {"text": "低分", "confidence": 0.2, "polygon": []},
                ]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client):
                output = await paddle_ocr_client.recognize_text(str(source), min_score=0.5)
            self.assertEqual(output["text"], "FU5")
            self.assertEqual(output["texts"], ["FU5"])
            self.assertEqual(output["text_count"], 1)
            self.assertNotIn("result", output)

    async def test_empty_text_is_valid_but_not_quality_ok(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "blank.png"
            Image.new("RGB", (10, 10), "white").save(source)
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "COMPLETED", "lines": []})))
            with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client):
                output = await paddle_ocr_client.recognize_text(str(source))
            self.assertEqual(output["text"], "")
            self.assertEqual(output["text_count"], 0)
            self.assertNotIn("result", output)

    async def test_errors_never_become_empty_success(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (10, 10)).save(source)
            responses = [
                httpx.Response(503, json={"detail": "unavailable"}),
                httpx.Response(200, json={"status": "COMPLETED"}),
                httpx.Response(200, json={"status": "FAILED", "lines": []}),
                httpx.Response(200, json={"status": "COMPLETED", "lines": [{"text": "a", "confidence": 2}]}),
            ]
            for response in responses:
                with self.subTest(response=response):
                    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
                    with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client), self.assertRaises(paddle_ocr_client.OcrRequestError):
                        await paddle_ocr_client.recognize_text(str(source))

    async def test_missing_image_and_timeout(self):
        with self.assertRaises(paddle_ocr_client.OcrRequestError):
            await paddle_ocr_client.recognize_text("missing-ocr-test-image.png")
        with TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (10, 10)).save(source)

            def timeout(request):
                raise httpx.ReadTimeout("timeout", request=request)

            client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
            with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client), self.assertRaisesRegex(paddle_ocr_client.OcrRequestError, "超时"):
                await paddle_ocr_client.recognize_text(str(source))


class OcrWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        scene = InspectionScenario(code="OCR_TEST", name="OCR 流程", mode="WORKFLOW")
        self.database.add(scene)
        self.database.flush()
        self.version = InspectionScenarioVersion(scenario_id=scene.id, version="1", status="DRAFT")
        self.database.add(self.version)
        self.database.flush()

    def tearDown(self):
        self.database.close()
        self.engine.dispose()

    def graph(self, entries):
        keys = []
        for order, (key, kind, config) in enumerate(entries):
            self.database.add(ScenarioNode(scenario_version_id=self.version.id, node_key=key, name=key, node_type=kind, sort_order=order, enabled=True, config_json=config))
            keys.append(key)
        for source, target in zip(keys, keys[1:]):
            self.database.add(ScenarioEdge(scenario_version_id=self.version.id, source_node_key=source, target_node_key=target, mapping_json={}))
        self.database.commit()
        self.database.refresh(self.version)

    async def test_publish_run_mapping_and_rule(self):
        self.graph([
            ("start", "START", {}),
            ("ocr_1", "OCR", {"image_path": "{{ input.image_path }}", "output_mapping": {"recognized": "{{ response.text }}"}}),
            ("rule", "RULE", {"actual": "{{ nodes.ocr_1.recognized }}", "operator": "EQUALS", "expected": "FU5"}),
            ("end", "END", {"output": {"text": "{{ nodes.ocr_1.text }}", "result": "{{ nodes.rule.result }}"}}),
        ])
        preflight = _workflow_preflight(self.database, self.version)
        self.assertTrue(preflight["ready"], preflight)
        publish_scenario_version(self.version.id, database=self.database)
        self.assertEqual(self.version.status, "PUBLISHED")
        output = {"status": "COMPLETED", "text": "FU5", "texts": ["FU5"], "lines": [], "text_count": 1}
        with patch("app.services.scenario_runtime.recognize_text", new=AsyncMock(return_value=output)) as recognize:
            execution = await ScenarioRuntime().execute(self.database, self.version, image_path="roi.png", source="TEST")
        self.assertEqual(execution.status, "COMPLETED", execution.error_message)
        self.assertEqual(execution.result, "OK")
        self.assertEqual(execution.output_json["result"]["text"], "FU5")
        trace = execution.output_json["traces"][1]
        self.assertEqual(trace["execution_status"], "COMPLETED")
        self.assertIsNone(trace["detection_result"])
        self.assertIn("elapsed_ms", trace)
        recognize.assert_awaited_once_with("roi.png", min_score=0.0)

    async def test_ocr_error_stops_workflow(self):
        self.graph([("start", "START", {}), ("ocr", "OCR", {"image_path": "{{ input.image_path }}"}), ("end", "END", {})])
        publish_scenario_version(self.version.id, database=self.database)
        with patch("app.services.scenario_runtime.recognize_text", new=AsyncMock(side_effect=paddle_ocr_client.OcrRequestError("OCR timeout"))):
            execution = await ScenarioRuntime().execute(self.database, self.version, image_path="roi.png", source="TEST")
        self.assertEqual(execution.status, "FAILED")
        self.assertIn("OCR timeout", execution.error_message)
        self.assertEqual(execution.output_json["traces"][-1]["execution_status"], "FAILED")

    async def test_missing_mapping_does_not_fall_back_to_original_image(self):
        self.graph([("start", "START", {}), ("ocr", "OCR", {"image_path": "{{ input.missing_image }}"}), ("end", "END", {})])
        publish_scenario_version(self.version.id, database=self.database)
        with patch("app.services.scenario_runtime.recognize_text", new=AsyncMock()) as recognize:
            execution = await ScenarioRuntime().execute(self.database, self.version, image_path="original.png", source="TEST")
        self.assertEqual(execution.status, "FAILED")
        recognize.assert_not_awaited()

    async def test_iteration_ocr_uses_each_item_image(self):
        self.graph([
            ("start", "START", {}),
            ("iteration", "ITERATION", {"items": "{{ input.crops }}", "aggregate": "COLLECT_ONLY", "max_iterations": 10, "body_nodes": [{"node_key": "ocr", "node_type": "OCR", "config_json": {"image_path": "{{ input.item.image_path }}"}}], "body_output": {"text": "{{ nodes.ocr.text }}"}}),
            ("end", "END", {"output": {"items": "{{ nodes.iteration.items }}"}}),
        ])
        self.assertTrue(_workflow_preflight(self.database, self.version)["ready"])
        publish_scenario_version(self.version.id, database=self.database)
        with patch("app.services.scenario_runtime.recognize_text", new=AsyncMock(return_value={"status": "COMPLETED", "text": "FU5"})) as recognize:
            execution = await ScenarioRuntime().execute(self.database, self.version, image_path="whole.png", source="TEST", context={"crops": [{"image_path": "first.png"}, {"image_path": "second.png"}]})
        self.assertEqual(execution.status, "COMPLETED", execution.error_message)
        self.assertEqual([call.args[0] for call in recognize.await_args_list], ["first.png", "second.png"])

    def test_invalid_settings_block_publish(self):
        from fastapi import HTTPException

        for config in ({}, {"image_path": "image.png", "min_score": "nan"}, {"image_path": "image.png", "min_score": 2}):
            with self.subTest(config=config), self.assertRaises(HTTPException):
                _validate_ocr_node(SimpleNamespace(config_json=config))


class OcrServiceTests(unittest.TestCase):
    def test_http_upload_and_model_output(self):
        from fastapi.testclient import TestClient
        import numpy as np
        from ocr_service import server

        buffer = BytesIO()
        Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
        engine = SimpleNamespace(predict=lambda *args, **kwargs: [{"rec_texts": ["FU5"], "rec_scores": [0.99], "rec_polys": [np.array([[1, 1], [20, 1], [20, 10], [1, 10]])]}])
        with patch.object(server, "_engine", engine), patch.dict(server.os.environ, {"OCR_SERVICE_API_KEY": "test-key"}), TestClient(server.app) as client:
            self.assertEqual(client.post("/ocr", files={"image": ("a.png", buffer.getvalue())}).status_code, 401)
            response = client.post("/ocr", files={"image": ("a.png", buffer.getvalue())}, headers={"X-OCR-API-Key": "test-key"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["lines"][0]["text"], "FU5")
            invalid = client.post("/ocr", files={"image": ("a.png", b"not an image")}, headers={"X-OCR-API-Key": "test-key"})
            self.assertEqual(invalid.status_code, 400)
