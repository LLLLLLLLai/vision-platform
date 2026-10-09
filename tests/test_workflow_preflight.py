import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.scenarios import (
    NodeCreate,
    ScenarioCreate,
    _workflow_preflight,
    add_node,
    create_scenario,
)
from app.db.base import Base
from app.models.intelligence import InspectionScenarioVersion, VlmModelConfig


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


if __name__ == "__main__":
    unittest.main()
