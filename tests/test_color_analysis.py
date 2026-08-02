import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from app.services.image_processing import analyze_roi_color


class ColorAnalysisTests(unittest.TestCase):
    def test_detects_orange_roi_and_returns_preview_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "orange.png"
            image = np.full((120, 160, 3), (30, 120, 240), dtype=np.uint8)
            cv2.imwrite(str(image_path), image)
            roi = SimpleNamespace(
                x_ratio=0.0,
                y_ratio=0.0,
                width_ratio=1.0,
                height_ratio=1.0,
                padding=0,
            )

            result = analyze_roi_color(str(image_path), roi)

        self.assertEqual(result["color"], "ORANGE")
        self.assertEqual(result["display_name"], "橙色")
        self.assertGreater(result["ratio"], 0.9)
        self.assertRegex(result["hex"], r"^#[0-9A-F]{6}$")


if __name__ == "__main__":
    unittest.main()
