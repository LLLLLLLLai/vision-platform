from __future__ import annotations

from pathlib import Path
import json
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
        train_label = yaml_path.parent / "labels" / "train" / "item_1.txt"
        val_label = yaml_path.parent / "labels" / "val" / "item_2.txt"
        self.assertEqual(train_label.read_text(encoding="utf-8").strip(), "0 0.300000 0.350000 0.400000 0.300000")
        self.assertEqual(val_label.read_text(encoding="utf-8"), "")
        self.assertIn("train: images/train", yaml_path.read_text(encoding="utf-8"))

    def test_long_names_export_all_tasks_without_changing_labels_or_originals(self) -> None:
        source = self._image("source.png", "white")
        source_bytes = source.read_bytes()
        original_name = "生产线_工艺配方_场景_ROI_产品条码_" * 30 + "same.png"
        annotations = {
            "YOLO_DETECTION": {"boxes": [{"label": "wire", "x": 10, "y": 16, "width": 40, "height": 24}]},
            "YOLO_SEGMENTATION": {"segments": [{"label": "wire", "points": [[10, 16], [50, 16], [50, 40]]}]},
            "YOLO_CLASSIFICATION": {"label": "wire"},
        }
        for index, (task_type, annotation) in enumerate(annotations.items()):
            with self.subTest(task_type=task_type):
                samples = [{
                    "dataset_item_id": identifier,
                    "media_path": str(source),
                    "original_name": original_name,
                    "annotation_status": "LABELED",
                    "annotation_json": {"label": "other"} if task_type == "YOLO_CLASSIFICATION" and identifier in (2, 4) else annotation,
                    "split": split,
                } for identifier, split in ((1, "TRAIN"), (2, "TRAIN"), (3, "VAL"), (4, "TEST"))]
                spec = build_training_spec(
                    job_id=index, vision_model_id=1, model_code="WIRE", task_type=task_type,
                    base_model="test.pt", labels=["wire", "other"], dataset_id=1, dataset_code="WIRE",
                    version="1.0", samples=samples, config={"epochs": 1}, device="cpu",
                    artifact_root=self.root / "runs",
                )
                old_export = spec.run_dir / "dataset" / "images" / "train"
                old_export.mkdir(parents=True)
                (old_export / "1_old_partial.png").write_bytes(source_bytes)
                first_path = export_yolo_dataset(spec)
                second_path = export_yolo_dataset(spec)
                self.assertEqual(first_path, second_path)
                dataset_dir = first_path if task_type == "YOLO_CLASSIFICATION" else first_path.parent
                self.assertIn("dataset_v2", dataset_dir.parts)
                images = list(dataset_dir.rglob("*.png"))
                self.assertEqual(len(images), 4)
                self.assertEqual({image.name for image in images}, {f"item_{identifier}.png" for identifier in range(1, 5)})
                for image in images:
                    self.assertEqual(image.read_bytes(), source_bytes)
                manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(len(manifest), 4)
                for row in manifest:
                    self.assertEqual(row["original_name"], original_name)
                    self.assertTrue((dataset_dir / row["exported_path"]).is_file())
                if task_type == "YOLO_CLASSIFICATION":
                    self.assertTrue((dataset_dir / "train" / "wire" / "item_1.png").is_file())
                    self.assertTrue((dataset_dir / "train" / "other" / "item_2.png").is_file())
                else:
                    labels = list((dataset_dir / "labels").rglob("*.txt"))
                    self.assertEqual(len(labels), 4)
                    self.assertTrue(all(label.read_text().startswith("0 ") for label in labels))
                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertTrue((old_export / "1_old_partial.png").exists())
                self.assertEqual(samples[0]["original_name"], original_name)

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
