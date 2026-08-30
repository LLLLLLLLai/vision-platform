from __future__ import annotations

import os
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter


class PaddleOcrEngine:
    def __init__(self) -> None:
        self.pipeline: Any = None
        self.device = os.getenv("OCR_DEVICE", "cpu")
        self.ocr_version = os.getenv("OCR_VERSION", "PP-OCRv5")
        self.detection_model_name = os.getenv(
            "OCR_DETECTION_MODEL",
            "PP-OCRv5_mobile_det",
        )
        self.recognition_model_name = os.getenv(
            "OCR_RECOGNITION_MODEL",
            "PP-OCRv5_mobile_rec",
        )
        self.model_name = self.recognition_model_name
        self.use_textline_orientation = self._environment_flag(
            "OCR_USE_TEXTLINE_ORIENTATION",
            default=False,
        )
        self.enable_mkldnn = self._environment_flag(
            "OCR_ENABLE_MKLDNN",
            default=self.device == "cpu",
        )

    @staticmethod
    def _environment_flag(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def load(self) -> None:
        from paddleocr import PaddleOCR

        self.pipeline = PaddleOCR(
            ocr_version=self.ocr_version,
            text_detection_model_name=self.detection_model_name,
            text_recognition_model_name=self.recognition_model_name,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=self.use_textline_orientation,
            device=self.device,
            enable_mkldnn=self.enable_mkldnn,
            cpu_threads=max(1, int(os.getenv("OCR_CPU_THREADS", "6"))),
        )

    @staticmethod
    def parse_result(results: list[Any]) -> dict[str, Any]:
        raw_lines: list[dict[str, Any]] = []
        for result in results:
            payload = getattr(result, "json", result)
            if not isinstance(payload, dict):
                continue
            data = payload.get("res", payload)
            texts = list(data.get("rec_texts") or [])
            scores = list(data.get("rec_scores") or [])
            boxes = list(data.get("rec_boxes") or [])
            for index, text in enumerate(texts):
                normalized = str(text).strip()
                if not normalized:
                    continue
                score = float(scores[index]) if index < len(scores) else None
                box = boxes[index] if index < len(boxes) else None
                if hasattr(box, "tolist"):
                    box = box.tolist()
                raw_lines.append({"text": normalized, "score": score, "box": box})

        minimum_line_confidence = float(os.getenv("OCR_MIN_LINE_CONFIDENCE", "0.40"))
        lines = [
            line
            for line in raw_lines
            if line["score"] is None or line["score"] >= minimum_line_confidence
        ]
        if not lines:
            lines = raw_lines

        scores = [line["score"] for line in lines if line["score"] is not None]
        return {
            "text": " ".join(line["text"] for line in lines),
            "lines": lines,
            "confidence": sum(scores) / len(scores) if scores else None,
            "raw_text": " ".join(line["text"] for line in raw_lines),
            "raw_lines": raw_lines,
            "filtered_noise_count": len(raw_lines) - len(lines),
        }

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())

    @classmethod
    def _matches_expected(cls, result: dict[str, Any], expected_text: str | None) -> bool:
        if not expected_text:
            return False
        expected = cls._normalize_text(expected_text)
        actual = cls._normalize_text(str(result.get("text") or ""))
        return bool(expected and actual and expected in actual)

    @staticmethod
    def _needs_fallback(result: dict[str, Any], expected_text: str | None) -> bool:
        if expected_text:
            return not PaddleOcrEngine._matches_expected(result, expected_text)
        return not result.get("text") or float(result.get("confidence") or 0.0) < 0.70

    def _predict(self, image_path: str) -> dict[str, Any]:
        results = list(
            self.pipeline.predict(
                image_path,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=self.use_textline_orientation,
            )
        )
        return self.parse_result(results)

    @staticmethod
    def _enhanced_variants(image: Image.Image, directory: Path) -> list[tuple[str, Path]]:
        variants: list[tuple[str, Path]] = []
        for angle in (90, 180, 270):
            rotated = image.rotate(angle, expand=True, fillcolor=(245, 245, 245))
            scale = 4 if max(rotated.size) <= 512 else 2
            enlarged = rotated.resize(
                (rotated.width * scale, rotated.height * scale),
                Image.Resampling.LANCZOS,
            )
            enhanced = ImageEnhance.Contrast(enlarged).enhance(1.8)
            enhanced = ImageEnhance.Sharpness(enhanced).enhance(2.0)
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3))
            variant_path = directory / f"ocr_angle_{angle}.png"
            enhanced.save(variant_path)
            variants.append((f"ROTATE_{angle}_ENHANCED", variant_path))
        return variants

    def recognize(self, image_path: str, expected_text: str | None = None) -> dict[str, Any]:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Image does not exist: {path}")
        if self.pipeline is None:
            raise RuntimeError("PaddleOCR model is not loaded.")

        original_result = self._predict(str(path))
        candidates = [("ORIGINAL", original_result)]
        if not self._needs_fallback(original_result, expected_text):
            return {
                **original_result,
                "preprocessing": {
                    "fallback_used": False,
                    "selected_variant": "ORIGINAL",
                    "expected_matched": self._matches_expected(
                        original_result,
                        expected_text,
                    ),
                },
            }

        with Image.open(path) as source_image, TemporaryDirectory(prefix="vision-ocr-") as temporary_directory:
            image = source_image.convert("RGB")
            for variant_name, variant_path in self._enhanced_variants(
                image,
                Path(temporary_directory),
            ):
                candidates.append((variant_name, self._predict(str(variant_path))))

        matching_candidates = [
            candidate
            for candidate in candidates
            if self._matches_expected(candidate[1], expected_text)
        ]
        selected_name, selected_result = max(
            matching_candidates or candidates,
            key=lambda candidate: (
                bool(candidate[1].get("text")),
                float(candidate[1].get("confidence") or 0.0),
                len(str(candidate[1].get("text") or "")),
            ),
        )
        return {
            **selected_result,
            "preprocessing": {
                "fallback_used": True,
                "selected_variant": selected_name,
                "expected_matched": self._matches_expected(selected_result, expected_text),
                "candidates": [
                    {
                        "variant": name,
                        "text": result.get("text", ""),
                        "confidence": result.get("confidence"),
                        "expected_matched": self._matches_expected(result, expected_text),
                    }
                    for name, result in candidates
                ],
            },
        }
