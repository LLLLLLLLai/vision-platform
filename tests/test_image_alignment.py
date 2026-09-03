import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.services.image_processing import align_image_to_reference, align_image_with_anchor


class ImageAlignmentTests(unittest.TestCase):
    def test_phase_correlation_restores_small_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.jpg"
            source_path = root / "source.jpg"
            destination_path = root / "aligned.jpg"
            reference = np.full((240, 320, 3), 20, dtype=np.uint8)
            cv2.rectangle(reference, (70, 60), (230, 180), (180, 80, 30), -1)
            cv2.circle(reference, (150, 120), 22, (220, 220, 220), -1)
            transform = np.float32([[1, 0, 12], [0, 1, -8]])
            shifted = cv2.warpAffine(
                reference,
                transform,
                (320, 240),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), shifted)

            aligned_path, metadata = align_image_to_reference(
                str(source_path),
                str(reference_path),
                destination_path,
                max_shift_ratio=0.10,
                minimum_response=0.01,
                max_dimension=320,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertTrue(aligned_path and aligned_path.is_file())
            aligned = cv2.imread(str(aligned_path))
            self.assertLess(
                float(np.mean(np.abs(aligned.astype(np.int16) - reference.astype(np.int16)))),
                4.0,
            )

    def test_anchor_feature_alignment_restores_translation_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            source_path = root / "source.png"
            destination_path = root / "aligned.png"
            generator = np.random.default_rng(7)
            reference = generator.integers(0, 80, size=(320, 420, 3), dtype=np.uint8)
            cv2.rectangle(reference, (120, 95), (300, 235), (235, 235, 235), -1)
            for center in ((150, 125), (270, 125), (150, 205), (270, 205)):
                cv2.circle(reference, center, 16, (20, 20, 20), -1)
                cv2.line(reference, (center[0] - 10, center[1]), (center[0] + 10, center[1]), (255, 80, 40), 3)
            rotation = cv2.getRotationMatrix2D((210, 160), 3.0, 1.0)
            rotation[:, 2] += np.array([10.0, -7.0])
            shifted = cv2.warpAffine(
                reference,
                rotation,
                (420, 320),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), shifted)
            anchor = type(
                "Anchor",
                (),
                {
                    "code": "ANCHOR_01",
                    "x_ratio": 0.25,
                    "y_ratio": 0.20,
                    "width_ratio": 0.50,
                    "height_ratio": 0.55,
                    "padding": 0,
                },
            )()

            aligned_path, metadata = align_image_with_anchor(
                str(source_path),
                str(reference_path),
                anchor,
                destination_path,
                max_shift_ratio=0.15,
                search_margin_ratio=0.25,
                minimum_inliers=6,
                minimum_inlier_ratio=0.25,
                maximum_rotation_degrees=8.0,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertTrue(aligned_path and aligned_path.is_file())
            aligned = cv2.imread(str(aligned_path))
            central = np.s_[35:285, 35:385]
            self.assertLess(
                float(np.mean(np.abs(aligned[central].astype(np.int16) - reference[central].astype(np.int16)))),
                18.0,
            )


if __name__ == "__main__":
    unittest.main()
