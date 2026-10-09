from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import datasets as datasets_module
from app.api.routes.datasets import (
    DatasetItemPreAnnotationRequest,
    pre_annotate_dataset_item,
)
from app.db.base import Base
from app.models.intelligence import Dataset, DatasetItem, VisionModel, VisionModelVersion


class DatasetPreAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
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

    def test_pre_annotation_returns_editable_candidates_without_persisting_truth(self) -> None:
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
        self.assertEqual(persisted_item.annotation_status, "PENDING")
        self.assertEqual(persisted_item.annotation_json, {})

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


if __name__ == "__main__":
    unittest.main()
