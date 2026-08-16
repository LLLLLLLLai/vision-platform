import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.services.reference_candidate_service import (
    image_quality_metrics,
    parse_candidate_decision,
    perceptual_hash,
    primary_rules_pass,
)
from app.models.inspection import DetectionItemResult


class ReferenceCandidatePolicyTest(unittest.TestCase):
    def test_accepts_structured_high_confidence_pass(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "result": "PASS",
                    "same_object": True,
                    "object_present": True,
                    "appearance_consistent": True,
                    "installation_consistent": True,
                    "critical_difference": False,
                    "image_quality_ok": True,
                    "confidence": 0.96,
                    "differences": [],
                    "reason": "Consistent with baseline.",
                }
            }
        }
        status, confidence, _, _ = parse_candidate_decision(response, 0.90)
        self.assertEqual(status, "ACCEPTED")
        self.assertAlmostEqual(confidence, 0.96)

    def test_low_confidence_pass_remains_uncertain(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "result": "PASS",
                    "same_object": True,
                    "object_present": True,
                    "appearance_consistent": True,
                    "installation_consistent": True,
                    "critical_difference": False,
                    "image_quality_ok": True,
                    "confidence": 0.72,
                }
            }
        }
        status, _, _, _ = parse_candidate_decision(response, 0.90)
        self.assertEqual(status, "UNCERTAIN")

    def test_rejects_visible_critical_difference(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "result": "REJECT",
                    "same_object": True,
                    "object_present": True,
                    "appearance_consistent": False,
                    "installation_consistent": True,
                    "critical_difference": True,
                    "image_quality_ok": True,
                    "confidence": 0.97,
                }
            }
        }
        status, _, _, _ = parse_candidate_decision(response, 0.90)
        self.assertEqual(status, "REJECTED")

    def test_quality_metrics_and_hash_are_stable(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "roi.png"
            image = Image.effect_noise((128, 128), 80).convert("RGB")
            image.save(path)
            quality = image_quality_metrics(str(path))
            self.assertTrue(quality["passed"])
            self.assertEqual(perceptual_hash(str(path)), perceptual_hash(str(path)))

    def test_vlm_override_cannot_hide_primary_rule_failure(self) -> None:
        row = DetectionItemResult(
            task_id=1,
            image_path="source.jpg",
            roi_id=1,
            inspection_item_id=1,
            status="OK",
            actual_json={"primary_status": "NG", "vlm_review": {"status": "OK"}},
        )
        self.assertFalse(primary_rules_pass([row]))


if __name__ == "__main__":
    unittest.main()
