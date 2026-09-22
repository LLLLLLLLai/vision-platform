from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import datasets as datasets_module
from app.api.routes import vlm_models as vlm_models_module
from app.api.routes.automation import (
    PromptOptimizationRequest,
    SceneEvaluationRequest,
    cancel_automation_job,
    get_automation_job,
    get_training_artifact,
    queue_prompt_optimization,
    queue_scene_evaluation,
)
from app.core.config import settings
from app.api.routes.datasets import (
    DatasetCreate,
    DatasetItemUpdate,
    DatasetUpdate,
    create_dataset,
    get_dataset,
    remove_dataset_item,
    update_dataset,
    update_dataset_item,
    upload_dataset_item,
)
from app.api.routes.scenarios import (
    ManualReviewRequest,
    NodeCreate,
    ScenarioCreate,
    ScenarioExecuteRequest,
    ScenarioUpdate,
    VersionCreate,
    VersionUpdate,
    add_node,
    create_scenario,
    create_scenario_version,
    execute_scenario_version,
    get_execution,
    get_scenario,
    publish_scenario_version,
    record_manual_review,
    update_scenario,
    update_scenario_version,
)
from app.api.routes.vlm_models import (
    VlmModelCreate,
    VlmModelUpdate,
    create_vlm_model,
    delete_vlm_model,
    test_vlm_model,
    update_vlm_model,
)
from app.api.routes.vision_models import (
    TrainingJobCreate,
    VisionModelCreate,
    VisionModelUpdate,
    VisionModelVersionCreate,
    create_model_version,
    create_vision_model,
    delete_vision_model,
    publish_model_version,
    queue_training_job,
    update_vision_model,
)
from app.db.base import Base
from app.models.intelligence import AutomationJob, DatasetItem
from app.services import automation_worker as worker_module
from app.services import scenario_runtime as runtime_module
from app.services.automation_worker import AutomationWorker


class IntelligenceManagementIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.database = self.session_factory()
        self.temporary_directory = TemporaryDirectory()
        self.temporary_root = Path(self.temporary_directory.name)
        self.original_project_root = datasets_module.PROJECT_ROOT
        self.original_judge = runtime_module.vlm_client.judge
        self.original_yolo_inference = runtime_module.run_yolo_inference
        self.original_connection_test = vlm_models_module.vlm_client.test_connection
        self.original_session_local = worker_module.SessionLocal
        self.original_gpu_memory_probe = worker_module._gpu_free_memory_mb
        self.original_yolo_training = worker_module.run_yolo_training
        self.original_training_artifacts_root = settings.training_artifacts_root
        datasets_module.PROJECT_ROOT = self.temporary_root
        settings.training_artifacts_root = str(self.temporary_root / "training_runs")
        worker_module.SessionLocal = self.session_factory
        runtime_module.vlm_client.judge = self._fake_judge
        runtime_module.run_yolo_inference = self._fake_yolo_inference
        vlm_models_module.vlm_client.test_connection = self._fake_connection_test
        worker_module.run_yolo_training = self._fake_yolo_training
        self.primary_vlm_id = create_vlm_model(
            VlmModelCreate(
                code="TEST_PRIMARY_VLM",
                name="测试主 VLM",
                base_url="http://vlm.test/v1",
                model_name="primary-vlm",
            ),
            database=self.database,
        )["id"]
        self.review_vlm_id = create_vlm_model(
            VlmModelCreate(
                code="TEST_REVIEW_VLM",
                name="测试复核 VLM",
                base_url="http://vlm.test/v1",
                model_name="review-vlm",
            ),
            database=self.database,
        )["id"]

    async def asyncTearDown(self) -> None:
        runtime_module.vlm_client.judge = self.original_judge
        runtime_module.run_yolo_inference = self.original_yolo_inference
        vlm_models_module.vlm_client.test_connection = self.original_connection_test
        worker_module.SessionLocal = self.original_session_local
        worker_module._gpu_free_memory_mb = self.original_gpu_memory_probe
        worker_module.run_yolo_training = self.original_yolo_training
        settings.training_artifacts_root = self.original_training_artifacts_root
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    async def _fake_judge(self, model, *, prompt: str, image_path: str | None = None, **_kwargs):
        if image_path is None:
            return {
                "prompt": "optimized prompt",
                "requirements_satisfied": True,
                "requirement_reason": "模拟满足用户优化要求。",
            }
        if "baseline prompt" in prompt:
            return {"result": "OK", "confidence": 0.55, "reason": "模拟基线输出。"}
        with Image.open(image_path) as image:
            red, green, _blue = image.convert("RGB").getpixel((0, 0))
        return {
            "result": "NG" if red > green else "OK",
            "confidence": 0.97,
            "reason": f"模拟 {model.name} 输出。",
        }

    def _fake_yolo_inference(self, spec, image_path, *, config):
        return {
            "result": "OK",
            "confidence": 0.96,
            "reason": "模拟已发布 YOLO 模型输出。",
            "model_version_id": spec.version_id,
            "detection_count": 1,
            "objects": [{"label": "harness", "confidence": 0.96, "bbox": [1, 2, 12, 18]}],
            "parameters": config,
        }

    async def _fake_connection_test(self, model):
        return {"ok": True, "model_code": model.code}

    def _fake_yolo_training(self, spec):
        run_dir = Path(settings.training_artifacts_root) / "fake_training" / str(spec.job_id)
        weights_path = run_dir / "weights" / "best.pt"
        weights_path.parent.mkdir(parents=True, exist_ok=True)
        weights_path.write_bytes(b"fake-yolo-weights")
        training_curve = run_dir / "results.png"
        Image.new("RGB", (24, 16), "steelblue").save(training_curve)
        confusion_matrix = run_dir / "confusion_matrix.png"
        Image.new("RGB", (24, 16), "seagreen").save(confusion_matrix)
        log_path = run_dir / "training.log"
        log_path.write_text("mock training completed\n", encoding="utf-8")
        return {
            "weights_path": str(weights_path),
            "metrics_json": {"metrics/mAP50(B)": 0.91},
            "artifact_paths_json": {
                "run_dir": str(run_dir),
                "best_weights": str(weights_path),
                "training_curve": str(training_curve),
                "confusion_matrix": str(confusion_matrix),
            },
            "log_path": str(log_path),
        }

    def _upload_image(
        self,
        dataset_id: int,
        filename: str,
        truth: str | None,
        color: tuple[int, int, int],
    ) -> dict:
        buffer = BytesIO()
        Image.new("RGB", (24, 24), color).save(buffer, format="PNG")
        buffer.seek(0)
        return upload_dataset_item(
            dataset_id,
            file=UploadFile(filename=filename, file=buffer),
            ground_truth=truth,
            split=None,
            database=self.database,
        )

    def _create_test_dataset(self) -> dict:
        dataset = create_dataset(
            DatasetCreate(
                code="HARNESS_TEST_SET",
                name="线束测试集",
                purpose="TEST",
                media_type="IMAGE",
                annotation_type="NONE",
            ),
            database=self.database,
        )
        self._upload_image(dataset["id"], "ok.png", "OK", (20, 120, 20))
        self._upload_image(dataset["id"], "ng.png", "NG", (180, 30, 30))
        return get_dataset(dataset["id"], database=self.database)

    async def test_dataset_keeps_vlm_truth_and_yolo_box_annotations_separate(self) -> None:
        with self.assertRaises(HTTPException) as invalid_test_dataset:
            create_dataset(
                DatasetCreate(
                    code="INVALID_VLM_TEST",
                    name="无效评测集",
                    purpose="TEST",
                    annotation_type="DETECTION",
                ),
                database=self.database,
            )
        self.assertEqual(invalid_test_dataset.exception.status_code, 400)

        generated_code_dataset = create_dataset(
            DatasetCreate(
                code="",
                name="自动编码测试集",
                purpose="TEST",
            ),
            database=self.database,
        )
        self.assertTrue(generated_code_dataset["code"].startswith("DATASET_TEST_IMAGE_"))

        training_dataset = create_dataset(
            DatasetCreate(
                code="BOX_ANNOTATION_TRAIN",
                name="检测框训练集",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="DETECTION",
            ),
            database=self.database,
        )
        training_item = self._upload_image(
            training_dataset["id"],
            "box.png",
            None,
            (20, 120, 20),
        )
        saved_item = update_dataset_item(
            training_dataset["id"],
            training_item["id"],
            DatasetItemUpdate(
                annotation_json={
                    "boxes": [
                        {
                            "label": "harness",
                            "x": 0.1,
                            "y": 0.2,
                            "width": 0.4,
                            "height": 0.3,
                        }
                    ]
                }
            ),
            database=self.database,
        )
        self.assertEqual(saved_item["annotation_status"], "LABELED")
        with self.assertRaises(HTTPException):
            update_dataset_item(
                training_dataset["id"],
                training_item["id"],
                DatasetItemUpdate(ground_truth="NG"),
                database=self.database,
            )

        test_dataset = self._create_test_dataset()
        with self.assertRaises(HTTPException):
            update_dataset_item(
                test_dataset["id"],
                test_dataset["items"][0]["id"],
                DatasetItemUpdate(annotation_json={"boxes": []}),
                database=self.database,
            )

    async def test_scene_maintenance_evaluation_and_optimization(self) -> None:
        scene = create_scenario(
            ScenarioCreate(
                name="AI 检测线束锁付",
                category="HARNESS",
                mode="VLM_DIRECT",
                primary_vlm_model_id=self.primary_vlm_id,
                review_vlm_model_id=self.review_vlm_id,
                prompt_template="baseline prompt: inspect harness lock",
            ),
            database=self.database,
        )
        self.assertTrue(get_scenario(scene["id"], database=self.database)["code"].startswith("SCENE_"))
        update_scenario(
            scene["id"],
            ScenarioUpdate(description="场景维护验证。"),
            database=self.database,
        )
        update_scenario_version(
            scene["version_id"],
            VersionUpdate(
                prompt_template="baseline prompt: inspect harness lock",
                primary_vlm_model_id=self.primary_vlm_id,
                review_vlm_model_id=self.review_vlm_id,
            ),
            database=self.database,
        )
        self.assertEqual(
            publish_scenario_version(scene["version_id"], database=self.database)["status"],
            "PUBLISHED",
        )

        workflow = create_scenario(
            ScenarioCreate(name="AI 工作流线束检查", category="HARNESS", mode="WORKFLOW"),
            database=self.database,
        )
        created_workflow = get_scenario(workflow["id"], database=self.database)
        created_workflow_version = next(item for item in created_workflow["versions"] if item["id"] == workflow["version_id"])
        self.assertEqual([node["node_key"] for node in created_workflow_version["nodes"]], ["start", "end"])
        add_node(
            workflow["version_id"],
            NodeCreate(
                node_key="vlm",
                name="VLM 检测",
                node_type="VLM",
                config_json={"vlm_model_id": self.primary_vlm_id, "prompt": "workflow prompt"},
                sort_order=2,
            ),
            database=self.database,
        )
        workflow_after_node = get_scenario(workflow["id"], database=self.database)
        workflow_after_node_version = next(item for item in workflow_after_node["versions"] if item["id"] == workflow["version_id"])
        self.assertEqual([node["node_key"] for node in workflow_after_node_version["nodes"]], ["start", "vlm", "end"])
        self.assertEqual(
            {(edge["source_node_key"], edge["target_node_key"]) for edge in workflow_after_node_version["edges"]},
            {("start", "vlm"), ("vlm", "end")},
        )
        publish_scenario_version(workflow["version_id"], database=self.database)

        workflow_copy = create_scenario_version(
            workflow["id"],
            VersionCreate(version="1.1", source_version_id=workflow["version_id"]),
            database=self.database,
        )
        copied_workflow = get_scenario(workflow["id"], database=self.database)
        copied_version = next(item for item in copied_workflow["versions"] if item["id"] == workflow_copy["id"])
        self.assertEqual([node["node_key"] for node in copied_version["nodes"]], ["start", "vlm", "end"])
        self.assertEqual(
            {(edge["source_node_key"], edge["target_node_key"]) for edge in copied_version["edges"]},
            {("start", "vlm"), ("vlm", "end")},
        )

        path = self.temporary_root / "single_ok.png"
        Image.new("RGB", (24, 24), "white").save(path)
        workflow_execution = await execute_scenario_version(
            workflow["version_id"],
            ScenarioExecuteRequest(image_path=str(path)),
            database=self.database,
        )
        self.assertEqual(workflow_execution["result"], "OK")
        execution = await execute_scenario_version(
            scene["version_id"],
            ScenarioExecuteRequest(image_path=str(path), enqueue_review=True),
            database=self.database,
        )
        worker = AutomationWorker()
        self.assertTrue(await worker.process_once())
        review = get_execution(execution["id"], database=self.database)["review"]
        self.assertEqual(review["verdict"], "OK")
        self.assertEqual(
            record_manual_review(execution["id"], ManualReviewRequest(verdict="NG"), database=self.database)["manual_verdict"],
            "NG",
        )

        dataset = self._create_test_dataset()
        evaluation = queue_scene_evaluation(
            SceneEvaluationRequest(scenario_version_id=scene["version_id"], dataset_id=dataset["id"]),
            database=self.database,
        )
        self.assertTrue(await worker.process_once())
        self.database.expire_all()
        evaluation_job = self.database.get(AutomationJob, evaluation["job_id"])
        self.assertEqual(evaluation_job.result_json["metrics"]["false_accept"], 1)

        optimization = queue_prompt_optimization(
            PromptOptimizationRequest(
                scenario_version_id=scene["version_id"],
                dataset_id=dataset["id"],
                detection_vlm_model_id=self.primary_vlm_id,
                optimizer_vlm_model_id=self.review_vlm_id,
                prompt_template="baseline prompt: inspect harness lock",
                optimization_requirements="优先降低漏判，保持 JSON 输出。",
                target_accuracy=1.0,
                max_rounds=2,
            ),
            database=self.database,
        )
        self.assertTrue(await worker.process_once())
        self.database.expire_all()
        optimized_job = self.database.get(AutomationJob, optimization["job_id"])
        self.assertEqual(optimized_job.result_json["best_prompt"], "optimized prompt")
        self.assertEqual(optimized_job.result_json["best_metrics"]["accuracy"], 1.0)
        self.assertEqual(optimized_job.result_json["detection_vlm_model_id"], self.primary_vlm_id)
        self.assertEqual(optimized_job.result_json["optimizer_vlm_model_id"], self.review_vlm_id)
        self.assertTrue(optimized_job.result_json["target_reached"])
        self.assertEqual(
            optimized_job.result_json["optimization_requirements"],
            "优先降低漏判，保持 JSON 输出。",
        )
        canceled = queue_scene_evaluation(
            SceneEvaluationRequest(scenario_version_id=scene["version_id"], dataset_id=dataset["id"]),
            database=self.database,
        )
        self.assertEqual(cancel_automation_job(canceled["job_id"], database=self.database)["status"], "CANCELED")
        self.assertEqual(
            create_scenario_version(
                scene["id"],
                VersionCreate(version="2.0", primary_vlm_model_id=self.primary_vlm_id, prompt_template="new prompt"),
                database=self.database,
            )["status"],
            "DRAFT",
        )

    async def test_running_jobs_are_not_falsely_canceled_and_restart_marks_them_failed(self) -> None:
        running_job = AutomationJob(
            job_type="YOLO_TRAINING",
            status="RUNNING",
            error_message="训练已开始。",
        )
        self.database.add(running_job)
        self.database.commit()
        self.database.refresh(running_job)

        with self.assertRaises(HTTPException) as context:
            cancel_automation_job(running_job.id, database=self.database)
        self.assertEqual(context.exception.status_code, 409)

        worker = AutomationWorker()
        worker._recover_interrupted_jobs()
        self.database.expire_all()
        recovered_job = self.database.get(AutomationJob, running_job.id)
        self.assertEqual(recovered_job.status, "FAILED")
        self.assertIn("服务重启导致任务中断", recovered_job.error_message)

    async def test_dataset_vlm_and_training_model_maintenance(self) -> None:
        vlm = create_vlm_model(
            VlmModelCreate(
                code="DISPOSABLE_VLM",
                name="待维护 VLM",
                base_url="http://vlm.test/v1/",
                model_name="qwen-test",
                thinking_enabled=True,
            ),
            database=self.database,
        )
        self.assertEqual(
            update_vlm_model(vlm["id"], VlmModelUpdate(name="已更新 VLM", max_tokens=2048), database=self.database)["max_tokens"],
            2048,
        )
        self.assertTrue((await test_vlm_model(vlm["id"], database=self.database))["ok"])
        self.assertTrue(delete_vlm_model(vlm["id"], database=self.database)["deleted"])

        dataset = create_dataset(
            DatasetCreate(code="DATASET_MAINTENANCE", name="数据集维护", purpose="TEST"),
            database=self.database,
        )
        item = self._upload_image(dataset["id"], "item.png", "OK", (30, 90, 160))
        self.assertEqual(
            update_dataset(dataset["id"], DatasetUpdate(name="数据集已更新"), database=self.database)["name"],
            "数据集已更新",
        )
        self.assertEqual(
            update_dataset_item(dataset["id"], item["id"], DatasetItemUpdate(ground_truth="NG"), database=self.database)["ground_truth"],
            "NG",
        )
        with self.assertRaises(HTTPException):
            self._upload_image(dataset["id"], "duplicate.png", "NG", (30, 90, 160))
        self.assertTrue(remove_dataset_item(dataset["id"], item["id"], database=self.database)["deleted"])

        training_set = create_dataset(
            DatasetCreate(
                code="HARNESS_TRAIN_SET",
                name="线束训练集",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="DETECTION",
            ),
            database=self.database,
        )
        for index in range(10):
            item = self._upload_image(
                training_set["id"],
                f"train_{index}.png",
                None,
                (index * 15, 80, 160),
            )
            update_dataset_item(
                training_set["id"],
                item["id"],
                DatasetItemUpdate(
                    annotation_status="LABELED",
                    annotation_json={
                        "boxes": (
                            [{"label": "harness", "x": 0.1, "y": 0.2, "width": 0.4, "height": 0.3}]
                            if index == 0
                            else []
                        )
                    },
                ),
                database=self.database,
            )
        model = create_vision_model(
            VisionModelCreate(
                code="AI_HARNESS_DETECTOR",
                name="AI 检测线束",
                task_type="YOLO_DETECTION",
                base_model="yolo11n.pt",
                labels_json=["harness"],
            ),
            database=self.database,
        )
        self.assertEqual(
            update_vision_model(model["id"], VisionModelUpdate(name="AI 检测线束（已维护）"), database=self.database)["name"],
            "AI 检测线束（已维护）",
        )
        manual_weights = self.temporary_root / "manual-1.0.pt"
        manual_weights.write_bytes(b"manual-weights")
        version = create_model_version(
            model["id"],
            VisionModelVersionCreate(
                version="manual-1.0",
                dataset_id=training_set["id"],
                weights_path=str(manual_weights),
            ),
            database=self.database,
        )
        self.assertEqual(
            publish_model_version(model["id"], version["id"], database=self.database)["status"],
            "PUBLISHED",
        )
        workflow = create_scenario(
            ScenarioCreate(name="YOLO 工作流场景", category="HARNESS", mode="WORKFLOW"),
            database=self.database,
        )
        add_node(
            workflow["version_id"],
            NodeCreate(
                node_key="detector",
                name="线束检测",
                node_type="VISION_MODEL",
                config_json={"model_version_id": version["id"], "expected_min_count": 1},
                sort_order=2,
            ),
            database=self.database,
        )
        publish_scenario_version(workflow["version_id"], database=self.database)
        workflow_image = self.temporary_root / "workflow.png"
        Image.new("RGB", (24, 24), "white").save(workflow_image)
        workflow_execution = await execute_scenario_version(
            workflow["version_id"],
            ScenarioExecuteRequest(image_path=str(workflow_image)),
            database=self.database,
        )
        self.assertEqual(workflow_execution["result"], "OK")
        self.assertEqual(workflow_execution["output_json"]["nodes"]["detector"]["model_version_id"], version["id"])
        job = queue_training_job(
            model["id"],
            TrainingJobCreate(dataset_id=training_set["id"], version="train-1.0", epochs=5),
            database=self.database,
        )
        self.assertEqual(job["split_summary"]["counts"], {"TRAIN": 7, "VAL": 2, "TEST": 1})
        self.database.expire_all()
        split_counts = Counter(
            self.database.get(DatasetItem, record["dataset_item_id"]).split
            for record in job["split_summary"]["manifest"]
        )
        self.assertEqual(split_counts, Counter({"TRAIN": 7, "VAL": 2, "TEST": 1}))
        worker = AutomationWorker()
        worker_module._gpu_free_memory_mb = lambda: []
        self.assertTrue(await worker.process_once())
        self.database.expire_all()
        self.assertEqual(self.database.get(AutomationJob, job["job_id"]).status, "WAITING_GPU")
        worker_module._gpu_free_memory_mb = lambda: [999999]
        worker._next_gpu_poll_at = 0.0
        self.assertTrue(await worker.process_once())
        self.database.expire_all()
        completed_job = self.database.get(AutomationJob, job["job_id"])
        self.assertEqual(completed_job.status, "COMPLETED")
        self.assertEqual(completed_job.result_json["model_version"]["status"], "DRAFT")
        self.assertEqual(completed_job.result_json["model_version"]["metrics_json"]["metrics/mAP50(B)"], 0.91)
        training_job_payload = get_automation_job(completed_job.id, database=self.database)
        self.assertIn("training_curve", training_job_payload["artifact_urls"])
        self.assertTrue(Path(get_training_artifact(completed_job.id, "training_curve", database=self.database).path).is_file())
        with self.assertRaises(HTTPException):
            get_training_artifact(completed_job.id, "best_weights", database=self.database)
        disposable_model = create_vision_model(
            VisionModelCreate(code="DISPOSABLE_MODEL", name="待删除模型", task_type="YOLO_CLASSIFICATION"),
            database=self.database,
        )
        self.assertTrue(delete_vision_model(disposable_model["id"], database=self.database)["deleted"])
