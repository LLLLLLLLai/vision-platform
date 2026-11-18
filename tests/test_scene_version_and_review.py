import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import scenarios
from app.db.base import Base
from app.db.session import get_db
from app.models.intelligence import (
    Dataset, InspectionScenario, InspectionScenarioVersion, RoiScenarioBinding,
    ScenarioExecution, ScenarioExecutionReview, VlmModelConfig,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station


class SceneVersionAndReviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(scenarios.router, prefix="/scenarios")

        def database_session():
            with Session(self.engine) as database:
                yield database

        app.dependency_overrides[get_db] = database_session
        self.client = TestClient(app)
        with Session(self.engine) as database:
            model = VlmModelConfig(code="test", name="test", model_name="test", base_url="http://localhost:8000/v1")
            database.add(model)
            database.commit()
            self.model_id = model.id

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def create_scene(self, mode="VLM_DIRECT", version="1.0"):
        response = self.client.post("/scenarios", json={
            "name": "测试", "mode": mode, "version": version,
            "primary_vlm_model_id": self.model_id, "prompt_template": "检查图中的线束是否存在",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_auto_versions_carry_and_clone_history_without_changing_source(self):
        for mode in ("VLM_DIRECT", "WORKFLOW"):
            with self.subTest(mode=mode):
                scene = self.create_scene(mode, "1.8")
                for expected in ("1.9", "2.0", "2.1"):
                    response = self.client.post(f"/scenarios/{scene['id']}/versions", json={
                        "source_version_id": scene["version_id"],
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["version"], expected)
                    self.assertEqual(response.json()["status"], "DRAFT")
                    detail = self.client.get(f"/scenarios/versions/{response.json()['id']}").json()
                    self.assertEqual(detail["prompt_template"], "检查图中的线束是否存在")
                    if mode == "WORKFLOW":
                        self.assertEqual({node["node_type"] for node in detail["nodes"]}, {"START", "END"})
                original = self.client.get(f"/scenarios/versions/{scene['version_id']}").json()
                self.assertEqual(original["version"], "1.8")

    def test_legacy_version_names_and_explicit_api_versions_remain_supported(self):
        scene = self.create_scene(version="1.0-draft-1-draft-2")
        response = self.client.post(f"/scenarios/{scene['id']}/versions", json={"version": " "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], "1.1")
        explicit = self.client.post(f"/scenarios/{scene['id']}/versions", json={"version": "v1.9"})
        self.assertEqual(explicit.status_code, 200, explicit.text)
        duplicate = self.client.post(f"/scenarios/{scene['id']}/versions", json={"version": "v1.9"})
        self.assertEqual(duplicate.status_code, 409)
        next_version = self.client.post(f"/scenarios/{scene['id']}/versions", json={})
        self.assertEqual(next_version.json()["version"], "2.0")

    def test_publishing_follows_roi_and_dataset_but_not_historical_execution(self):
        scene = self.create_scene()
        published = self.client.post(f"/scenarios/versions/{scene['version_id']}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        with Session(self.engine) as database:
            product, station = Product(code="P", name="P"), Station(code="S", name="S")
            database.add_all([product, station])
            database.flush()
            recipe = Recipe(code="R", name="R", product_id=product.id, station_id=station.id, status="PUBLISHED")
            database.add(recipe)
            database.flush()
            roi = RegionOfInterest(recipe_id=recipe.id, code="ROI1", name="ROI1", object_type="WIRE",
                                   x_ratio=0, y_ratio=0, width_ratio=0.5, height_ratio=0.5)
            database.add(roi)
            database.flush()
            binding = RoiScenarioBinding(roi_id=roi.id, scenario_version_id=scene["version_id"], input_mapping_json={"type": "FU5"})
            dataset = Dataset(code="D", name="D", collection_scenario_version_id=scene["version_id"])
            execution = ScenarioExecution(scenario_version_id=scene["version_id"], result="OK", status="COMPLETED")
            database.add_all([binding, dataset, execution])
            database.commit()
            binding_id, dataset_id, execution_id = binding.id, dataset.id, execution.id
        draft = self.client.post(f"/scenarios/{scene['id']}/versions", json={"source_version_id": scene["version_id"]}).json()
        with Session(self.engine) as database:
            self.assertEqual(database.get(RoiScenarioBinding, binding_id).scenario_version_id, scene["version_id"])
            self.assertEqual(database.get(InspectionScenario, scene["id"]).published_version_id, scene["version_id"])
        published = self.client.post(f"/scenarios/versions/{draft['id']}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["rebound_roi_count"], 1)
        with Session(self.engine) as database:
            self.assertEqual(database.get(RoiScenarioBinding, binding_id).scenario_version_id, draft["id"])
            self.assertEqual(database.get(RoiScenarioBinding, binding_id).input_mapping_json, {"type": "FU5"})
            self.assertEqual(database.get(Dataset, dataset_id).collection_scenario_version_id, draft["id"])
            self.assertEqual(database.get(ScenarioExecution, execution_id).scenario_version_id, scene["version_id"])
            self.assertEqual(database.get(InspectionScenarioVersion, scene["version_id"]).status, "ARCHIVED")

    def test_manual_verdicts_persist_without_overwriting_model_results(self):
        scene = self.create_scene()
        with Session(self.engine) as database:
            execution = ScenarioExecution(scenario_version_id=scene["version_id"], result="NG", status="COMPLETED")
            database.add(execution)
            database.flush()
            database.add(ScenarioExecutionReview(execution_id=execution.id, verdict="OK", status="COMPLETED"))
            database.commit()
            execution_id = execution.id
        for verdict in ("OK", "NG", "UNCERTAIN", "OK"):
            response = self.client.put(f"/scenarios/executions/{execution_id}/manual-review", json={"verdict": verdict, "note": f"人工确认 {verdict}"})
            self.assertEqual(response.status_code, 200, response.text)
            with Session(self.engine) as database:
                saved = database.get(ScenarioExecution, execution_id)
                self.assertEqual(saved.review.manual_verdict, verdict)
                self.assertEqual(saved.review.manual_note, f"人工确认 {verdict}")
                self.assertIsNotNone(saved.review.manually_reviewed_at)
                self.assertEqual(saved.result, "NG")
                self.assertEqual(saved.review.verdict, "OK")
                self.assertEqual(len(database.scalars(select(ScenarioExecutionReview)).all()), 1)
        invalid = self.client.put(f"/scenarios/executions/{execution_id}/manual-review", json={"verdict": "invalid"})
        self.assertEqual(invalid.status_code, 400)

    def test_manual_review_can_be_saved_without_vlm_review(self):
        scene = self.create_scene()
        with Session(self.engine) as database:
            execution = ScenarioExecution(scenario_version_id=scene["version_id"], result="OK", status="COMPLETED")
            database.add(execution)
            database.commit()
            execution_id = execution.id
        for verdict in ("NG", "UNCERTAIN"):
            response = self.client.put(f"/scenarios/executions/{execution_id}/manual-review", json={"verdict": verdict})
            self.assertEqual(response.status_code, 200, response.text)
        with Session(self.engine) as database:
            review = database.get(ScenarioExecution, execution_id).review
            self.assertEqual(review.manual_verdict, "UNCERTAIN")
            self.assertIsNone(review.verdict)
