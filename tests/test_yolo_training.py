from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image

from app.services.yolo_training import (
    YoloTrainingError,
    build_training_spec,
    export_yolo_dataset,
    validate_training_dataset,
)


class YoloTrainingExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _image(self, name: str, color: str) -> Path:
        path = self.root / name
        Image.new("RGB", (100, 80), color).save(path)
        return path

    def _item(
        self,
        identifier: int,
        image_path: Path,
        *,
        split: str,
        annotation: dict,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=identifier,
            media_path=str(image_path),
            original_name=image_path.name,
            annotation_status="LABELED",
            annotation_json=annotation,
            split=split,
            content_hash=str(identifier),
            is_deleted=False,
        )

    def test_detection_export_uses_frozen_split_and_normalized_boxes(self) -> None:
        positive = self._item(
            1,
            self._image("positive.png", "white"),
            split="TRAIN",
            annotation={
                "boxes": [
                    {"label": "harness", "x": 10, "y": 16, "width": 40, "height": 24}
                ]
            },
        )
        negative = self._item(
            2,
            self._image("negative.png", "black"),
            split="VAL",
            annotation={"boxes": []},
        )
        validation = validate_training_dataset(
            task_type="YOLO_DETECTION",
            labels=["harness"],
            dataset_annotation_type="DETECTION",
            items=[positive, negative],
        )
        self.assertEqual(validation["positive_annotation_count"], 1)
        samples = [
            {
                "dataset_item_id": item.id,
                "media_path": item.media_path,
                "original_name": item.original_name,
                "annotation_status": item.annotation_status,
                "annotation_json": item.annotation_json,
                "split": item.split,
            }
            for item in (positive, negative)
        ]
        spec = build_training_spec(
            job_id=9,
            vision_model_id=1,
            model_code="AI_HARNESS",
            task_type="YOLO_DETECTION",
            base_model="yolo11n.pt",
            labels=["harness"],
            dataset_id=1,
            dataset_code="HARNESS",
            version="1.0",
            samples=samples,
            config={"epochs": 1, "image_size": 64},
            device="cpu",
            artifact_root=self.root / "runs",
        )

        yaml_path = export_yolo_dataset(spec)

        self.assertTrue(yaml_path.is_file())
        train_label = yaml_path.parent / "labels" / "train" / "1_positive.txt"
        val_label = yaml_path.parent / "labels" / "val" / "2_negative.txt"
        self.assertEqual(train_label.read_text(encoding="utf-8").strip(), "0 0.300000 0.350000 0.400000 0.300000")
        self.assertEqual(val_label.read_text(encoding="utf-8"), "")
        self.assertIn("train: images/train", yaml_path.read_text(encoding="utf-8"))

    def test_training_requires_complete_and_matching_annotations(self) -> None:
        sample = self._item(
            1,
            self._image("unlabeled.png", "white"),
            split="TRAIN",
            annotation={"boxes": [{"label": "unknown", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}]},
        )
        with self.assertRaisesRegex(YoloTrainingError, "不在模型类别列表"):
            validate_training_dataset(
                task_type="YOLO_DETECTION",
                labels=["harness"],
                dataset_annotation_type="DETECTION",
                items=[sample],
            )
