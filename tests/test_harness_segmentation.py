import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from app.services.harness_segmentation import (
    harness_segments_to_candidates,
    merge_harness_segmentations,
    segment_orange_harness,
)


class HarnessSegmentationTest(unittest.TestCase):
    def test_converts_segments_to_reviewable_candidates(self) -> None:
        result = harness_segments_to_candidates(
            {
                "mode": "GROUNDED_SAM2_WITH_COLOR_FALLBACK",
                "segments": [
                    {
                        "segment_id": "COLOR_1",
                        "label": "橙色线束",
                        "bbox": [0.1, 0.2, 0.4, 0.6],
                        "confidence": 0.91,
                        "area_ratio": 0.12,
                    },
                    {
                        "segment_id": "SAM2_1",
                        "label": "黑色线束",
                        "bbox": [0.5, 0.3, 0.7, 0.8],
                        "confidence": 0.76,
                        "area_ratio": 0.05,
                        "engine": "SAM2.1_HIERA_SMALL",
                    },
                ],
            }
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["source_segment_id"], "SAM2_1")
        self.assertEqual(result[0]["target_kind"], "HARNESS_SEGMENT")
        self.assertEqual(result[0]["x_ratio"], 0.5)
        self.assertEqual(result[0]["width_ratio"], 0.2)
        self.assertFalse(result[0]["batch_confirmable"])

    def test_merges_sam2_and_non_overlapping_color_segments(self) -> None:
        sam2 = {
            "segments": [
                {"segment_id": "S1", "bbox": [0.1, 0.1, 0.4, 0.4]}
            ]
        }
        color = {
            "segments": [
                {"segment_id": "C1", "bbox": [0.12, 0.12, 0.38, 0.38]},
                {"segment_id": "C2", "bbox": [0.6, 0.6, 0.8, 0.8]},
            ]
        }

        result = merge_harness_segmentations(sam2, color)

        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(
            result["mode"],
            "GROUNDED_SAM2_WITH_COLOR_FALLBACK",
        )

    def test_segments_orange_harness_and_writes_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "assembly.png"
            output_path = root / "results"
            image = np.full((500, 900, 3), 35, dtype=np.uint8)
            cv2.line(image, (80, 360), (430, 210), (0, 110, 245), 32)
            cv2.line(image, (430, 210), (820, 120), (0, 110, 245), 28)
            cv2.circle(image, (430, 210), 30, (0, 110, 245), -1)
            cv2.imwrite(str(image_path), image)

            result = segment_orange_harness(str(image_path), output_path)

            self.assertGreaterEqual(result["segment_count"], 1)
            self.assertEqual(result["mode"], "ORANGE_HARNESS_HSV")
            self.assertTrue(Path(result["mask_path"]).exists())
            self.assertTrue(Path(result["overlay_path"]).exists())
            segment = result["segments"][0]
            self.assertGreaterEqual(len(segment["polygon"]), 3)
            self.assertTrue(
                all(0.0 <= coordinate <= 1.0 for point in segment["polygon"] for coordinate in point)
            )
            self.assertGreater(segment["area_ratio"], 0.01)


if __name__ == "__main__":
    unittest.main()
