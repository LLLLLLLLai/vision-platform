from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import datasets as datasets_module
from app.api.routes.datasets import (
    DatasetBatchPreAnnotationRequest,
    DatasetItemPreAnnotationRequest,
    create_batch_pre_annotation_job,
    latest_batch_pre_annotation_job,
    pre_annotate_dataset_item,
)
from app.db.base import Base
from app.db.init_db import migrate_ai_annotations
from app.db.session import get_db
from app.models.intelligence import Dataset, DatasetItem, VisionModel, VisionModelVersion
from app.services import automation_worker as worker_module
from app.services.automation_worker import AutomationWorker
from app.services.yolo_training import validate_training_dataset
from app.services.vision_model_runtime import VisionModelRuntimeError


class DatasetPreAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        from sqlalchemy.pool import StaticPool

        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.image_path = root / "sample.png"
        Image.new("RGB", (200, 100), "white").save(self.image_path)
        self.weights_path = root / "model.pt"
        self.weights_path.write_bytes(b"test weights")

        self.dataset = Dataset(
            code="PRE_ANNOTATION_DATASET",
            name="自动标注测试集",
            purpose="TRAIN",
            media_type="IMAGE",
            annotation_type="DETECTION",
            label_schema_json=["harness"],
        )
        self.database.add(self.dataset)
        self.database.flush()
        self.item = DatasetItem(
            dataset_id=self.dataset.id,
            media_path=str(self.image_path),
            original_name="sample.png",
            media_type="IMAGE",
            annotation_status="PENDING",
            annotation_json={},
        )
        self.database.add(self.item)
        self.model = VisionModel(
            code="YOLO_HARNESS",
            name="线束检测模型",
            task_type="YOLO_DETECTION",
            labels_json=["harness", "connector"],
        )
        self.database.add(self.model)
        self.database.flush()
        self.version = VisionModelVersion(
            vision_model_id=self.model.id,
            version="1.0",
            status="PUBLISHED",
            weights_path=str(self.weights_path),
        )
        self.database.add(self.version)
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _request(self) -> DatasetItemPreAnnotationRequest:
        return DatasetItemPreAnnotationRequest(
            vision_model_version_id=self.version.id,
            confidence=0.4,
        )

    def test_pre_annotation_saves_editable_labels_directly_for_training(self) -> None:
        model_output = {
            "image": {"width": 200, "height": 100},
            "objects": [
                {
                    "label": "harness",
                    "confidence": 0.93,
                    "bbox": [20, 10, 120, 60],
                },
                {
                    "label": "connector",
                    "confidence": 0.81,
                    "bbox": [150, 20, 190, 80],
                },
            ],
            "object_count": 2,
            "confidence": 0.93,
            "classification": None,
        }
        with patch.object(datasets_module, "run_yolo_inference", return_value=model_output):
            result = pre_annotate_dataset_item(
                self.dataset.id,
                self.item.id,
                self._request(),
                database=self.database,
            )

        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["model"]["name"], "线束检测模型")
        self.assertEqual(result["unknown_labels"], ["connector"])
        first_box = result["annotation"]["boxes"][0]
        self.assertEqual(first_box["label"], "harness")
        self.assertEqual(first_box["x"], 0.1)
        self.assertEqual(first_box["y"], 0.1)
        self.assertEqual(first_box["width"], 0.5)
        self.assertEqual(first_box["height"], 0.5)
        self.assertTrue(first_box["auto_generated"])

        persisted_item = self.database.get(DatasetItem, self.item.id)
        self.assertEqual(persisted_item.annotation_status, "LABELED")
        self.assertEqual(persisted_item.annotation_json, result["annotation"])
        self.assertFalse(persisted_item.annotation_json["_meta"]["review_required"])
        self.assertTrue(persisted_item.annotation_json["_meta"]["auto_accepted"])
        self.assertNotIn("confirmed_at", persisted_item.annotation_json["_meta"])
        self.assertIsNone(persisted_item.ground_truth)
        self.assertEqual(result["item"]["annotation_status"], "LABELED")
        self.assertEqual(result["labels"], ["harness", "connector"])
        training = validate_training_dataset(
            task_type="YOLO_DETECTION", labels=result["labels"],
            dataset_annotation_type="DETECTION", items=[persisted_item],
        )
        self.assertEqual(training["positive_annotation_count"], 2)

    def test_pre_annotation_rejects_model_with_wrong_task_type(self) -> None:
        self.model.task_type = "YOLO_CLASSIFICATION"
        self.database.commit()

        with self.assertRaises(HTTPException) as captured:
            pre_annotate_dataset_item(
                self.dataset.id,
                self.item.id,
                self._request(),
                database=self.database,
            )

        self.assertEqual(captured.exception.status_code, 400)
        self.assertIn("YOLO_DETECTION", str(captured.exception.detail))

    def test_segmentation_candidates_keep_only_masks(self) -> None:
        self.dataset.annotation_type = "SEGMENTATION"
        self.model.task_type = "YOLO_SEGMENTATION"
        self.database.commit()
        model_output = {
            "image": {"width": 200, "height": 100},
            "objects": [
                {
                    "label": "harness",
                    "confidence": 0.93,
                    "mask": [[0.1, 0.1], [0.8, 0.1], [0.7, 0.7]],
                },
                {"label": "connector", "confidence": 0.5},
            ],
            "object_count": 2,
            "confidence": 0.93,
            "classification": None,
        }
        with patch.object(datasets_module, "run_yolo_inference", return_value=model_output):
            result = pre_annotate_dataset_item(
                self.dataset.id,
                self.item.id,
                self._request(),
                database=self.database,
            )

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["annotation"]["segments"][0]["label"], "harness")
        self.assertTrue(result["warnings"])
        self.assertEqual(self.item.annotation_status, "LABELED")
        training = validate_training_dataset(
            task_type="YOLO_SEGMENTATION", labels=["harness"],
            dataset_annotation_type="SEGMENTATION", items=[self.item],
        )
        self.assertEqual(training["positive_annotation_count"], 1)

    def test_batch_preserves_existing_and_saves_new_annotations_without_review(self) -> None:
        original = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}]}
        self.item.annotation_json = original
        self.item.annotation_status = "LABELED"
        second = DatasetItem(
            dataset_id=self.dataset.id,
            media_path=str(self.image_path),
            original_name="second.png",
            media_type="IMAGE",
            annotation_status="PENDING",
            annotation_json={},
        )
        self.database.add(second)
        self.database.commit()
        output = {
            "image": {"width": 200, "height": 100},
            "objects": [{"label": "harness", "confidence": 0.92, "bbox": [20, 10, 120, 60]}],
        }
        request = DatasetBatchPreAnnotationRequest(
            vision_model_version_id=self.version.id,
            existing_annotations="PRESERVE",
        )
        queued = create_batch_pre_annotation_job(self.dataset.id, request, database=self.database)
        self.assertEqual(queued["result"]["total"], 2)
        with self.assertRaises(HTTPException) as captured:
            create_batch_pre_annotation_job(self.dataset.id, request, database=self.database)
        self.assertEqual(captured.exception.status_code, 409)

        with patch.object(worker_module, "run_yolo_inference", return_value=output) as inference:
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database,
                self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.database.refresh(self.item)
        self.database.refresh(second)
        self.assertEqual(self.item.annotation_json, original)
        self.assertEqual(self.item.annotation_status, "LABELED")
        self.assertEqual(second.annotation_status, "LABELED")
        self.assertFalse(second.annotation_json["_meta"]["review_required"])
        self.assertEqual(inference.call_count, 2)
        latest = latest_batch_pre_annotation_job(self.dataset.id, database=self.database)
        self.assertEqual(latest["status"], "COMPLETED")
        self.assertEqual(latest["result"]["labeled"], 1)
        self.assertEqual(latest["result"]["skipped"], 1)

    def test_batch_preserve_merges_new_boxes_without_losing_manual_boxes(self) -> None:
        original = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}]}
        self.item.annotation_json = original
        self.item.annotation_status = "LABELED"
        self.database.commit()
        output = {
            "image": {"width": 200, "height": 100},
            "objects": [
                {"label": "harness", "confidence": 0.93, "bbox": [20, 10, 120, 60]},
                {"label": "connector", "confidence": 0.9, "bbox": [150, 20, 190, 80]},
            ],
        }
        queued = create_batch_pre_annotation_job(
            self.dataset.id,
            DatasetBatchPreAnnotationRequest(
                vision_model_version_id=self.version.id,
                existing_annotations="PRESERVE",
            ),
            database=self.database,
        )
        with patch.object(worker_module, "run_yolo_inference", return_value=output):
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database,
                self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.database.refresh(self.item)
        self.assertEqual(self.item.annotation_status, "LABELED")
        self.assertEqual([box["label"] for box in self.item.annotation_json["boxes"]], ["harness", "connector"])
        self.assertEqual(self.item.annotation_json["_meta"]["previous_annotation"]["boxes"], original["boxes"])

    def test_batch_replace_keeps_previous_annotation_backup(self) -> None:
        previous = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}]}
        self.item.annotation_json = previous
        self.item.annotation_status = "LABELED"
        self.database.commit()
        output = {
            "image": {"width": 200, "height": 100},
            "objects": [{"label": "harness", "confidence": 0.95, "bbox": [30, 10, 130, 60]}],
        }
        request = DatasetBatchPreAnnotationRequest(
            vision_model_version_id=self.version.id,
            existing_annotations="REPLACE",
        )
        queued = create_batch_pre_annotation_job(self.dataset.id, request, database=self.database)
        with patch.object(worker_module, "run_yolo_inference", return_value=output):
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database,
                self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.database.refresh(self.item)
        self.assertEqual(self.item.annotation_status, "LABELED")
        self.assertEqual(self.item.annotation_json["_meta"]["previous_annotation"]["boxes"], previous["boxes"])
        self.assertEqual(self.item.annotation_json["boxes"][0]["x"], 0.15)

    def test_batch_keeps_old_annotation_when_inference_fails(self) -> None:
        previous = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}]}
        self.item.annotation_json = previous
        self.item.annotation_status = "LABELED"
        self.database.commit()
        queued = create_batch_pre_annotation_job(
            self.dataset.id,
            DatasetBatchPreAnnotationRequest(
                vision_model_version_id=self.version.id,
                existing_annotations="REPLACE",
            ),
            database=self.database,
        )
        with patch.object(worker_module, "run_yolo_inference", side_effect=RuntimeError("model offline")):
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database,
                self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.database.refresh(self.item)
        self.assertEqual(self.item.annotation_json, previous)
        self.assertEqual(self.item.annotation_status, "LABELED")
        latest = latest_batch_pre_annotation_job(self.dataset.id, database=self.database)
        self.assertEqual(latest["status"], "FAILED")
        self.assertEqual(latest["result"]["failed"], 1)

    def test_single_inference_failure_does_not_label_or_overwrite_item(self) -> None:
        with patch.object(datasets_module, "run_yolo_inference", side_effect=VisionModelRuntimeError("offline")):
            with self.assertRaises(HTTPException):
                pre_annotate_dataset_item(self.dataset.id, self.item.id, self._request(), database=self.database)
        self.assertEqual(self.item.annotation_status, "PENDING")
        self.assertEqual(self.item.annotation_json, {})

    def test_empty_results_do_not_destroy_previous_annotation(self) -> None:
        previous = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}]}
        self.item.annotation_json = previous
        self.item.annotation_status = "LABELED"
        self.database.commit()
        output = {"image": {"width": 200, "height": 100}, "objects": []}
        with patch.object(datasets_module, "run_yolo_inference", return_value=output):
            with self.assertRaises(HTTPException) as captured:
                pre_annotate_dataset_item(self.dataset.id, self.item.id, self._request(), database=self.database)
        self.assertEqual(captured.exception.status_code, 422)
        self.assertEqual(self.item.annotation_json, previous)
        queued = create_batch_pre_annotation_job(self.dataset.id, DatasetBatchPreAnnotationRequest(
            vision_model_version_id=self.version.id, existing_annotations="REPLACE",
        ), database=self.database)
        with patch.object(worker_module, "run_yolo_inference", return_value=output):
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database, self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.assertEqual(self.item.annotation_json, previous)
        self.assertEqual(self.item.annotation_status, "LABELED")

    def test_single_replace_backs_up_old_annotation_and_invalidates_augmentation(self) -> None:
        previous = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}],
                    "_meta": {"confirmed_at": "old-manual-confirmation"}}
        self.item.annotation_json = previous
        self.item.annotation_status = "LABELED"
        self.database.commit()
        output = {"image": {"width": 200, "height": 100},
                  "objects": [{"label": "harness", "confidence": 0.9, "bbox": [30, 10, 130, 60]}]}
        with patch.object(datasets_module, "run_yolo_inference", return_value=output), \
                patch.object(datasets_module, "_invalidate_augmented_children", return_value=1) as invalidate:
            result = pre_annotate_dataset_item(self.dataset.id, self.item.id, self._request(), database=self.database)
        invalidate.assert_called_once_with(self.database, dataset_id=self.dataset.id, source_id=self.item.id)
        self.assertEqual(result["annotation"]["boxes"][0]["x"], 0.15)
        self.assertEqual(result["annotation"]["_meta"]["previous_annotation"], previous)
        self.assertNotIn("confirmed_at", result["annotation"]["_meta"])

    def test_classification_uses_canonical_dataset_label_without_review(self) -> None:
        self.dataset.annotation_type = "CLASSIFICATION"
        self.model.task_type = "YOLO_CLASSIFICATION"
        self.dataset.label_schema_json = ["FU5", "FU6"]
        self.database.commit()
        with patch.object(datasets_module, "run_yolo_inference", return_value={"classification": {"top1_label": "fu5"}}):
            result = pre_annotate_dataset_item(self.dataset.id, self.item.id, self._request(), database=self.database)
        self.assertEqual(result["annotation"]["label"], "FU5")
        self.assertEqual(result["annotation_status"], "LABELED")
        self.assertFalse(result["annotation"]["_meta"]["review_required"])

    def test_unknown_classification_does_not_become_trainable(self) -> None:
        self.dataset.annotation_type = "CLASSIFICATION"
        self.model.task_type = "YOLO_CLASSIFICATION"
        self.database.commit()
        with patch.object(datasets_module, "run_yolo_inference", return_value={"classification": {"top1_label": "unknown"}}):
            with self.assertRaises(HTTPException):
                pre_annotate_dataset_item(self.dataset.id, self.item.id, self._request(), database=self.database)
        self.assertEqual(self.item.annotation_status, "PENDING")
        self.assertEqual(self.item.annotation_json, {})

    def test_http_single_annotation_persists_without_followup_put(self) -> None:
        app = FastAPI()
        app.include_router(datasets_module.router, prefix="/api/v1/datasets")
        app.dependency_overrides[get_db] = lambda: self.database
        output = {"image": {"width": 200, "height": 100},
                  "objects": [{"label": "harness", "confidence": 0.9, "bbox": [30, 10, 130, 60]}]}
        with TestClient(app) as client, patch.object(datasets_module, "run_yolo_inference", return_value=output):
            response = client.post(
                f"/api/v1/datasets/{self.dataset.id}/items/{self.item.id}/pre-annotations",
                json={"vision_model_version_id": self.version.id},
            )
            self.assertEqual(response.status_code, 200)
            dataset_response = client.get(f"/api/v1/datasets/{self.dataset.id}")
            self.assertEqual(dataset_response.status_code, 200)
        item = dataset_response.json()["items"][0]
        self.assertEqual(item["annotation_status"], "LABELED")
        self.assertEqual(item["annotation_json"]["boxes"][0]["label"], "harness")

    def test_batch_segmentation_is_directly_trainable(self) -> None:
        self.dataset.annotation_type = "SEGMENTATION"
        self.model.task_type = "YOLO_SEGMENTATION"
        self.database.commit()
        queued = create_batch_pre_annotation_job(self.dataset.id, DatasetBatchPreAnnotationRequest(
            vision_model_version_id=self.version.id,
        ), database=self.database)
        output = {"objects": [{"label": "harness", "confidence": 0.95,
                               "mask": [[0.1, 0.1], [0.8, 0.1], [0.7, 0.7]]}]}
        with patch.object(worker_module, "run_yolo_inference", return_value=output):
            asyncio.run(AutomationWorker()._pre_annotate_dataset(
                self.database, self.database.get(worker_module.AutomationJob, queued["id"]),
            ))
        self.assertEqual(self.item.annotation_status, "LABELED")
        self.assertFalse(self.item.annotation_json["_meta"]["review_required"])
        training = validate_training_dataset(task_type="YOLO_SEGMENTATION", labels=["harness"],
                                             dataset_annotation_type="SEGMENTATION", items=[self.item])
        self.assertEqual(training["sample_count"], 1)

    def test_batch_classification_preserve_keeps_label_replace_uses_prediction(self) -> None:
        self.dataset.annotation_type = "CLASSIFICATION"
        self.model.task_type = "YOLO_CLASSIFICATION"
        self.dataset.label_schema_json = ["FU5", "FU6"]
        self.item.annotation_json = {"label": "FU5"}
        self.item.annotation_status = "LABELED"
        self.database.commit()
        for mode, expected in (("PRESERVE", "FU5"), ("REPLACE", "FU6")):
            with self.subTest(mode=mode):
                queued = create_batch_pre_annotation_job(self.dataset.id, DatasetBatchPreAnnotationRequest(
                    vision_model_version_id=self.version.id, existing_annotations=mode,
                ), database=self.database)
                with patch.object(worker_module, "run_yolo_inference", return_value={"classification": {"top1_label": "FU6"}}):
                    asyncio.run(AutomationWorker()._pre_annotate_dataset(
                        self.database, self.database.get(worker_module.AutomationJob, queued["id"]),
                    ))
                self.assertEqual(self.item.annotation_status, "LABELED")
                self.assertEqual(self.item.annotation_json["label"], expected)
                self.assertNotIn("ai_candidate_label", self.item.annotation_json.get("_meta", {}))

    def test_legacy_ai_labels_migrate_once_without_touching_unlabeled_collection(self) -> None:
        self.item.annotation_status = "AI_SUGGESTED"
        self.item.annotation_json = {"boxes": [{"label": "harness", "x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}],
                                     "_meta": {"review_required": True, "model_version_id": self.version.id}}
        pending = DatasetItem(dataset_id=self.dataset.id, media_path=str(self.image_path),
                              original_name="collected.png", media_type="IMAGE", source="AUTO_ROI",
                              annotation_status="PENDING", annotation_json={})
        self.database.add(pending)
        self.database.commit()
        self.assertEqual(migrate_ai_annotations(self.database), 1)
        self.assertEqual(self.item.annotation_status, "LABELED")
        self.assertFalse(self.item.annotation_json["_meta"]["review_required"])
        self.assertEqual(self.item.annotation_json["_meta"]["model_version_id"], self.version.id)
        self.assertEqual(pending.annotation_status, "PENDING")
        revision = self.dataset.revision
        self.assertEqual(migrate_ai_annotations(self.database), 0)
        self.assertEqual(self.dataset.revision, revision)

    def test_invalid_legacy_ai_annotations_remain_unlabeled(self) -> None:
        self.item.annotation_status = "AI_SUGGESTED"
        self.item.annotation_json = {"boxes": [], "_meta": {"review_required": True}}
        self.database.commit()
        self.assertEqual(migrate_ai_annotations(self.database), 0)
        self.assertEqual(self.item.annotation_status, "PENDING")
        self.assertIn("migration_error", self.item.annotation_json["_meta"])


if __name__ == "__main__":
    unittest.main()
