import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.scenarios import (
    NodeCreate,
    ScenarioCreate,
    _validate_preview_rule_contract,
    _workflow_preflight,
    add_node,
    create_scenario,
    delete_edge,
)
from app.db.base import Base
from app.core.config import settings
from app.models.intelligence import InspectionScenarioVersion, ScenarioNode, VlmModelConfig, VisionModel, VisionModelVersion
from app.services.scenario_runtime import _complete_workflow_end_output


class WorkflowPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_preflight_reports_missing_vision_node_without_mutating_draft(self) -> None:
        created = create_scenario(
            ScenarioCreate(name="空流程", category="TEST", mode="WORKFLOW"),
            database=self.database,
        )
        version = self.database.get(InspectionScenarioVersion, created["version_id"])

        result = _workflow_preflight(self.database, version)

        self.assertFalse(result["ready"])
        self.assertTrue(any(item["code"] == "VISION_NODE_REQUIRED" for item in result["diagnostics"]))
        self.assertEqual(version.status, "DRAFT")
        self.assertEqual(len(version.nodes), 2)

    def test_preflight_flags_nonexistent_variable_source_on_the_referencing_node(self) -> None:
        model = VlmModelConfig(
            code="PREFLIGHT_VLM",
            name="Preflight VLM",
            model_name="test-vlm",
            base_url="http://127.0.0.1:8000/v1",
            enabled=True,
        )
        self.database.add(model)
        self.database.commit()
        created = create_scenario(
            ScenarioCreate(name="变量来源检查", category="TEST", mode="WORKFLOW"),
            database=self.database,
        )
        version_id = created["version_id"]
        node = add_node(
            version_id,
            NodeCreate(
                node_key="vlm_check",
                name="VLM 检查",
                node_type="VLM",
                config_json={
                    "vlm_model_id": model.id,
                    "prompt": "判断 {{ nodes.missing.result }} 是否合格",
                },
            ),
            database=self.database,
        )
        version = self.database.get(InspectionScenarioVersion, version_id)

        result = _workflow_preflight(self.database, version)

        issue = next(item for item in result["diagnostics"] if item["code"] == "VARIABLE_SOURCE_MISSING")
        self.assertEqual(issue["node_id"], node["id"])
        self.assertEqual(issue["node_key"], "vlm_check")
        self.assertFalse(result["ready"])

    def test_classification_prediction_survives_custom_end_output(self) -> None:
        previous = {
            "result": "UNCERTAIN",
            "confidence": 0.999992,
            "classification": {"top1_label": "FU5", "top1_confidence": 0.999992},
        }

        output = _complete_workflow_end_output({"res": None}, previous)

        self.assertEqual(output["result"], "UNCERTAIN")
        self.assertEqual(output["predicted_label"], "FU5")
        self.assertEqual(output["classification"]["top1_confidence"], 0.999992)

    def test_missing_end_result_uses_last_quality_decision(self) -> None:
        output = _complete_workflow_end_output(
            {"result": None, "object_count": 2},
            {"result": "NG", "reason": "型号不符"},
        )

        self.assertEqual(output["result"], "NG")
        self.assertEqual(output["reason"], "型号不符")
        self.assertEqual(output["object_count"], 2)

    def test_delete_edge_removes_only_the_selected_draft_connection(self) -> None:
        created = create_scenario(
            ScenarioCreate(name="连线删除测试", category="TEST", mode="WORKFLOW"),
            database=self.database,
        )
        version_id = created["version_id"]
        add_node(
            version_id,
            NodeCreate(node_key="model_1", name="模型", node_type="VISION_MODEL"),
            database=self.database,
        )
        version = self.database.get(InspectionScenarioVersion, version_id)
        selected = next(edge for edge in version.edges if edge.source_node_key == "start")
        remaining_id = next(edge.id for edge in version.edges if edge.source_node_key == "model_1")

        response = delete_edge(version_id, selected.id, database=self.database)

        self.assertTrue(response["deleted"])
        self.assertEqual([edge.id for edge in version.edges], [remaining_id])

    def test_rule_cannot_publish_without_actual_value_source(self) -> None:
        created = create_scenario(
            ScenarioCreate(name="规则来源测试", category="TEST", mode="WORKFLOW"),
            database=self.database,
        )
        add_node(
            created["version_id"],
            NodeCreate(node_key="rule_1", name="分类比较", node_type="RULE", config_json={"actual": "", "expected": "OK"}),
            database=self.database,
        )
        version = self.database.get(InspectionScenarioVersion, created["version_id"])

        result = _workflow_preflight(self.database, version)

        self.assertTrue(any(item["code"] == "RULE_ACTUAL_REQUIRED" for item in result["diagnostics"]))

    def test_s3_upload_node_requires_image_and_server_configuration(self) -> None:
        created = create_scenario(
            ScenarioCreate(name="S3 配置检查", category="TEST", mode="WORKFLOW"),
            database=self.database,
        )
        add_node(
            created["version_id"],
            NodeCreate(node_key="s3_1", name="结果上传", node_type="S3_UPLOAD", config_json={}),
            database=self.database,
        )
        version = self.database.get(InspectionScenarioVersion, created["version_id"])
        original_enabled = settings.scene_result_s3_enabled
        try:
            settings.scene_result_s3_enabled = False
            result = _workflow_preflight(self.database, version)
        finally:
            settings.scene_result_s3_enabled = original_enabled
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("S3_UPLOAD_IMAGE_REQUIRED", codes)
        self.assertIn("S3_UPLOAD_CONFIG_INVALID", codes)

    def test_preflight_warns_on_missing_rule_and_rejects_invalid_end_field(self) -> None:
        with TemporaryDirectory() as directory:
            weights_path = Path(directory) / "best.pt"
            weights_path.write_bytes(b"test weights")
            model = VisionModel(
                code="AI_HARNESS_CLASSIFICATION",
                name="线束分类",
                task_type="YOLO_CLASSIFICATION",
                labels_json=["FU5", "FU6"],
                enabled=True,
            )
            self.database.add(model)
            self.database.flush()
            model_version = VisionModelVersion(
                vision_model_id=model.id,
                version="1.0",
                status="PUBLISHED",
                weights_path=str(weights_path),
            )
            self.database.add(model_version)
            self.database.commit()
            created = create_scenario(
                ScenarioCreate(name="线束分类输出检查", category="TEST", mode="WORKFLOW"),
                database=self.database,
            )
            add_node(
                created["version_id"],
                NodeCreate(
                    node_key="model_1",
                    name="线束分类",
                    node_type="VISION_MODEL",
                    config_json={"model_version_id": model_version.id},
                ),
                database=self.database,
            )
            version = self.database.get(InspectionScenarioVersion, created["version_id"])
            end = next(node for node in version.nodes if node.node_type == "END")
            end.config_json = {"output": {"res": "{{ nodes.model_1.res }}"}}
            self.database.commit()

            invalid = _workflow_preflight(self.database, version)

            self.assertFalse(invalid["ready"])
            self.assertTrue(any(item["code"] == "END_OUTPUT_FIELD_INVALID" for item in invalid["diagnostics"]))
            self.assertTrue(any(item["code"] == "CLASSIFICATION_RULE_MISSING" for item in invalid["diagnostics"]))

            end.config_json = {"output": {"predicted_label": "{{ nodes.model_1.predicted_label }}"}}
            self.database.commit()
            valid = _workflow_preflight(self.database, version)

            self.assertTrue(valid["ready"])
            self.assertEqual(valid["summary"]["warnings"], 1)

    def test_preflight_checks_multi_model_rule_references(self) -> None:
        with TemporaryDirectory() as directory:
            weights_path = Path(directory) / "best.pt"
            weights_path.write_bytes(b"test weights")
            versions = {}
            for task_type, code in (
                ("YOLO_DETECTION", "PREFLIGHT_DETECTOR"),
                ("YOLO_CLASSIFICATION", "PREFLIGHT_CLASSIFIER"),
            ):
                model = VisionModel(
                    code=code,
                    name=code,
                    task_type=task_type,
                    labels_json=["FU5", "FU6"],
                    enabled=True,
                )
                self.database.add(model)
                self.database.flush()
                version = VisionModelVersion(
                    vision_model_id=model.id,
                    version="1.0",
                    status="PUBLISHED",
                    weights_path=str(weights_path),
                )
                self.database.add(version)
                self.database.flush()
                versions[task_type] = version.id
            self.database.commit()
            created = create_scenario(
                ScenarioCreate(name="双模型规则来源检查", category="TEST", mode="WORKFLOW"),
                database=self.database,
            )
            version_id = created["version_id"]
            add_node(
                version_id,
                NodeCreate(
                    node_key="model_1",
                    name="目标检测",
                    node_type="VISION_MODEL",
                    config_json={
                        "model_version_id": versions["YOLO_DETECTION"],
                        "output_mapping": {"res": "{{ response.object_count }}"},
                    },
                ),
                database=self.database,
            )
            add_node(
                version_id,
                NodeCreate(
                    node_key="model_2",
                    name="图像分类",
                    node_type="VISION_MODEL",
                    config_json={"model_version_id": versions["YOLO_CLASSIFICATION"]},
                ),
                database=self.database,
            )
            add_node(
                version_id,
                NodeCreate(
                    node_key="rule_1",
                    name="类别判断",
                    node_type="RULE",
                    config_json={
                        "actual": "{{ nodes.model_1.res }}",
                        "expected": "FU6",
                        "operator": "EQUALS",
                    },
                ),
                database=self.database,
            )
            version = self.database.get(InspectionScenarioVersion, version_id)
            rule = next(node for node in version.nodes if node.node_key == "rule_1")

            mismatched = _workflow_preflight(self.database, version)

            self.assertFalse(mismatched["ready"])
            self.assertTrue(any(
                item["code"] == "RULE_VALUE_TYPE_MISMATCH" and item["node_key"] == "rule_1"
                for item in mismatched["diagnostics"]
            ))
            with self.assertRaises(HTTPException) as preview_error:
                _validate_preview_rule_contract(self.database, version)
            self.assertEqual(preview_error.exception.status_code, 422)
            self.assertIn("目标检测", preview_error.exception.detail)

            rule.config_json = {
                "actual": "{{ nodes.model_2.classification.top1_label }}",
                "expected": "FU6",
                "operator": "EQUALS",
            }
            self.database.commit()
            correct = _workflow_preflight(self.database, version)
            self.assertTrue(correct["ready"])
            _validate_preview_rule_contract(self.database, version)

            rule.config_json = {
                "actual": "{{ nodes.model_2.classification.unknown_field }}",
                "expected": "FU6",
                "operator": "EQUALS",
            }
            self.database.commit()
            invalid = _workflow_preflight(self.database, version)
            self.assertTrue(any(item["code"] == "RULE_OUTPUT_FIELD_INVALID" for item in invalid["diagnostics"]))

            rule.config_json = {
                "actual": "{{ nodes.model_1.object_count }}",
                "expected": "0",
                "operator": "EQUALS",
            }
            self.database.commit()
            numeric = _workflow_preflight(self.database, version)
            self.assertTrue(any(item["code"] == "RULE_NUMBER_OPERATOR_REQUIRED" for item in numeric["diagnostics"]))

            start = next(node for node in version.nodes if node.node_key == "start")
            start.config_json = {
                "inputs": [
                    {"name": "image_path", "type": "IMAGE", "required": True},
                    {"name": "type_name", "type": "TEXT"},
                ]
            }
            rule.config_json = {
                "actual": "{{ nodes.model_1.res }}",
                "expected": "{{ input.type_name }}",
                "operator": "EQUALS",
            }
            self.database.commit()
            dynamic = _workflow_preflight(self.database, version)
            self.assertTrue(any(item["code"] == "RULE_VALUE_TYPE_MISMATCH" for item in dynamic["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
