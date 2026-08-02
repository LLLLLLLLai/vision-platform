from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class PaddleOcrEngine:
    model_name = "PP-OCRv5-mobile"

    def __init__(self) -> None:
        self.pipeline: Any = None
        self.device = os.getenv("OCR_DEVICE", "cpu")

    def load(self) -> None:
        from paddleocr import PaddleOCR

        self.pipeline = PaddleOCR(
            ocr_version="PP-OCRv5",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=self.device,
            enable_mkldnn=self.device == "cpu",
            cpu_threads=max(1, int(os.getenv("OCR_CPU_THREADS", "6"))),
        )

    @staticmethod
    def parse_result(results: list[Any]) -> dict[str, Any]:
        lines: list[dict[str, Any]] = []
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
                lines.append({"text": normalized, "score": score, "box": box})
        scores = [line["score"] for line in lines if line["score"] is not None]
        return {
            "text": " ".join(line["text"] for line in lines),
            "lines": lines,
            "confidence": sum(scores) / len(scores) if scores else None,
        }

    def recognize(self, image_path: str) -> dict[str, Any]:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Image does not exist: {path}")
        if self.pipeline is None:
            raise RuntimeError("PaddleOCR model is not loaded.")
        results = list(
            self.pipeline.predict(
                str(path),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        )
        return self.parse_result(results)
