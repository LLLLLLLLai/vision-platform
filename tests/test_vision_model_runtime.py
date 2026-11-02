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

        self.assertNotIn("result", output)
        self.assertNotIn("reason", output)
        self.assertEqual(output["status"], "COMPLETED")
        self.assertEqual(output["detection_count"], 1)
        self.assertEqual(output["object_count"], 1)
        self.assertEqual(output["image"]["width"], 40)
        self.assertEqual(output["image"]["coordinate_space"], "PIXEL")
        self.assertEqual(output["objects"][0]["label"], "harness")
        self.assertEqual(output["objects"][0]["bbox"], [1.0, 2.0, 30.0, 34.0])
        self.assertEqual(output["objects"][0]["bbox_format"], "XYXY_PIXEL")
        self.assertNotIn("classification", output)
        self.assertNotIn("result", output["output_profile"]["output_keys"])

    def test_detection_with_no_objects_remains_a_prediction(self) -> None:
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

        self.assertNotIn("result", output)
        self.assertEqual(output["status"], "COMPLETED")
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

        self.assertNotIn("result", output)
        self.assertEqual(output["objects"][0]["mask"], [[0.1, 0.2], [0.3, 0.2], [0.3, 0.4]])
        self.assertEqual(output["objects"][0]["mask_format"], "POLYGON_NORMALIZED")
        self.assertTrue(output["output_profile"]["supports_mask"])
        self.assertNotIn("result", output["output_profile"]["output_keys"])

    def test_classification_output_exposes_top1_without_judging_expected_label(self) -> None:
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

        self.assertNotIn("result", output)
        self.assertEqual(output["task_type"], "YOLO_CLASSIFICATION")
        self.assertEqual(output["classification"]["top1_label"], "locked")
        self.assertEqual(output["classification"]["top1_confidence"], 0.94)
        self.assertEqual(output["predicted_label"], "locked")
        self.assertNotIn("reason", output)
        self.assertNotIn("objects", output)

    def test_classification_without_expected_label_keeps_prediction_but_not_ok(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "FU5", 1: "FU6"},
            boxes=None,
            masks=None,
            probs=SimpleNamespace(top1=0, top1conf=0.999992, top5=[0, 1], top5conf=[0.999992, 0.000008]),
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )
        spec = runtime.PublishedVisionModelSpec(
            version_id=6,
            model_id=2,
            model_code="AI_HARNESS_CLASSIFICATION",
            model_name="AI 检测线束分类",
            task_type="YOLO_CLASSIFICATION",
            labels=("FU5", "FU6"),
            weights_path=self.weights,
        )

        output = runtime.run_yolo_inference(spec, str(self.image))

        self.assertNotIn("result", output)
        self.assertEqual(output["predicted_label"], "FU5")
        self.assertEqual(output["classification"]["top1_confidence"], 0.999992)

    def test_classification_uses_checkpoint_names_when_card_order_differs(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "FU5", 1: "FU6", 2: "FU7"},
            boxes=None,
            masks=None,
            probs=SimpleNamespace(
                top1=0,
                top1conf=0.98,
                top5=[0, 2, 1],
                top5conf=[0.98, 0.01, 0.01],
            ),
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )
        spec = runtime.PublishedVisionModelSpec(
            version_id=7,
            model_id=2,
            model_code="AI_HARNESS_CLASSIFICATION",
            model_name="AI 检测线束分类",
            task_type="YOLO_CLASSIFICATION",
            labels=("FU7", "FU6", "FU5"),
            weights_path=self.weights,
        )

        output = runtime.run_yolo_inference(spec, str(self.image))

        self.assertEqual(output["classification"]["top1_class_id"], 0)
        self.assertEqual(output["classification"]["top1_label"], "FU5")
        self.assertEqual(output["classification"]["top5"][1]["label"], "FU7")
        self.assertEqual(output["predicted_label"], "FU5")

    def test_detection_uses_checkpoint_names_when_card_order_differs(self) -> None:
        fake_result = SimpleNamespace(
            names={0: "FU5", 1: "FU7"},
            boxes=SimpleNamespace(xyxy=[[1, 2, 30, 34]], conf=[0.9], cls=[0]),
            masks=None,
            probs=None,
        )
        runtime._load_yolo = lambda _weights: SimpleNamespace(
            predict=lambda **_kwargs: [fake_result]
        )
        spec = runtime.PublishedVisionModelSpec(
            version_id=8,
            model_id=3,
            model_code="AI_HARNESS_DETECTION",
            model_name="AI 检测线束",
            task_type="YOLO_DETECTION",
            labels=("FU7", "FU5"),
            weights_path=self.weights,
        )

        output = runtime.run_yolo_inference(spec, str(self.image))

        self.assertEqual(output["objects"][0]["class_id"], 0)
        self.assertEqual(output["objects"][0]["label"], "FU5")

    def test_classification_profile_does_not_advertise_bbox(self) -> None:
        profile = runtime.task_output_profile("YOLO_CLASSIFICATION")

        self.assertFalse(profile["supports_bbox"])
        self.assertTrue(profile["supports_classification"])
        self.assertNotIn("objects.0.bbox", profile["output_keys"])
        self.assertNotIn("result", profile["output_keys"])
        self.assertIn("predicted_label", profile["output_keys"])
