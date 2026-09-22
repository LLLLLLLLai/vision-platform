from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PIL import Image

from app.services import vision_model_runtime as runtime


class VisionModelRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.weights = self.root / "best.pt"
        self.weights.write_bytes(b"fake weights")
        self.image = self.root / "sample.png"
        Image.new("RGB", (40, 40), "white").save(self.image)
        self.original_loader = runtime._load_yolo

    def tearDown(self) -> None:
        runtime._load_yolo = self.original_loader
        runtime.clear_model_cache()
        self.temporary_directory.cleanup()

    def _spec(self) -> runtime.PublishedVisionModelSpec:
        return runtime.PublishedVisionModelSpec(
            version_id=3,
            model_id=1,
            model_code="AI_HARNESS",
            model_name="AI 检测线束",
            task_type="YOLO_DETECTION",
            labels=("harness",),
            weights_path=self.weights,
        )

    def test_detection_output_is_workflow_friendly(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "harness"},
            boxes=SimpleNamespace(
                xyxy=[[1.0, 2.0, 30.0, 34.0]],
                conf=[0.93],
                cls=[0],
            ),
            masks=None,
            probs=None,
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )

        output = runtime.run_yolo_inference(
            self._spec(),
            str(self.image),
            config={"expected_min_count": 1, "confidence": 0.2},
        )

        self.assertEqual(output["result"], "OK")
        self.assertEqual(output["detection_count"], 1)
        self.assertEqual(output["objects"][0]["label"], "harness")
        self.assertEqual(output["objects"][0]["bbox"], [1.0, 2.0, 30.0, 34.0])

    def test_detection_count_failure_is_reported_without_runtime_error(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "harness"},
            boxes=SimpleNamespace(xyxy=[], conf=[], cls=[]),
            masks=None,
            probs=None,
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )

        output = runtime.run_yolo_inference(
            self._spec(),
            str(self.image),
            config={"expected_min_count": 1},
        )

        self.assertEqual(output["result"], "NG")
        self.assertEqual(output["detection_count"], 0)

    def test_segmentation_mask_array_is_serialized(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "harness"},
            boxes=SimpleNamespace(xyxy=[[1.0, 2.0, 30.0, 34.0]], conf=[0.8], cls=[0]),
            masks=SimpleNamespace(xyn=[[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4]]]),
            probs=None,
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )
        spec = runtime.PublishedVisionModelSpec(
            version_id=4,
            model_id=1,
            model_code="AI_HARNESS_SEG",
            model_name="AI 线束分割",
            task_type="YOLO_SEGMENTATION",
            labels=("harness",),
            weights_path=self.weights,
        )

        output = runtime.run_yolo_inference(
            spec,
            str(self.image),
            config={"expected_min_count": 1},
        )

        self.assertEqual(output["result"], "OK")
        self.assertEqual(output["objects"][0]["mask"], [[0.1, 0.2], [0.3, 0.2], [0.3, 0.4]])

    def test_classification_output_exposes_top1_for_workflow_nodes(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "locked", 1: "unlocked"},
            boxes=None,
            masks=None,
            probs=SimpleNamespace(
                top1=0,
                top1conf=0.94,
                top5=[0, 1],
                top5conf=[0.94, 0.06],
            ),
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )
        spec = runtime.PublishedVisionModelSpec(
            version_id=5,
            model_id=1,
            model_code="AI_HARNESS_LOCK",
            model_name="AI 检测线束锁付",
            task_type="YOLO_CLASSIFICATION",
            labels=("locked", "unlocked"),
            weights_path=self.weights,
        )

        output = runtime.run_yolo_inference(
            spec,
            str(self.image),
            config={"expected_label": "locked", "confidence": 0.2},
        )

        self.assertEqual(output["result"], "OK")
        self.assertEqual(output["task_type"], "YOLO_CLASSIFICATION")
        self.assertEqual(output["classification"]["top1_label"], "locked")
        self.assertEqual(output["classification"]["top1_confidence"], 0.94)

    def test_classification_profile_does_not_advertise_bbox(self) -> None:
        profile = runtime.task_output_profile("YOLO_CLASSIFICATION")

        self.assertFalse(profile["supports_bbox"])
        self.assertTrue(profile["supports_classification"])
        self.assertNotIn("objects.0.bbox", profile["output_keys"])
