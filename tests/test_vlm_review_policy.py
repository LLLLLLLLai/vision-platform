import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.inspection import InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceImage
from app.services.inspection_engine import (
    InspectionEngine,
    should_run_vlm_review,
    similarity_review_band,
    vlm_review_status,
)


class VlmReviewPolicyTest(unittest.TestCase):
    def test_default_review_band_surrounds_similarity_threshold(self) -> None:
        lower, upper = similarity_review_band({}, 0.9)
        self.assertAlmostEqual(lower, 0.85)
        self.assertAlmostEqual(upper, 0.93)

    def test_review_only_runs_in_configured_band(self) -> None:
        rule = {
            "vlm_review_enabled": True,
            "vlm_review_lower": 0.84,
            "vlm_review_upper": 0.92,
        }
        self.assertTrue(should_run_vlm_review(0.88, rule, 0.9))
        self.assertFalse(should_run_vlm_review(0.95, rule, 0.9))
        self.assertFalse(
            should_run_vlm_review(
                0.88,
                {**rule, "vlm_review_enabled": False},
                0.9,
            )
        )

    def test_always_review_runs_without_primary_score(self) -> None:
        rule = {
            "vlm_review_enabled": True,
            "vlm_review_mode": "ALWAYS",
        }
        self.assertTrue(should_run_vlm_review(None, rule, 0.0))
        self.assertTrue(should_run_vlm_review(0.99, rule, 0.9))

    def test_forced_test_review_ignores_confidence_band(self) -> None:
        rule = {
            "vlm_review_enabled": True,
            "vlm_review_mode": "LOW_CONFIDENCE",
            "vlm_review_lower": 0.84,
            "vlm_review_upper": 0.92,
        }
        self.assertTrue(should_run_vlm_review(0.99, rule, 0.9, force=True))
        self.assertFalse(
            should_run_vlm_review(
                0.88,
                {**rule, "vlm_review_enabled": False},
                0.9,
                force=True,
            )
        )

    def test_uncertain_review_uses_fail_safe_result(self) -> None:
        response = {"result": {"parsed": {"result": "UNCERTAIN"}}}
        status, parsed = vlm_review_status(response, "NG")
        self.assertEqual(status, "NG")
        self.assertEqual(parsed["result"], "UNCERTAIN")

    def test_valid_review_overrides_primary_result(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "result": "OK",
                    "confidence": 0.96,
                    "reason": "Component is visible.",
                }
            }
        }
        status, _ = vlm_review_status(response, "NG")
        self.assertEqual(status, "OK")


class VlmReviewExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_payload_uses_dedicated_service_text(self) -> None:
        self.assertEqual(
            InspectionEngine._extract_ocr_text(
                {"model": "PP-OCRv5-mobile", "text": "DC 750V 40A"}
            ),
            "DC 750V 40A",
        )

    async def test_draft_roi_test_uses_unsaved_rules_and_returns_review(self) -> None:
        recipe = Recipe(
            id=1,
            code="DRAFT_RECIPE",
            name="Draft recipe",
            version="1.0",
            product_id=1,
            station_id=1,
        )
        roi = RegionOfInterest(
            id=7,
            recipe_id=1,
            code="ROI_7",
            name="ROI_7",
            object_type="HARNESS",
            x_ratio=0.1,
            y_ratio=0.1,
            width_ratio=0.8,
            height_ratio=0.8,
            padding=0,
        )

        class FakeAlgorithms:
            async def vlm_judge(self, *_args, **_kwargs):
                return {
                    "model": "qwen3-vl-4b",
                    "result": {
                        "parsed": {
                            "result": "OK",
                            "confidence": 0.94,
                            "reason": "Orange harness is visible.",
                        }
                    },
                }

        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "orange.jpg"
            Image.new("RGB", (100, 100), (255, 140, 0)).save(image_path)
            engine = InspectionEngine()
            engine.algorithms = FakeAlgorithms()
            result = await engine.test_draft_roi(
                None,
                recipe,
                roi,
                str(image_path),
                [{"type": "COLOR", "scene": "COLOR_ATTRIBUTE", "value": "orange"}],
                {
                    "enabled": True,
                    "mode": "LOW_CONFIDENCE",
                    "prompt": "Verify the orange harness color.",
                },
            )

        item = result["image_results"][0]["inspection_items"][0]
        self.assertEqual(
            result["image_results"][0]["roi_image_url"],
            f'/results/{result["request_id"]}/ROI_7.jpg',
        )
        self.assertEqual(item["capability"], "COLOR_RATIO")
        self.assertEqual(item["actual"]["vlm_review"]["status"], "OK")
        self.assertEqual(
            item["actual"]["vlm_review"]["prompt"],
            "Verify the orange harness color.",
        )

    async def test_forced_test_review_executes_outside_confidence_band(self) -> None:
        item = InspectionItem(
            roi_id=1,
            code="COLOR_FORCE_REVIEW",
            name="Forced review",
            inspection_type="COLOR",
            capability="COLOR_RATIO",
            expected_json={"color": "ORANGE"},
            rule_json={
                "vlm_review_enabled": True,
                "vlm_review_mode": "LOW_CONFIDENCE",
                "vlm_review_lower": 0.4,
                "vlm_review_upper": 0.6,
                "vlm_prompt": "Verify the orange color.",
            },
        )

        class FakeAlgorithms:
            async def vlm_judge(self, *_args, **_kwargs):
                return {
                    "model": "qwen3-vl-4b",
                    "result": {
                        "parsed": {
                            "result": "OK",
                            "confidence": 0.95,
                            "reason": "The expected color is visible.",
                        }
                    },
                }

        engine = InspectionEngine()
        engine.algorithms = FakeAlgorithms()
        result = await engine._apply_vlm_review(
            item,
            "roi.jpg",
            {
                "status": "OK",
                "actual": {"color": "ORANGE", "ratio": 0.99},
                "score": 0.99,
                "message": "Primary color check passed.",
            },
            primary_model="OpenCV",
            minimum=0.15,
            force_vlm_review=True,
        )

        self.assertEqual(result["actual"]["vlm_review"]["status"], "OK")
        self.assertEqual(
            result["actual"]["vlm_review"]["prompt"],
            "Verify the orange color.",
        )

    async def test_qwen_can_review_opencv_result(self) -> None:
        item = InspectionItem(
            roi_id=1,
            code="COLOR_CHECK",
            name="Harness color",
            inspection_type="COLOR",
            capability="COLOR_RATIO",
            expected_json={"color": "ORANGE"},
            rule_json={
                "vlm_review_enabled": True,
                "vlm_review_mode": "ALWAYS",
            },
        )

        class FakeAlgorithms:
            async def vlm_judge(self, *_args, **_kwargs):
                return {
                    "model": "qwen3-vl-4b",
                    "elapsed_ms": 320,
                    "result": {
                        "parsed": {
                            "result": "OK",
                            "confidence": 0.92,
                            "reason": "The cable is orange.",
                        }
                    },
                }

        engine = InspectionEngine()
        engine.algorithms = FakeAlgorithms()
        result = await engine._apply_vlm_review(
            item,
            "roi.jpg",
            {
                "status": "OK",
                "actual": {"color": "ORANGE", "ratio": 0.5},
                "score": 0.5,
                "message": "ORANGE ratio 0.5",
            },
            primary_model="OpenCV",
            minimum=0.15,
        )

        self.assertEqual(result["actual"]["primary_result"]["model"], "OpenCV")
        self.assertEqual(result["actual"]["vlm_review"]["status"], "OK")
        self.assertIn("prompt", result["actual"]["vlm_review"])
        self.assertEqual(
            result["actual"]["vlm_review"]["expected"],
            {"color": "ORANGE"},
        )

    async def test_qwen_can_resolve_similarity_boundary_case(self) -> None:
        database_engine = create_engine("sqlite://")
        Base.metadata.create_all(database_engine)
        database = Session(database_engine)
        group = ReferenceGroup(
            code="FUSE_400A",
            name="Fuse 400A",
            object_type="FUSE",
            class_code="FUSE_400A",
        )
        database.add(group)
        database.flush()
        database.add(
            ReferenceImage(
                group_id=group.id,
                image_path="reference.jpg",
                quality_status="READY",
            )
        )
        database.commit()
        item = InspectionItem(
            roi_id=1,
            code="FUSE_CHECK",
            name="Fuse check",
            inspection_type="EXISTENCE",
            capability="REFERENCE_SIMILARITY",
            reference_group_id=group.id,
            expected_json={"class_code": "FUSE_400A"},
            rule_json={
                "min_similarity": 0.9,
                "vlm_review_enabled": True,
                "vlm_review_lower": 0.85,
                "vlm_review_upper": 0.93,
            },
        )

        class FakeAlgorithms:
            async def similarity(self, *_args, **_kwargs):
                return {
                    "top1_similarity": 0.88,
                    "candidates": [{"reference_path": "reference.jpg"}],
                }

            async def vlm_judge(self, *_args, **_kwargs):
                return {
                    "model": "qwen3-vl-4b",
                    "elapsed_ms": 1200,
                    "result": {
                        "parsed": {
                            "result": "OK",
                            "confidence": 0.96,
                            "reason": "Expected fuse is installed.",
                        }
                    },
                }

        engine = InspectionEngine()
        engine.algorithms = FakeAlgorithms()
        result = await engine._reference_similarity(database, item, "roi.jpg")

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["actual"]["primary_status"], "NG")
        self.assertEqual(result["actual"]["vlm_review"]["status"], "OK")
        database.close()
        database_engine.dispose()


if __name__ == "__main__":
    unittest.main()
