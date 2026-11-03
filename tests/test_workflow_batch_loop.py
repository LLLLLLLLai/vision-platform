import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, selectinload

from app.api.routes.scenarios import _validate_image_crop_node, _workflow_preflight
from app.db.base import Base
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioEdge,
    ScenarioNode,
    VlmModelConfig,
    VisionModel,
    VisionModelVersion,
)
from app.services import scenario_runtime as runtime_module
from app.services.scenario_runtime import ScenarioRuntime, ScenarioRuntimeError


class WorkflowBatchLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.model = VlmModelConfig(
            code="BATCH_VLM",
            name="Batch VLM",
            base_url="http://vlm.test/v1",
            model_name="batch-vlm",
        )
        self.scene = InspectionScenario(code="BATCH_SCENE", name="批量复核", mode="WORKFLOW")
        self.database.add_all((self.model, self.scene))
        self.database.flush()
        self.version = InspectionScenarioVersion(
            scenario_id=self.scene.id,
            version="1.0",
            status="PUBLISHED",
        )
        self.database.add(self.version)
        self.database.flush()

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def _build_graph(self, *, max_iterations: int = 10) -> InspectionScenarioVersion:
        nodes = [
            ("start", "START", {}),
            ("model_1", "VISION_MODEL", {"model_version_id": 999}),
            ("crop_1", "IMAGE_CROP", {
                "image_path": "{{ input.image_path }}",
                "objects": "{{ nodes.model_1.objects }}",
                "padding_ratio": 0,
            }),
            ("loop_1", "LOOP", {
                "operation": "VLM",
                "items": "{{ nodes.crop_1.crops }}",
                "item_name": "item",
                "max_iterations": max_iterations,
                "max_parallelism": 2,
                "aggregate": "ALL_OK",
                "vlm_model_id": self.model.id,
                "prompt": "检查 {{ item.label }}；标准 {{ input.expected }}。",
                "vlm_parameters": {"temperature": 0},
            }),
            ("annotate_1", "IMAGE_ANNOTATE", {
                "image_path": "{{ input.image_path }}",
                "objects": "{{ nodes.model_1.objects }}",
            }),
            ("end", "END", {"output": {
                "items": "{{ nodes.loop_1.items }}",
                "annotated_image_path": "{{ nodes.annotate_1.image_path }}",
            }}),
        ]
        for order, (key, node_type, config) in enumerate(nodes):
            self.database.add(ScenarioNode(
                scenario_version_id=self.version.id,
                node_key=key,
                name=key,
                node_type=node_type,
                sort_order=order,
                config_json=config,
            ))
            if order:
                self.database.add(ScenarioEdge(
                    scenario_version_id=self.version.id,
                    source_node_key=nodes[order - 1][0],
                    target_node_key=key,
                ))
        self.database.commit()
        return self.database.query(InspectionScenarioVersion).options(
            selectinload(InspectionScenarioVersion.scenario),
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
        ).filter_by(id=self.version.id).one()

    async def test_multiple_objects_are_cropped_reviewed_and_annotated(self) -> None:
        version = self._build_graph()
        active = 0
        peak = 0
        prompts: list[str] = []

        def fake_inference(_spec, _image_path, *, config):
            return {"task_type": "YOLO_DETECTION", "objects": [
                {"label": "fuse", "confidence": 0.95, "bbox": [10, 10, 30, 30]},
                {"label": "screw", "confidence": 0.91, "bbox": [40, 20, 70, 50]},
            ]}

        async def fake_judge(_model, *, prompt, image_path, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            prompts.append(prompt)
            with Image.open(image_path) as image:
                self.assertIn(image.size, {(20, 20), (30, 30)})
            await asyncio.sleep(0.02)
            active -= 1
            return {"result": "NG" if "screw" in prompt else "OK", "reason": prompt}

        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "source.jpg"
            Image.new("RGB", (100, 80), "white").save(image_path)
            with patch.object(runtime_module, "PROJECT_ROOT", Path(directory)), patch.object(
                runtime_module, "_select_published_vision_model",
                return_value=SimpleNamespace(version_id=999, model_code="BATCH", task_type="YOLO_DETECTION"),
            ), patch.object(runtime_module, "run_yolo_inference", side_effect=fake_inference), patch.object(
                runtime_module.vlm_client, "judge", side_effect=fake_judge,
            ):
                execution = await ScenarioRuntime().execute(
                    self.database, version, image_path=str(image_path), source="TEST", context={"expected": "已安装"}
                )
                self.assertEqual(execution.status, "COMPLETED", execution.error_message)
                self.assertEqual(execution.result, "NG")
                crops = execution.output_json["nodes"]["crop_1"]["crops"]
                self.assertEqual([crop["label"] for crop in crops], ["fuse", "screw"])
                self.assertEqual([crop["bbox"] for crop in crops], [[10, 10, 30, 30], [40, 20, 70, 50]])
                self.assertTrue(all(Path(crop["image_path"]).is_file() for crop in crops))
                reviewed = execution.output_json["nodes"]["loop_1"]["items"]
                self.assertEqual([item["result"] for item in reviewed], ["OK", "NG"])
                self.assertTrue(all("elapsed_ms" in item for item in reviewed))
                self.assertEqual(execution.output_json["result"]["result"], "NG")
                self.assertTrue(Path(execution.output_json["result"]["annotated_image_path"]).is_file())
                self.assertEqual(peak, 2)
                self.assertEqual(len(prompts), 2)
                self.assertTrue(any("fuse" in prompt and "已安装" in prompt for prompt in prompts))

    async def test_zero_targets_does_not_produce_false_ok(self) -> None:
        version = self._build_graph()
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "source.jpg"
            Image.new("RGB", (100, 80), "white").save(image_path)
            with patch.object(runtime_module, "PROJECT_ROOT", Path(directory)), patch.object(
                runtime_module, "_select_published_vision_model",
                return_value=SimpleNamespace(version_id=999, model_code="BATCH", task_type="YOLO_DETECTION"),
            ), patch.object(runtime_module, "run_yolo_inference", return_value={"task_type": "YOLO_DETECTION", "objects": []}), patch.object(
                runtime_module.vlm_client, "judge",
            ) as judge:
                execution = await ScenarioRuntime().execute(self.database, version, image_path=str(image_path), source="TEST")
            self.assertEqual(execution.status, "COMPLETED", execution.error_message)
            self.assertEqual(execution.result, "UNCERTAIN")
            self.assertEqual(execution.output_json["nodes"]["crop_1"]["crops"], [])
            self.assertEqual(execution.output_json["nodes"]["loop_1"]["items"], [])
            judge.assert_not_called()

    async def test_too_many_targets_fails_instead_of_ignoring_extra_targets(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "VLM", "items": "{{ input.items }}", "max_iterations": 1,
            "vlm_model_id": self.model.id, "prompt": "检查 {{ item.label }}",
        })
        with self.assertRaisesRegex(ScenarioRuntimeError, "超过设置的最大次数"):
            await ScenarioRuntime()._execute_loop(
                self.database, self.version, node,
                {"input": {"items": [{"image_path": "a.jpg"}, {"image_path": "b.jpg"}]}, "nodes": {}},
            )

    async def test_custom_loop_body_runs_multiple_nodes_for_each_item(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 5,
            "aggregate": "ALL_OK", "body_nodes": [
                {"node_key": "body_vlm_1", "name": "逐项检测", "node_type": "VLM", "config_json": {
                    "vlm_model_id": self.model.id, "prompt": "检查 {{ item.label }}",
                }},
                {"node_key": "body_rule_1", "name": "结果判定", "node_type": "RULE", "config_json": {
                    "actual": "{{ nodes.body_vlm_1.result }}", "operator": "EQUALS", "expected": "OK",
                }},
            ],
        })

        async def fake_judge(_model, *, prompt, **_kwargs):
            return {"result": "NG" if "screw" in prompt else "OK", "reason": prompt}

        context = {
            "input": {"image_path": "source.jpg", "items": [
                {"image_path": "fuse.jpg", "label": "fuse"},
                {"image_path": "screw.jpg", "label": "screw"},
            ]},
            "nodes": {}, "traces": [],
        }
        with patch.object(runtime_module.vlm_client, "judge", side_effect=fake_judge):
            result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, context)

        self.assertEqual(result["result"], "NG")
        self.assertEqual([item["result"] for item in result["items"]], ["OK", "NG"])
        self.assertEqual([trace["node_key"] for trace in result["items"][0]["traces"]], [
            "loop_start", "body_vlm_1", "body_rule_1", "loop_end",
        ])
        self.assertEqual(result["items"][0]["nodes"]["body_vlm_1"]["reason"], "检查 fuse")

    async def test_custom_loop_body_uses_item_and_parent_inputs(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 2,
            "body_nodes": [{"node_key": "body_rule_1", "node_type": "RULE", "config_json": {
                "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "{{ parent.input.expected }}",
            }}],
        })
        result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg", "expected": "FU5", "items": [
                {"value": "FU5"}, {"value": "FU7"},
            ]}, "nodes": {}, "traces": [],
        })
        self.assertEqual(result["result"], "NG")
        self.assertEqual([item["result"] for item in result["items"]], ["OK", "NG"])

    async def test_custom_loop_body_routes_condition_branches(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 2,
            "body_nodes": [
                {"node_key": "body_if_1", "node_type": "IF", "config_json": {
                    "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU5",
                }},
                {"node_key": "body_rule_1", "node_type": "RULE", "config_json": {
                    "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU5",
                }},
            ],
        })
        result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg", "items": [
                {"value": "FU5"}, {"value": "FU7"},
            ]}, "nodes": {}, "traces": [],
        })
        self.assertEqual([item["result"] for item in result["items"]], ["OK", "NG"])
        self.assertEqual(result["items"][1]["nodes"]["body_rule_1"]["result"], "SKIPPED")

    async def test_custom_loop_body_uses_canvas_connections(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 2,
            "body_nodes": [
                {"node_key": "body_if", "node_type": "IF", "config_json": {
                    "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU5",
                }},
                {"node_key": "body_fu5", "node_type": "RULE", "config_json": {
                    "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU5",
                }},
                {"node_key": "body_fu7", "node_type": "RULE", "config_json": {
                    "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU7",
                }},
            ],
            "body_edges": [
                {"source_node_key": "loop_start", "target_node_key": "body_if"},
                {"source_node_key": "body_if", "target_node_key": "body_fu5", "branch": "true"},
                {"source_node_key": "body_if", "target_node_key": "body_fu7", "branch": "false"},
                {"source_node_key": "body_fu5", "target_node_key": "loop_end"},
                {"source_node_key": "body_fu7", "target_node_key": "loop_end"},
            ],
        })
        result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg", "items": [{"value": "FU5"}, {"value": "FU7"}]},
            "nodes": {}, "traces": [],
        })

        self.assertEqual([item["nodes"]["body_fu5"]["result"] for item in result["items"]], ["OK", "SKIPPED"])
        self.assertEqual([item["nodes"]["body_fu7"]["result"] for item in result["items"]], ["SKIPPED", "OK"])

    async def test_custom_loop_body_failed_child_does_not_pass(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 1,
            "body_nodes": [{"node_key": "body_vlm_1", "node_type": "VLM", "config_json": {
                "vlm_model_id": self.model.id, "prompt": "检查图片",
            }}],
        })
        with patch.object(runtime_module.vlm_client, "judge", side_effect=RuntimeError("VLM 超时")):
            result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
                "input": {"image_path": "source.jpg", "items": [{"image_path": "item.jpg"}]},
                "nodes": {}, "traces": [],
            })
        self.assertEqual(result["result"], "ERROR")
        self.assertIn("VLM 超时", result["items"][0]["reason"])

    async def test_s3_upload_inside_loop_uses_distinct_item_keys(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 2,
            "body_nodes": [{"node_key": "body_s3_1", "node_type": "S3_UPLOAD", "config_json": {
                "image_path": "{{ input.image_path }}",
            }}],
        })
        with patch.object(runtime_module.SceneS3Storage, "publish_result", return_value="s3://test/result.jpg") as publish:
            await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
                "input": {"image_path": "source.jpg", "result_s3_key": "results/result.jpg", "items": [
                    {"image_path": "one.jpg"}, {"image_path": "two.jpg"},
                ]}, "nodes": {}, "traces": [],
            })
        self.assertEqual([call.kwargs["requested_key"] for call in publish.call_args_list], [
            "results/result_item_0.jpg", "results/result_item_1.jpg",
        ])

    async def test_custom_loop_body_runs_in_complete_scene_execution(self) -> None:
        version = self._build_graph()
        loop = next(item for item in version.nodes if item.node_key == "loop_1")
        loop.config_json = {
            "operation": "SUBFLOW", "items": "{{ nodes.crop_1.crops }}", "max_iterations": 3,
            "body_nodes": [{"node_key": "body_vlm_1", "node_type": "VLM", "config_json": {
                "vlm_model_id": self.model.id, "prompt": "检查 {{ item.label }}",
            }}],
        }
        self.database.flush()
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "source.jpg"
            Image.new("RGB", (80, 80), "white").save(image_path)
            with patch.object(runtime_module, "PROJECT_ROOT", Path(directory)), patch.object(
                runtime_module, "_select_published_vision_model",
                return_value=SimpleNamespace(version_id=999, model_code="BATCH", task_type="YOLO_DETECTION"),
            ), patch.object(runtime_module, "run_yolo_inference", return_value={
                "task_type": "YOLO_DETECTION", "objects": [
                    {"label": "fuse", "confidence": 0.95, "bbox": [10, 10, 30, 30]},
                ],
            }), patch.object(runtime_module.vlm_client, "judge", return_value={"result": "OK", "reason": "已安装"}):
                execution = await ScenarioRuntime().execute(
                    self.database, version, image_path=str(image_path), source="TEST",
                )
        self.assertEqual(execution.status, "COMPLETED", execution.error_message)
        self.assertEqual(execution.result, "OK")
        item = execution.output_json["nodes"]["loop_1"]["items"][0]
        self.assertEqual(item["result"], "OK")
        self.assertEqual(item["traces"][1]["node_key"], "body_vlm_1")

    async def test_custom_loop_body_rejects_overflow_and_nested_loop(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 1,
            "body_nodes": [{"node_key": "body_rule_1", "node_type": "RULE", "config_json": {
                "actual": "{{ input.value }}", "operator": "EQUALS", "expected": "FU5",
            }}],
        })
        with self.assertRaisesRegex(ScenarioRuntimeError, "超过设置的最大次数"):
            await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
                "input": {"items": [1, 2]}, "nodes": {}, "traces": [],
            })
        node.config_json = {**node.config_json, "body_nodes": [{
            "node_key": "nested", "node_type": "LOOP", "config_json": {},
        }]}
        with self.assertRaisesRegex(ScenarioRuntimeError, "类型不受支持"):
            await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
                "input": {"items": [1]}, "nodes": {}, "traces": [],
            })

    async def test_preflight_rejects_invalid_custom_loop_body_reference(self) -> None:
        version = self._build_graph()
        loop = next(item for item in version.nodes if item.node_key == "loop_1")
        loop.config_json = {
            "operation": "SUBFLOW", "items": "{{ nodes.crop_1.crops }}", "max_iterations": 10,
            "body_nodes": [{"node_key": "body_rule_1", "node_type": "RULE", "config_json": {
                "actual": "{{ nodes.missing.result }}", "operator": "EQUALS", "expected": "OK",
            }}],
        }
        self.database.flush()
        diagnostics = _workflow_preflight(self.database, version)["diagnostics"]
        self.assertIn("LOOP_BODY_REFERENCE_INVALID", {item["code"] for item in diagnostics})

    async def test_preflight_accepts_detection_inside_custom_loop_body(self) -> None:
        for position, (key, node_type, config) in enumerate([
            ("start", "START", {}),
            ("loop_1", "LOOP", {
                "operation": "SUBFLOW", "items": "{{ input.items }}", "max_iterations": 5,
                "body_nodes": [{"node_key": "body_vlm_1", "node_type": "VLM", "config_json": {
                    "vlm_model_id": self.model.id, "prompt": "检查 {{ item.label }}",
                }}],
            }),
            ("end", "END", {"output": {"result": "{{ nodes.loop_1.result }}"}}),
        ]):
            self.database.add(ScenarioNode(
                scenario_version_id=self.version.id, node_key=key, node_type=node_type,
                name=key, sort_order=position, config_json=config,
            ))
        for source, target in [("start", "loop_1"), ("loop_1", "end")]:
            self.database.add(ScenarioEdge(
                scenario_version_id=self.version.id, source_node_key=source, target_node_key=target,
            ))
        self.database.commit()
        version = self.database.query(InspectionScenarioVersion).options(
            selectinload(InspectionScenarioVersion.scenario),
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
        ).filter_by(id=self.version.id).one()
        result = _workflow_preflight(self.database, version)
        self.assertTrue(result["ready"], result["diagnostics"])

    async def test_batch_crop_rejects_classification_but_allows_segmentation(self) -> None:
        version = self._build_graph()
        model_node = next(node for node in version.nodes if node.node_key == "model_1")
        crop_node = next(node for node in version.nodes if node.node_key == "crop_1")
        model = VisionModel(code="BATCH_CLASSIFY", name="分类模型", task_type="YOLO_CLASSIFICATION")
        self.database.add(model)
        self.database.flush()
        model_version = VisionModelVersion(
            vision_model_id=model.id, version="1.0", status="PUBLISHED", weights_path="test.pt"
        )
        self.database.add(model_version)
        self.database.flush()
        model_node.config_json = {"model_version_id": model_version.id}
        self.database.flush()

        with self.assertRaises(HTTPException) as invalid:
            _validate_image_crop_node(self.database, version, crop_node)
        self.assertIn("不输出", str(invalid.exception.detail))

        model.task_type = "YOLO_SEGMENTATION"
        self.database.flush()
        _validate_image_crop_node(self.database, version, crop_node)

    async def test_preflight_requires_loop_vlm_configuration(self) -> None:
        version = self._build_graph()
        loop = next(node for node in version.nodes if node.node_key == "loop_1")
        loop.config_json = {"operation": "VLM", "items": "{{ nodes.crop_1.crops }}", "max_parallelism": 7}
        self.database.flush()

        diagnostics = _workflow_preflight(self.database, version)["diagnostics"]
        codes = {item["code"] for item in diagnostics}
        self.assertIn("LOOP_PROMPT_REQUIRED", codes)
        self.assertIn("LOOP_VLM_INVALID", codes)
        self.assertIn("LOOP_PARALLELISM_INVALID", codes)

    async def test_preflight_accepts_published_detector_batch_crop_and_loop(self) -> None:
        version = self._build_graph()
        with TemporaryDirectory() as directory:
            weights_path = Path(directory) / "best.pt"
            weights_path.write_bytes(b"test weights")
            model = VisionModel(code="BATCH_DETECT", name="目标检测", task_type="YOLO_DETECTION")
            self.database.add(model)
            self.database.flush()
            model_version = VisionModelVersion(
                vision_model_id=model.id,
                version="1.0",
                status="PUBLISHED",
                weights_path=str(weights_path),
            )
            self.database.add(model_version)
            self.database.flush()
            model_node = next(node for node in version.nodes if node.node_key == "model_1")
            model_node.config_json = {"model_version_id": model_version.id}
            self.database.flush()

            result = _workflow_preflight(self.database, version)

        self.assertTrue(result["ready"], result["diagnostics"])
        self.assertEqual(result["summary"]["errors"], 0)

    async def test_preflight_rejects_model_objects_without_crop_images(self) -> None:
        version = self._build_graph()
        loop = next(node for node in version.nodes if node.node_key == "loop_1")
        loop.config_json = {**loop.config_json, "items": "{{ nodes.model_1.objects }}"}
        self.database.flush()

        diagnostics = _workflow_preflight(self.database, version)["diagnostics"]

        self.assertIn("LOOP_ITEMS_NEED_IMAGE", {item["code"] for item in diagnostics})

    async def test_per_item_vlm_error_is_visible_and_fails_closed(self) -> None:
        node = ScenarioNode(config_json={
            "operation": "VLM",
            "items": "{{ input.items }}",
            "max_iterations": 10,
            "vlm_model_id": self.model.id,
            "prompt": "检查 {{ item.label }}",
        })

        async def fake_judge(_model, *, prompt, **_kwargs):
            if "screw" in prompt:
                raise TimeoutError("VLM 请求超时")
            return {"result": "OK", "reason": "检测通过"}

        with patch.object(runtime_module.vlm_client, "judge", side_effect=fake_judge):
            result = await ScenarioRuntime()._execute_loop(
                self.database, self.version, node,
                {"input": {"items": [
                    {"image_path": "fuse.jpg", "label": "fuse"},
                    {"image_path": "screw.jpg", "label": "screw"},
                ]}, "nodes": {}},
            )

        self.assertEqual(result["result"], "ERROR")
        self.assertEqual([item["result"] for item in result["items"]], ["OK", "ERROR"])
        self.assertIn("超时", result["items"][1]["reason"])

    async def test_existing_loop_remains_pass_through(self) -> None:
        node = ScenarioNode(config_json={"items": "{{ input.items }}", "max_iterations": 1})
        result = await ScenarioRuntime()._execute_loop(
            self.database, self.version, node,
            {"input": {"items": [1, 2]}, "nodes": {}},
        )
        self.assertEqual(result["items"], [1])
        self.assertEqual(result["count"], 1)

    async def test_iteration_executes_body_for_every_array_item(self) -> None:
        node = ScenarioNode(node_type="ITERATION", config_json={
            "items": "{{ input.items }}", "max_iterations": 3, "aggregate": "ALL_OK",
            "body_nodes": [{"node_key": "body_rule", "node_type": "RULE", "config_json": {
                "actual": "{{ item.label }}", "operator": "EQUALS", "expected": "FU5",
            }}],
        })
        result = await ScenarioRuntime()._execute_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg", "items": [{"label": "FU5"}, {"label": "FU6"}]},
            "nodes": {}, "traces": [],
        })
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["result"] for item in result["items"]], ["OK", "NG"])
        self.assertEqual(result["result"], "NG")

    async def test_condition_loop_updates_state_and_exits_when_matched(self) -> None:
        node = ScenarioNode(node_type="CONDITION_LOOP", config_json={
            "initial_state": {"count": 0},
            "state_updates": {"count": {"operation": "INCREMENT", "value": 1}},
            "stop_condition": {"actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 3},
            "max_iterations": 5, "timeout_seconds": 30,
            "body_nodes": [{"node_key": "body_rule", "node_type": "RULE", "config_json": {
                "actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 2,
            }}],
        })
        result = await ScenarioRuntime()._execute_condition_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg"}, "nodes": {}, "traces": [],
        })
        self.assertEqual(result["exit_reason"], "CONDITION_MET")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["state"]["count"], 3)
        self.assertEqual([item["result"] for item in result["rounds"]], ["NG", "NG", "OK"])
        self.assertEqual(result["result"], "OK")

    async def test_condition_loop_fails_closed_at_round_limit(self) -> None:
        node = ScenarioNode(node_type="CONDITION_LOOP", config_json={
            "initial_state": {"count": 0},
            "state_updates": {"count": {"operation": "INCREMENT", "value": 1}},
            "stop_condition": {"actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 9},
            "max_iterations": 2, "timeout_seconds": 30,
            "body_nodes": [{"node_key": "body_rule", "node_type": "RULE", "config_json": {
                "actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 0,
            }}],
        })
        result = await ScenarioRuntime()._execute_condition_loop(self.database, self.version, node, {
            "input": {"image_path": "source.jpg"}, "nodes": {}, "traces": [],
        })
        self.assertEqual(result["result"], "ERROR")
        self.assertEqual(result["exit_reason"], "MAX_ROUNDS")
        self.assertEqual(result["count"], 2)

    async def test_new_loop_nodes_run_through_workflow_graph(self) -> None:
        for node_type, loop_config, expected in [
            ("ITERATION", {
                "items": "{{ input.items }}", "max_iterations": 2, "aggregate": "ALL_OK",
                "body_nodes": [{"node_key": "body_rule", "node_type": "RULE", "config_json": {
                    "actual": "{{ item.label }}", "operator": "EQUALS", "expected": "FU5",
                }}],
            }, "NG"),
            ("CONDITION_LOOP", {
                "initial_state": {"count": 0},
                "state_updates": {"count": {"operation": "INCREMENT", "value": 1}},
                "stop_condition": {"actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 2},
                "max_iterations": 3, "timeout_seconds": 30,
                "body_nodes": [{"node_key": "body_rule", "node_type": "RULE", "config_json": {
                    "actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 1,
                }}],
            }, "OK"),
        ]:
            with self.subTest(node_type=node_type):
                graph = SimpleNamespace(
                    scenario=self.scene, primary_vlm_model_id=None, prompt_template=None,
                    definition_json={},
                    nodes=[
                        ScenarioNode(id=1, node_key="start", name="开始", node_type="START", sort_order=0, config_json={}, enabled=True),
                        ScenarioNode(id=2, node_key="loop", name="循环", node_type=node_type, sort_order=1, config_json=loop_config, enabled=True),
                        ScenarioNode(id=3, node_key="end", name="结束", node_type="END", sort_order=2, config_json={"output": {"result": "{{ nodes.loop.result }}"}}, enabled=True),
                    ],
                    edges=[
                        ScenarioEdge(source_node_key="start", target_node_key="loop", mapping_json={}),
                        ScenarioEdge(source_node_key="loop", target_node_key="end", mapping_json={}),
                    ],
                )
                context = {
                    "input": {"image_path": "source.jpg", "items": [{"label": "FU5"}, {"label": "FU6"}]},
                    "nodes": {}, "traces": [],
                }
                result = await ScenarioRuntime()._run_definition(
                    self.database, graph, image_path="source.jpg", runtime_context=context,
                )
                self.assertEqual(result["result"], expected)
                self.assertEqual(context["nodes"]["loop"]["result"], expected)
                self.assertEqual([entry["node_key"] for entry in context["traces"]], ["start", "loop", "end"])

    async def test_condition_loop_times_out_without_false_ok(self) -> None:
        node = ScenarioNode(node_type="CONDITION_LOOP", config_json={
            "initial_state": {"count": 0}, "state_updates": {},
            "stop_condition": {"actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 1},
            "max_iterations": 2, "timeout_seconds": 0.01,
        })

        async def slow_body(*_args, **_kwargs):
            await asyncio.sleep(0.1)

        runtime = ScenarioRuntime()
        with patch.object(runtime, "_execute_loop_body", side_effect=slow_body):
            result = await runtime._execute_condition_loop(self.database, self.version, node, {
                "input": {"image_path": "source.jpg"}, "nodes": {}, "traces": [],
            })
        self.assertEqual(result["result"], "ERROR")
        self.assertEqual(result["exit_reason"], "TIMEOUT")

    async def test_new_loop_nodes_allow_nested_vision_at_preflight(self) -> None:
        for node_type, loop_config in [
            ("ITERATION", {
                "items": "{{ input.items }}", "max_iterations": 3, "aggregate": "ALL_OK",
                "body_nodes": [{"node_key": "body_vlm", "node_type": "VLM", "config_json": {
                    "vlm_model_id": self.model.id, "prompt": "检查 {{ item.label }}",
                }}],
            }),
            ("CONDITION_LOOP", {
                "initial_state": {"count": 0},
                "state_updates": {"count": {"operation": "INCREMENT", "value": 1}},
                "stop_condition": {"actual": "{{ state.count }}", "operator": "NUMBER_GTE", "expected": 2},
                "max_iterations": 3, "timeout_seconds": 30,
                "body_nodes": [{"node_key": "body_vlm", "node_type": "VLM", "config_json": {
                    "vlm_model_id": self.model.id, "prompt": "检查当前图片",
                }}],
            }),
        ]:
            with self.subTest(node_type=node_type):
                self.database.query(ScenarioEdge).filter_by(scenario_version_id=self.version.id).delete()
                self.database.query(ScenarioNode).filter_by(scenario_version_id=self.version.id).delete()
                self.database.flush()
                for index, (key, kind, config) in enumerate([
                    ("start", "START", {}), ("inner", node_type, loop_config),
                    ("end", "END", {"output": {"result": "{{ nodes.inner.result }}"}}),
                ]):
                    self.database.add(ScenarioNode(
                        scenario_version_id=self.version.id, node_key=key, node_type=kind,
                        name=key, sort_order=index, config_json=config,
                    ))
                self.database.add_all([
                    ScenarioEdge(scenario_version_id=self.version.id, source_node_key="start", target_node_key="inner"),
                    ScenarioEdge(scenario_version_id=self.version.id, source_node_key="inner", target_node_key="end"),
                ])
                self.database.commit()
                version = self.database.query(InspectionScenarioVersion).options(
                    selectinload(InspectionScenarioVersion.scenario),
                    selectinload(InspectionScenarioVersion.nodes),
                    selectinload(InspectionScenarioVersion.edges),
                ).filter_by(id=self.version.id).one()
                preflight = _workflow_preflight(self.database, version)
                self.assertTrue(preflight["ready"], preflight["diagnostics"])


if __name__ == "__main__":
    unittest.main()
