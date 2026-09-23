import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, selectinload

from app.api.routes.scenarios import (
    EdgeCreate,
    EdgeUpdate,
    NodeCreate,
    NodeUpdate,
    RoiBindingRequest,
    ScenarioCreate,
    _scene_input_fields,
    add_edge,
    add_node,
    bind_roi_to_scenario,
    create_scenario,
    get_scenario_version,
    publish_scenario_version,
    update_edge,
    update_node,
)
from app.db.base import Base
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    RoiScenarioBinding,
    ScenarioEdge,
    ScenarioNode,
    VlmModelConfig,
)
from app.models.recipe import Recipe, RecipeFeatureAnchor, RegionOfInterest
from app.models.system import Product, Station
from app.services import scenario_runtime as runtime_module
from app.services.scenario_runtime import ScenarioRuntime, ScenarioRuntimeError


class ScenarioCreateValidationTests(unittest.TestCase):
    def test_normalizes_create_form_values(self) -> None:
        payload = ScenarioCreate.model_validate(
            {
                "code": "  AI_HARNESS_LOCK  ",
                "name": "  AI 检测线束锁付  ",
                "category": "  HARNESS  ",
                "mode": "  VLM_DIRECT  ",
                "description": "   ",
                "version": "  1.0  ",
                "primary_vlm_model_id": "",
                "review_vlm_model_id": "  ",
            }
        )

        self.assertEqual(payload.code, "AI_HARNESS_LOCK")
        self.assertEqual(payload.name, "AI 检测线束锁付")
        self.assertEqual(payload.category, "HARNESS")
        self.assertEqual(payload.mode, "VLM_DIRECT")
        self.assertEqual(payload.version, "1.0")
        self.assertIsNone(payload.description)
        self.assertIsNone(payload.primary_vlm_model_id)
        self.assertIsNone(payload.review_vlm_model_id)

    def test_creates_scene_and_returns_clear_duplicate_code_error(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        payload = ScenarioCreate(
            code="AI_HARNESS_LOCK",
            name="AI 检测线束锁付",
            category="HARNESS",
            mode="WORKFLOW",
        )

        result = create_scenario(payload, database=database)

        self.assertEqual(result["status"], "DRAFT")
        self.assertIsNotNone(result["id"])
        version = database.get(InspectionScenarioVersion, result["version_id"])
        self.assertEqual([node.node_key for node in version.nodes], ["start", "end"])
        with self.assertRaises(HTTPException) as context:
            create_scenario(
                ScenarioCreate(
                    code="  AI_HARNESS_LOCK  ",
                    name="重复场景",
                    category="HARNESS",
                    mode="WORKFLOW",
                ),
                database=database,
            )
        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("AI_HARNESS_LOCK", str(context.exception.detail))
        database.close()
        engine.dispose()

    def test_direct_vlm_scene_allows_empty_draft_before_designer_configuration(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)

        created = create_scenario(
            ScenarioCreate(
                name="直接 VLM 检测",
                category="HARNESS",
                mode="VLM_DIRECT",
            ),
            database=database,
        )
        self.assertEqual(created["status"], "DRAFT")
        with self.assertRaises(HTTPException) as context:
            publish_scenario_version(created["version_id"], database=database)
        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("主 VLM", str(context.exception.detail))
        database.close()
        engine.dispose()


class WorkflowCanvasTests(unittest.TestCase):
    def test_canvas_nodes_edges_and_publish_flow(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        vlm = VlmModelConfig(
            code="CANVAS_VLM",
            name="Canvas VLM",
            base_url="http://vlm.test/v1",
            model_name="canvas-vlm",
        )
        database.add(vlm)
        database.flush()
        created = create_scenario(
            ScenarioCreate(
                name="画布流程",
                category="HARNESS",
                mode="WORKFLOW",
            ),
            database=database,
        )
        version_id = created["version_id"]

        node = add_node(
            version_id,
            NodeCreate(
                node_key="vlm_check",
                name="线束检查",
                node_type="VLM",
                config_json={
                    "vlm_model_id": vlm.id,
                    "prompt": "只输出 OK、NG 或 UNCERTAIN。",
                },
                canvas_x=360,
                canvas_y=150,
                auto_connect=False,
            ),
            database=database,
        )
        version = get_scenario_version(version_id, database=database)
        self.assertEqual(version["edges"], [])

        update_node(
            version_id,
            node["id"],
            NodeUpdate(name="线束锁付检查", canvas_x=420, canvas_y=180),
            database=database,
        )
        first_edge = add_edge(
            version_id,
            EdgeCreate(source_node_key="start", target_node_key="vlm_check"),
            database=database,
        )
        second_edge = add_edge(
            version_id,
            EdgeCreate(source_node_key="vlm_check", target_node_key="end"),
            database=database,
        )
        update_edge(
            version_id,
            second_edge["id"],
            EdgeUpdate(mapping_json={"branch": "true"}),
            database=database,
        )

        version = get_scenario_version(version_id, database=database)
        self.assertEqual(len(version["edges"]), 2)
        edited_node = next(item for item in version["nodes"] if item["id"] == node["id"])
        self.assertEqual(edited_node["name"], "线束锁付检查")
        self.assertEqual(first_edge["created"], True)
        self.assertEqual(publish_scenario_version(version_id, database=database)["status"], "PUBLISHED")
        database.close()
        engine.dispose()

    def test_referenced_vlm_node_configuration_is_persisted_and_publishable(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        vlm = VlmModelConfig(
            code="REFERENCE_VLM",
            name="Reference VLM",
            base_url="http://vlm.test/v1",
            model_name="reference-vlm",
        )
        reference_scene = InspectionScenario(
            code="PUBLISHED_DIRECT_REFERENCE",
            name="已发布 VLM 场景",
            mode="VLM_DIRECT",
        )
        database.add_all((vlm, reference_scene))
        database.flush()
        reference_version = InspectionScenarioVersion(
            scenario_id=reference_scene.id,
            version="1.0",
            status="PUBLISHED",
            primary_vlm_model_id=vlm.id,
            prompt_template="检查 {{ params.expected_text }}",
        )
        database.add(reference_version)
        database.commit()

        workflow = create_scenario(
            ScenarioCreate(name="引用 VLM 流程", category="HARNESS", mode="WORKFLOW"),
            database=database,
        )
        version_id = workflow["version_id"]
        node = add_node(
            version_id,
            NodeCreate(
                node_key="vlm_reference",
                name="引用场景检测",
                node_type="VLM",
                config_json={"vlm_mode": "CUSTOM"},
                auto_connect=False,
            ),
            database=database,
        )
        saved = update_node(
            version_id,
            node["id"],
            NodeUpdate(
                config_json={
                    "vlm_mode": "SCENE",
                    "referenced_scenario_version_id": reference_version.id,
                    "input_mapping": {"expected_text": "{{ input.expected_text }}"},
                    "output_mapping": {"result": "{{ response.result }}"},
                }
            ),
            database=database,
        )
        self.assertEqual(saved["config_json"]["vlm_mode"], "SCENE")
        self.assertEqual(
            saved["config_json"]["referenced_scenario_version_id"], reference_version.id
        )

        add_edge(
            version_id,
            EdgeCreate(source_node_key="start", target_node_key="vlm_reference"),
            database=database,
        )
        add_edge(
            version_id,
            EdgeCreate(source_node_key="vlm_reference", target_node_key="end"),
            database=database,
        )
        self.assertEqual(publish_scenario_version(version_id, database=database)["status"], "PUBLISHED")
        database.close()
        engine.dispose()


class RecipeSceneBindingAndFeatureAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="PDU", name="PDU")
        station = Station(code="ST01", name="Station")
        self.database.add_all((product, station))
        self.database.flush()
        self.recipe = Recipe(
            code="L01_PDU_AS15_CAM01_SHOT01",
            name="配方",
            product_id=product.id,
            station_id=station.id,
        )
        self.database.add(self.recipe)
        self.database.flush()
        self.roi = RegionOfInterest(
            recipe_id=self.recipe.id,
            code="ROI_OCR",
            name="线束文字",
            object_type="HARNESS",
            x_ratio=0.1,
            y_ratio=0.1,
            width_ratio=0.2,
            height_ratio=0.2,
        )
        self.database.add(self.roi)
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_roi_binding_only_accepts_declared_non_image_scene_fields(self) -> None:
        scene = InspectionScenario(
            code="OCR_CHECK",
            name="OCR 检查",
            mode="VLM_DIRECT",
        )
        self.database.add(scene)
        self.database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scene.id,
            version="1.0",
            status="PUBLISHED",
            input_schema_json={
                "fields": [
                    {"name": "image_path", "label": "检测图片", "type": "IMAGE"},
                    {"name": "ocr_text", "label": "期望文字", "type": "TEXT"},
                ]
            },
        )
        self.database.add(version)
        self.database.commit()

        self.assertEqual(_scene_input_fields(version), [{
            "name": "ocr_text",
            "label": "期望文字",
            "type": "TEXT",
        }])
        result = bind_roi_to_scenario(
            self.roi.id,
            RoiBindingRequest(
                scenario_version_id=version.id,
                input_mapping_json={"ocr_text": "FUS"},
            ),
            database=self.database,
        )
        self.assertEqual(result["input_mapping_json"], {"ocr_text": "FUS"})

        with self.assertRaises(HTTPException) as context:
            bind_roi_to_scenario(
                self.roi.id,
                RoiBindingRequest(
                    scenario_version_id=version.id,
                    input_mapping_json={"image_path": "not-allowed"},
                ),
                database=self.database,
            )
        self.assertEqual(context.exception.status_code, 422)

    def test_recipe_feature_anchor_is_independent_from_quality_roi(self) -> None:
        anchor = RecipeFeatureAnchor(
            recipe_id=self.recipe.id,
            code="ANCHOR_SCREW",
            name="固定螺钉",
            x_ratio=0.3,
            y_ratio=0.3,
            width_ratio=0.1,
            height_ratio=0.1,
        )
        self.database.add(anchor)
        self.database.commit()
        self.database.refresh(self.recipe)

        self.assertIsNotNone(self.recipe.feature_anchor)
        self.assertEqual(self.recipe.feature_anchor.code, "ANCHOR_SCREW")
        self.assertFalse(self.roi.alignment_anchor)


class InspectionScenarioRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.primary_model = VlmModelConfig(
            code="PRIMARY_VLM",
            name="Primary",
            base_url="http://vlm.test/v1",
            model_name="primary",
        )
        self.review_model = VlmModelConfig(
            code="REVIEW_VLM",
            name="Review",
            base_url="http://vlm.test/v1",
            model_name="review",
        )
        self.database.add_all((self.primary_model, self.review_model))
        self.database.flush()
        self.scene = InspectionScenario(
            code="HARNESS_LOCK",
            name="线束锁付",
            mode="VLM_DIRECT",
        )
        self.database.add(self.scene)
        self.database.flush()
        self.version = InspectionScenarioVersion(
            scenario_id=self.scene.id,
            version="1.0",
            status="PUBLISHED",
            prompt_template="检查 {{ input.roi_name }}",
            primary_vlm_model_id=self.primary_model.id,
            review_vlm_model_id=self.review_model.id,
        )
        self.database.add(self.version)
        self.database.commit()
        self.version = self.database.query(InspectionScenarioVersion).options(
            selectinload(InspectionScenarioVersion.scenario)
        ).filter_by(id=self.version.id).one()
        self.original_judge = runtime_module.vlm_client.judge

        async def fake_judge(model, **_kwargs):
            if model.id == self.review_model.id:
                return {"result": "NG", "confidence": 0.91, "reason": "锁付缺失"}
            return {"result": "OK", "confidence": 0.96, "reason": "锁付可见"}

        runtime_module.vlm_client.judge = fake_judge

    async def asyncTearDown(self) -> None:
        runtime_module.vlm_client.judge = self.original_judge
        self.database.close()
        self.engine.dispose()

    async def test_published_direct_scene_keeps_raw_and_review_facts_separate(self) -> None:
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "roi.jpg"
            Image.new("RGB", (32, 32), "white").save(image_path)
            runtime = ScenarioRuntime()
            execution = await runtime.execute(
                self.database,
                self.version,
                image_path=str(image_path),
                source="TEST",
                context={"roi_name": "线束端子"},
            )
            review = await runtime.review(self.database, execution)

        self.assertEqual(execution.result, "OK")
        self.assertEqual(execution.output_json["result"]["reason"], "锁付可见")
        self.assertEqual(review.verdict, "NG")
        self.assertEqual(execution.result, "OK")

    async def test_draft_scene_cannot_run(self) -> None:
        self.version.status = "DRAFT"
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "roi.jpg"
            Image.new("RGB", (32, 32), "white").save(image_path)
            with self.assertRaises(ScenarioRuntimeError):
                await ScenarioRuntime().execute(
                    self.database,
                    self.version,
                    image_path=str(image_path),
                    source="TEST",
                )

    async def test_workflow_parameter_mappings_are_available_to_later_nodes(self) -> None:
        scene = InspectionScenario(
            code="WORKFLOW_PARAMETER_MAPPING",
            name="流程参数映射",
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
                config_json={"inputs": [{"name": "expected_text", "label": "期望文字", "type": "TEXT"}]},
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="vlm_1",
                name="文字检测",
                node_type="VLM",
                sort_order=1,
                config_json={
                    "vlm_model_id": self.primary_model.id,
                    "prompt": "请检查 {{ params.expected_text }}",
                    "input_mapping": {"expected_text": "{{ input.expected_text }}"},
                    "output_mapping": {"checked_text": "{{ response.reason }}"},
                },
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="rule_1",
                name="文字规则",
                node_type="RULE",
                sort_order=2,
                config_json={
                    "actual": "{{ params.actual_value }}",
                    "expected": "{{ params.expected_value }}",
                    "operator": "EQUALS",
                    "input_mapping": {
                        "actual_value": "{{ nodes.vlm_1.checked_text }}",
                        "expected_value": "{{ input.expected_text }}",
                    },
                    "output_mapping": {"rule_actual": "{{ response.actual }}"},
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
                        "result": "{{ nodes.rule_1.result }}",
                        "checked_text": "{{ nodes.rule_1.rule_actual }}",
                        "score": "{{ nodes.vlm_1.confidence }}",
                    }
                },
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="start",
                target_node_key="vlm_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="vlm_1",
                target_node_key="rule_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="rule_1",
                target_node_key="end",
            ),
        ))
        self.database.commit()
        version = self.database.query(InspectionScenarioVersion).options(
            selectinload(InspectionScenarioVersion.scenario),
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
        ).filter_by(id=version.id).one()

        captured: dict[str, object] = {}

        async def mapped_judge(_model, *, prompt, context, **_kwargs):
            captured["prompt"] = prompt
            captured["context"] = context
            return {"result": "OK", "confidence": 0.93, "reason": "FUS"}

        runtime_module.vlm_client.judge = mapped_judge
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "roi.jpg"
            Image.new("RGB", (32, 32), "white").save(image_path)
            execution = await ScenarioRuntime().execute(
                self.database,
                version,
                image_path=str(image_path),
                source="TEST",
                context={"expected_text": "FUS"},
            )

        self.assertEqual(captured["prompt"], "请检查 FUS")
        self.assertEqual(captured["context"], {"expected_text": "FUS"})
        self.assertEqual(execution.output_json["nodes"]["vlm_1"]["checked_text"], "FUS")
        self.assertEqual(execution.output_json["nodes"]["rule_1"]["rule_actual"], "FUS")
        self.assertEqual(execution.output_json["result"], {
            "result": "OK",
            "checked_text": "FUS",
            "score": 0.93,
        })

    async def test_workflow_detector_bbox_can_crop_image_for_downstream_vlm(self) -> None:
        scene = InspectionScenario(
            code="WORKFLOW_DETECT_CROP_VLM",
            name="检测裁剪复核",
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
                node_key="model_1",
                name="目标检测",
                node_type="VISION_MODEL",
                sort_order=1,
                config_json={
                    "model_version_id": 999,
                    "input_mapping": {"image_path": "{{ input.image_path }}"},
                },
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="crop_1",
                name="裁剪目标",
                node_type="IMAGE_CROP",
                sort_order=2,
                config_json={
                    "image_path": "{{ input.image_path }}",
                    "bbox": "{{ nodes.model_1.objects.0.bbox }}",
                    "padding_ratio": 0,
                },
            ),
            ScenarioNode(
                scenario_version_id=version.id,
                node_key="vlm_1",
                name="VLM 复核",
                node_type="VLM",
                sort_order=3,
                config_json={
                    "vlm_model_id": self.primary_model.id,
                    "prompt": "检查 {{ params.detected_label }}",
                    "input_mapping": {
                        "image_path": "{{ nodes.crop_1.image_path }}",
                        "detected_label": "{{ nodes.model_1.objects.0.label }}",
                    },
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
                        "result": "{{ nodes.vlm_1.result }}",
                        "crop_image_path": "{{ nodes.crop_1.image_path }}",
                    },
                },
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="start",
                target_node_key="model_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="model_1",
                target_node_key="crop_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="crop_1",
                target_node_key="vlm_1",
            ),
            ScenarioEdge(
                scenario_version_id=version.id,
                source_node_key="vlm_1",
                target_node_key="end",
            ),
        ))
        self.database.commit()
        version = self.database.query(InspectionScenarioVersion).options(
            selectinload(InspectionScenarioVersion.scenario),
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
        ).filter_by(id=version.id).one()

        captured: dict[str, object] = {}
        original_selector = runtime_module._select_published_vision_model
        original_inference = runtime_module.run_yolo_inference
        original_judge = runtime_module.vlm_client.judge

        def fake_inference(_spec, received_image_path, *, config):
            captured["detector_image_path"] = received_image_path
            captured["detector_config"] = config
            return {
                "result": "OK",
                "confidence": 0.96,
                "reason": "检测到线束端子",
                "task_type": "YOLO_DETECTION",
                "objects": [{
                    "label": "harness_terminal",
                    "confidence": 0.96,
                    "bbox": [10.0, 20.0, 70.0, 60.0],
                }],
                "detection_count": 1,
            }

        async def fake_judge(_model, *, image_path, context, **_kwargs):
            captured["vlm_image_path"] = image_path
            captured["vlm_context"] = context
            with Image.open(image_path) as cropped:
                captured["crop_size"] = cropped.size
            return {"result": "OK", "confidence": 0.91, "reason": "局部目标符合要求"}

        runtime_module._select_published_vision_model = lambda _database, _node: SimpleNamespace(
            version_id=999,
            model_code="AI_HARNESS",
            task_type="YOLO_DETECTION",
        )
        runtime_module.run_yolo_inference = fake_inference
        runtime_module.vlm_client.judge = fake_judge
        crop_path: Path | None = None
        try:
            with TemporaryDirectory() as directory:
                image_path = Path(directory) / "input.jpg"
                Image.new("RGB", (120, 80), "white").save(image_path)
                execution = await ScenarioRuntime().execute(
                    self.database,
                    version,
                    image_path=str(image_path),
                    source="TEST",
                )
                self.assertEqual(execution.status, "COMPLETED", execution.error_message)
                self.assertIn("crop_1", execution.output_json["nodes"], execution.output_json)
                crop_path = Path(execution.output_json["nodes"]["crop_1"]["image_path"])

            self.assertEqual(execution.result, "OK")
            self.assertEqual(captured["detector_image_path"], str(image_path))
            self.assertEqual(captured["vlm_context"]["detected_label"], "harness_terminal")
            self.assertEqual(captured["crop_size"], (60, 40))
            self.assertEqual(captured["vlm_image_path"], str(crop_path))
            self.assertTrue(crop_path.is_file())
            traces = execution.output_json["traces"]
            self.assertEqual([trace["node_key"] for trace in traces], [
                "start", "model_1", "crop_1", "vlm_1", "end",
            ])
            self.assertTrue(all("elapsed_ms" in trace for trace in traces))
            self.assertEqual(traces[2]["output"]["crop_bbox"], [10, 20, 70, 60])
        finally:
            runtime_module._select_published_vision_model = original_selector
            runtime_module.run_yolo_inference = original_inference
            runtime_module.vlm_client.judge = original_judge
            if crop_path and crop_path.exists():
                crop_path.unlink()
                try:
                    crop_path.parent.rmdir()
                except OSError:
                    pass

    async def test_roi_binding_points_to_published_scenario_version(self) -> None:
        product = Product(code="PDU", name="PDU")
        station = Station(code="ST01", name="Station")
        self.database.add_all((product, station))
        self.database.flush()
        recipe = Recipe(
            code="R1",
            name="Recipe",
            product_id=product.id,
            station_id=station.id,
        )
        self.database.add(recipe)
        self.database.flush()
        roi = RegionOfInterest(
            recipe_id=recipe.id,
            code="ROI_1",
            name="ROI_1",
            x_ratio=0.1,
            y_ratio=0.1,
            width_ratio=0.2,
            height_ratio=0.2,
        )
        self.database.add(roi)
        self.database.flush()
        binding = RoiScenarioBinding(
            roi_id=roi.id,
            scenario_version_id=self.version.id,
        )
        self.database.add(binding)
        self.database.commit()
        self.assertEqual(binding.scenario_version.status, "PUBLISHED")
