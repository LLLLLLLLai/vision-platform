import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ocr_service.engine import PaddleOcrEngine


class OcrFallbackTests(unittest.TestCase):
    def test_v6_model_selection_uses_environment_configuration(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OCR_VERSION": "PP-OCRv6",
                "OCR_DETECTION_MODEL": "PP-OCRv6_medium_det",
                "OCR_RECOGNITION_MODEL": "PP-OCRv6_medium_rec",
                "OCR_USE_TEXTLINE_ORIENTATION": "true",
            },
            clear=False,
        ):
            engine = PaddleOcrEngine()

        self.assertEqual(engine.ocr_version, "PP-OCRv6")
        self.assertEqual(engine.detection_model_name, "PP-OCRv6_medium_det")
        self.assertEqual(engine.model_name, "PP-OCRv6_medium_rec")
        self.assertTrue(engine.use_textline_orientation)

    def test_expected_text_selects_enhanced_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "roi.jpg"
            Image.new("RGB", (64, 64), (220, 120, 80)).save(image_path)

            engine = PaddleOcrEngine()
            engine.pipeline = object()

            def predict(path: str) -> dict:
                if path.endswith("ocr_angle_180.png"):
                    return {
                        "text": "FU7前",
                        "lines": [{"text": "FU7前", "score": 0.99}],
                        "confidence": 0.99,
                    }
                return {"text": "", "lines": [], "confidence": None}

            setattr(engine, "_predict", predict)
            result = engine.recognize(str(image_path), expected_text="FU7")

        self.assertEqual(result["text"], "FU7前")
        self.assertTrue(result["preprocessing"]["fallback_used"])
        self.assertEqual(
            result["preprocessing"]["selected_variant"],
            "ROTATE_180_ENHANCED",
        )
        self.assertTrue(result["preprocessing"]["expected_matched"])


if __name__ == "__main__":
    unittest.main()
