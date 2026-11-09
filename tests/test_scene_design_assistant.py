import copy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import scenarios
from app.db.base import Base
from app.db.session import get_db
from app.models.intelligence import InspectionScenario, InspectionScenarioVersion, VlmModelConfig, VisionModel, VisionModelVersion
from app.core.config import settings
from app.services.openai_compatible_vlm import VlmRequestError
from app.services.scene_design_assistant import DESIGN_SYSTEM_PROMPT, vlm_client


class SceneDesignAssistantTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.directory = TemporaryDirectory()
        self.path_patch = patch.object(scenarios, "PROJECT_ROOT", Path(self.directory.name))
        self.path_patch.start()
        self.model = VlmModelConfig(code="assistant", name="设计助手", base_url="http://test/v1", model_name="test-vlm")
        self.scene = InspectionScenario(code="test", name="线束场景", mode="VLM_DIRECT")
        self.database.add_all([self.model, self.scene])
        self.database.commit()
        self.original = InspectionScenarioVersion(scenario_id=self.scene.id, version="1", status="PUBLISHED", prompt_template="原始提示词")
        self.database.add(self.original)
        self.database.commit()
        self.app = FastAPI()
        self.app.include_router(scenarios.router, prefix="/api/v1/scenarios")
        self.app.dependency_overrides[get_db] = lambda: self.database
        self.client = TestClient(self.app)
        self.endpoint = f"/api/v1/scenarios/{self.scene.id}/design-assistant"
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "orange").save(buffer, format="PNG")
        self.image = buffer.getvalue()
        self.direct = {
            "mode": "VLM_DIRECT", "explanation": "校验线束文字，模糊按NG，需要测试验证。",
            "primary_vlm_model_id": self.model.id,
            "prompt_template": "识别线束文字，与{{ input.expected_text }}比较。返回JSON，result仅OK或NG。",
            "input_fields": [{"name": "expected_text", "label": "校验文字", "type": "TEXT", "required": True, "default": "FU5"}],
            "nodes": [], "edges": [],
        }

    def tearDown(self):
        self.client.close()
        self.path_patch.stop()
        self.database.close()
        self.engine.dispose()
        self.directory.cleanup()

    def generate(self, **data):
        return self.client.post(self.endpoint, data={
            "vlm_model_id": str(self.model.id), "requirements": "result只能返回OK或NG", **data,
        }, files={"image": ("sample.png", self.image, "image/png")})

    def workflow(self):
        self.scene.mode = "WORKFLOW"
        self.database.commit()
        return {
            "mode": "WORKFLOW", "explanation": "通过VLM检测标签文字。",
            "input_fields": self.direct["input_fields"],
            "nodes": [
                {"node_key": "start", "name": "开始", "node_type": "START", "config_json": {}},
                {"node_key": "inspect", "name": "检测", "node_type": "VLM", "config_json": {
                    "vlm_model_id": self.model.id, "prompt": self.direct["prompt_template"], "image_path": "{{ input.image_path }}",
                }},
                {"node_key": "end", "name": "结束", "node_type": "END", "config_json": {
                    "output": {"result": "{{ nodes.inspect.result }}", "reason": "{{ nodes.inspect.reason }}"},
                }},
            ],
            "edges": [{"source_node_key": "start", "target_node_key": "inspect"}, {"source_node_key": "inspect", "target_node_key": "end"}],
        }

    def test_generate_apply_and_preview_direct_draft_without_changing_published(self):
        with patch.object(vlm_client, "judge", new=AsyncMock(return_value=self.direct)) as mock:
            response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.database.scalar(select(func.count()).select_from(InspectionScenarioVersion)), 1)
        call = mock.call_args.kwargs
        self.assertEqual(call["system_prompt"], DESIGN_SYSTEM_PROMPT)
        self.assertFalse(Path(call["image_path"]).exists())
        applied = self.client.post(f"{self.endpoint}/apply", json=response.json()["design"])
        self.assertEqual(applied.status_code, 200, applied.text)
        version_id = applied.json()["version_id"]
        self.assertEqual(applied.json()["status"], "DRAFT")
        self.database.refresh(self.original)
        self.assertEqual(self.original.status, "PUBLISHED")
        self.assertEqual(self.original.prompt_template, "原始提示词")
        with patch.object(vlm_client, "judge", new=AsyncMock(return_value={"result": "OK", "confidence": 0.95, "reason": "文字一致"})) as mock:
            tested = self.client.post(f"/api/v1/scenarios/versions/{version_id}/preview-upload", data={"context_json": '{"expected_text":"FU5"}'}, files={"image": ("test.png", self.image, "image/png")})
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(tested.json()["result"], "OK")
        self.assertIn("FU5", mock.call_args.kwargs["prompt"])

    def test_workflow_generation_apply_preflight_and_execute(self):
        design = self.workflow()
        with patch.object(vlm_client, "judge", new=AsyncMock(return_value=design)):
            response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        applied = self.client.post(f"{self.endpoint}/apply", json=response.json()["design"])
        self.assertEqual(applied.status_code, 200, applied.text)
        version_id = applied.json()["version_id"]
        preflight = self.client.get(f"/api/v1/scenarios/versions/{version_id}/preflight")
        self.assertTrue(preflight.json()["ready"], preflight.text)
        with patch.object(vlm_client, "judge", new=AsyncMock(return_value={"result": "NG", "confidence": 0.9, "reason": "文字不一致"})) as mock:
            tested = self.client.post(f"/api/v1/scenarios/versions/{version_id}/preview-upload", data={"context_json": '{"expected_text":"FU6"}'}, files={"image": ("test.png", self.image, "image/png")})
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(tested.json()["result"], "NG")
        self.assertIn("FU6", mock.call_args.kwargs["prompt"])
        self.assertEqual(len(tested.json()["output_json"]["traces"]), 3)

    def test_wrong_detection_response_is_repaired_preserving_requirements_and_history(self):
        conversation = {"messages": [{"role": "user", "content": "必须检查线束颜色"}], "previous_design": self.direct}
        with patch.object(vlm_client, "judge", new=AsyncMock(side_effect=[{"result": "OK"}, self.direct])) as mock:
            response = self.generate(conversation_json=json.dumps(conversation))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock.call_count, 2)
        for call in mock.call_args_list:
            self.assertEqual(call.kwargs["context"]["requirements"], "result只能返回OK或NG")
            self.assertEqual(call.kwargs["context"]["conversation"]["messages"][0]["content"], "必须检查线束颜色")
        self.assertTrue(mock.call_args.kwargs["context"]["validation_feedback"])

    def test_invalid_generation_does_not_create_versions(self):
        with patch.object(vlm_client, "judge", new=AsyncMock(return_value={"result": "NG"})):
            response = self.generate()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.database.scalar(select(func.count()).select_from(InspectionScenarioVersion)), 1)

    def test_apply_revalidates_models_variables_and_graph(self):
        missing_model = copy.deepcopy(self.direct)
        missing_model["primary_vlm_model_id"] = 99999
        missing_variable = copy.deepcopy(self.direct)
        missing_variable["prompt_template"] = "检查{{ input.not_defined }}"
        for design in [missing_model, missing_variable]:
            response = self.client.post(f"{self.endpoint}/apply", json=design)
            self.assertEqual(response.status_code, 422, response.text)
        workflow = self.workflow()
        invalid_graph = copy.deepcopy(workflow)
        invalid_graph["edges"].append({"source_node_key": "ghost", "target_node_key": "end"})
        duplicated_edge = copy.deepcopy(workflow)
        duplicated_edge["edges"].append(workflow["edges"][0])
        unsafe_path = copy.deepcopy(workflow)
        unsafe_path["nodes"][1]["config_json"]["image_path"] = "C:/private.png"
        for design in [invalid_graph, duplicated_edge, unsafe_path]:
            response = self.client.post(f"{self.endpoint}/apply", json=design)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.database.scalar(select(func.count()).select_from(InspectionScenarioVersion)), 1)

    def test_non_image_and_disabled_model_are_rejected(self):
        response = self.client.post(self.endpoint, data={"vlm_model_id": self.model.id, "requirements": "检查线束"}, files={"image": ("bad.png", b"not an image", "image/png")})
        self.assertEqual(response.status_code, 422)
        self.model.enabled = False
        self.database.commit()
        self.assertEqual(self.generate().status_code, 422)

    def test_timeout_returns_diagnostic_without_creating_a_draft(self):
        with patch.object(vlm_client, "judge", new=AsyncMock(side_effect=VlmRequestError("服务请求超时"))):
            response = self.generate()
        self.assertEqual(response.status_code, 502)
        self.assertIn("超时", response.json()["detail"])
        self.assertEqual(self.database.scalar(select(func.count()).select_from(InspectionScenarioVersion)), 1)

    def test_model_node_followed_by_rule_uses_class_label(self):
        design = self.workflow()
        weights = Path(self.directory.name) / "classification.pt"
        weights.write_bytes(b"test-only")
        model = VisionModel(code="labels", name="标签分类", task_type="YOLO_CLASSIFICATION", labels_json=["FU5", "FU6"])
        self.database.add(model)
        self.database.flush()
        version = VisionModelVersion(vision_model_id=model.id, version="1", status="PUBLISHED", weights_path=str(weights))
        self.database.add(version)
        self.database.commit()
        design["nodes"][1] = {"node_key": "classify", "name": "标签分类", "node_type": "VISION_MODEL", "config_json": {
            "model_version_id": version.id, "image_path": "{{ input.image_path }}", "confidence": 0.5,
        }}
        design["nodes"].insert(2, {"node_key": "check", "name": "比较文字", "node_type": "RULE", "config_json": {
            "actual": "{{ nodes.classify.predicted_label }}", "operator": "EQUALS", "expected": "{{ input.expected_text }}",
        }})
        design["nodes"][-1]["config_json"] = {"output": {"result": "{{ nodes.check.result }}"}}
        keys = [node["node_key"] for node in design["nodes"]]
        design["edges"] = [{"source_node_key": source, "target_node_key": target} for source, target in zip(keys, keys[1:])]
        applied = self.client.post(f"{self.endpoint}/apply", json=design)
        self.assertEqual(applied.status_code, 200, applied.text)
        version_id = applied.json()["version_id"]
        with patch.object(settings, "yolo_inference_mode", "LOCAL"), patch("app.services.scenario_runtime.run_yolo_inference", return_value={"predicted_label": "FU5", "confidence": 0.99}):
            for expected, verdict in [("FU5", "OK"), ("FU6", "NG")]:
                response = self.client.post(f"/api/v1/scenarios/versions/{version_id}/preview-upload", data={"context_json": json.dumps({"expected_text": expected})}, files={"image": ("test.png", self.image, "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["result"], verdict)

    def test_bad_config_shapes_are_validation_errors_not_server_errors(self):
        workflow = self.workflow()
        workflow["nodes"][1]["config_json"]["vlm_model_id"] = []
        response = self.client.post(f"{self.endpoint}/apply", json=workflow)
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
