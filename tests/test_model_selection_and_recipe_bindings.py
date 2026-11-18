import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import configuration, vlm_models, vision_models, scenarios
from app.db.base import Base
from app.models.intelligence import (
    InspectionScenario, InspectionScenarioVersion, RoiScenarioBinding, ScenarioNode,
    VisionModel, VisionModelVersion, VlmModelConfig, VlmModelVersion,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services.model_selection import (
    ModelSelectionError, resolve_vision_version, resolve_vlm_config, snapshot_vlm_config,
)
from app.services.scenario_runtime import _select_published_vision_model, _select_vlm


class ModelSelectionAndRecipeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine, expire_on_commit=False)
        self.temporary = TemporaryDirectory()
        self.weights = Path(self.temporary.name) / "model.pt"
        self.weights.write_bytes(b"test")
        self.model = VisionModel(code="wire", name="线束", task_type="YOLO_DETECTION")
        self.vlm = VlmModelConfig(code="vlm", name="VLM", model_name="first", base_url="http://localhost:8000/v1")
        self.database.add_all([self.model, self.vlm])
        self.database.flush()
        self.first = VisionModelVersion(vision_model_id=self.model.id, version="1", status="PUBLISHED", weights_path=str(self.weights))
        self.database.add(self.first)
        self.database.commit()

    def tearDown(self):
        self.database.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_latest_resolves_at_execution_and_fixed_stays_archived(self):
        latest = {"vision_model_id": self.model.id, "model_version_mode": "LATEST", "model_version_id": self.first.id}
        fixed = {"vision_model_id": self.model.id, "model_version_mode": "FIXED", "model_version_id": self.first.id}
        self.assertEqual(resolve_vision_version(self.database, latest).id, self.first.id)
        second = VisionModelVersion(vision_model_id=self.model.id, version="2", status="DRAFT", weights_path=str(self.weights))
        self.database.add(second)
        self.database.commit()
        self.assertEqual(resolve_vision_version(self.database, latest).id, self.first.id)
        self.first.status = "ARCHIVED"
        second.status = "PUBLISHED"
        self.database.commit()
        self.assertEqual(_select_published_vision_model(self.database, ScenarioNode(name="test", config_json=latest)).version_id, second.id)
        self.assertEqual(_select_published_vision_model(self.database, ScenarioNode(name="test", config_json=fixed)).version_id, self.first.id)
        self.assertEqual(resolve_vision_version(self.database, {"model_version_id": self.first.id}).id, self.first.id)
        self.first.status, second.status = "PUBLISHED", "ARCHIVED"
        self.database.commit()
        self.assertEqual(resolve_vision_version(self.database, latest).id, self.first.id)
        catalog = vision_models.list_published_vision_model_versions(self.database, include_archived=True)
        self.assertEqual({row["version_id"] for row in catalog}, {self.first.id, second.id})

    def test_selection_rejects_wrong_parent_disabled_and_missing_latest(self):
        with self.assertRaises(ModelSelectionError):
            resolve_vision_version(self.database, {"vision_model_id": 999, "model_version_id": self.first.id})
        self.first.status = "DRAFT"
        with self.assertRaises(ModelSelectionError):
            resolve_vision_version(self.database, {"vision_model_id": self.model.id, "model_version_mode": "LATEST"})
        self.model.enabled = False
        self.first.status = "PUBLISHED"
        with self.assertRaises(ModelSelectionError):
            resolve_vision_version(self.database, {"model_version_id": self.first.id})

    def test_vlm_config_snapshots_and_secrets_are_not_exposed(self):
        first = snapshot_vlm_config(self.database, self.vlm)
        self.database.commit()
        self.assertEqual(snapshot_vlm_config(self.database, self.vlm).id, first.id)
        vlm_models.update_vlm_model(self.vlm.id, vlm_models.VlmModelUpdate(model_name="second", temperature=0.4), self.database)
        rows = self.database.scalars(select(VlmModelVersion).order_by(VlmModelVersion.version)).all()
        self.assertEqual(len(rows), 2)
        fixed = {"vlm_model_id": self.vlm.id, "vlm_version_mode": "FIXED", "vlm_version_id": first.id}
        latest = {"vlm_model_id": self.vlm.id, "vlm_version_mode": "LATEST"}
        scene = InspectionScenarioVersion(primary_vlm_model_id=self.vlm.id)
        self.assertEqual(_select_vlm(self.database, scene, ScenarioNode(config_json=fixed)).model_name, "first")
        self.assertEqual(_select_vlm(self.database, scene, ScenarioNode(config_json=latest)).model_name, "second")
        self.assertEqual(resolve_vlm_config(self.database, latest).config_version, 2)
        self.assertNotIn("api_key", str(vlm_models.vlm_versions_catalog(self.database)))
        self.vlm.enabled = False
        with self.assertRaises(ModelSelectionError):
            resolve_vlm_config(self.database, fixed)

    def test_vlm_fixed_rejects_cross_model_or_missing_version(self):
        version = snapshot_vlm_config(self.database, self.vlm)
        other = VlmModelConfig(code="other", name="Other", base_url="http://localhost:8000/v1", model_name="other")
        self.database.add(other)
        self.database.commit()
        with self.assertRaises(ModelSelectionError):
            resolve_vlm_config(self.database, {"vlm_model_id": other.id, "vlm_version_mode": "FIXED", "vlm_version_id": version.id})
        with self.assertRaises(ModelSelectionError):
            resolve_vlm_config(self.database, {"vlm_model_id": other.id, "vlm_version_mode": "FIXED"})

    def test_collection_draft_stays_detect_after_adding_model_nodes(self):
        created = scenarios.create_scenario(scenarios.ScenarioCreate(
            name="采集切换", mode="WORKFLOW", run_mode="COLLECT_ONLY",
        ), self.database)
        original = self.database.get(InspectionScenarioVersion, created["version_id"])
        original.status = "PUBLISHED"
        self.database.commit()
        draft = scenarios.create_scenario_version(created["id"], scenarios.VersionCreate(
            version="2", source_version_id=original.id,
        ), self.database)
        scenarios.update_scenario_version(draft["id"], scenarios.VersionUpdate(
            definition_json={"run_mode": "DETECT"},
        ), self.database)
        revision = snapshot_vlm_config(self.database, self.vlm)
        self.database.commit()
        for key, kind, config in (
            ("model", "VISION_MODEL", {"vision_model_id": self.model.id, "model_version_mode": "LATEST"}),
            ("vlm", "VLM", {"vlm_model_id": self.vlm.id, "vlm_version_mode": "FIXED", "vlm_version_id": revision.id, "prompt": "检查图片"}),
        ):
            scenarios.add_node(draft["id"], scenarios.NodeCreate(
                node_key=key, name=key, node_type=kind, config_json=config,
            ), self.database)
        self.database.expire_all()
        loaded = scenarios.get_scenario_version(draft["id"], self.database)
        self.assertEqual(loaded["definition_json"]["run_mode"], "DETECT")
        self.assertEqual(len(loaded["nodes"]), 4)
        self.assertEqual(self.database.get(InspectionScenarioVersion, original.id).definition_json["run_mode"], "COLLECT_ONLY")
        for node in self.database.get(InspectionScenarioVersion, draft["id"]).nodes:
            if node.node_type == "VISION_MODEL":
                scenarios._validate_published_vision_model_node(self.database, node)
            if node.node_type == "VLM":
                scenarios._validate_vlm_node(self.database, node)

    def test_recipe_details_include_bindings_without_opening_roi(self):
        product, station = Product(code="P", name="P"), Station(code="S", name="S")
        scene = InspectionScenario(code="SCENE", name="线束检测", mode="WORKFLOW")
        self.database.add_all([product, station, scene])
        self.database.flush()
        recipe = Recipe(code="R", name="R", product_id=product.id, station_id=station.id, status="DRAFT")
        version = InspectionScenarioVersion(scenario_id=scene.id, version="1", status="PUBLISHED")
        self.database.add_all([recipe, version])
        self.database.flush()
        roi = RegionOfInterest(recipe_id=recipe.id, code="ROI1", name="ROI1", object_type="WIRE", x_ratio=0, y_ratio=0, width_ratio=0.5, height_ratio=0.5)
        self.database.add(roi)
        self.database.flush()
        binding = RoiScenarioBinding(roi_id=roi.id, scenario_version_id=version.id, input_mapping_json={"type": "FU5"})
        self.database.add(binding)
        self.database.commit()
        for status in ("DRAFT", "PUBLISHED"):
            recipe.status = status
            self.database.commit()
            detail = configuration.recipe_detail(recipe.id, self.database)
            self.assertEqual(detail["rois"][0]["scenario_binding"]["scenario_name"], scene.name)
            self.assertEqual(detail["rois"][0]["scenario_binding"]["input_mapping_json"], {"type": "FU5"})
            self.assertEqual(configuration.list_recipes(self.database)[0]["scenarios"][0]["scenario_id"], scene.id)
        roi.is_deleted = True
        self.database.commit()
        self.assertEqual(configuration.list_recipes(self.database)[0]["scenarios"], [])
        roi.is_deleted = False
        binding.is_deleted = True
        self.database.commit()
        self.assertEqual(configuration.list_recipes(self.database)[0]["scenarios"], [])
        binding.is_deleted = False
        binding.enabled = False
        self.database.commit()
        self.assertIsNone(configuration.recipe_detail(recipe.id, self.database)["rois"][0]["scenario_binding"])
        self.assertEqual(configuration.list_recipes(self.database)[0]["scenarios"], [])


if __name__ == "__main__":
    unittest.main()
