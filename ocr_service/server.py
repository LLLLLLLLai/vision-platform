import asyncio
from io import BytesIO
import logging
import math
import os
from pathlib import Path
import secrets
import threading

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


app = FastAPI(title="Vision Platform PaddleOCR")
logger = logging.getLogger("uvicorn.error")
_engine = None
_engine_lock = threading.Lock()
_admission = threading.BoundedSemaphore(2)


def _model_directory(kind: str, name: str) -> str:
    configured = os.getenv(f"OCR_{kind}_MODEL_DIR", "").strip()
    local_root = Path(__file__).resolve().parents[1] / "vision-models" / "paddleocr"
    candidates = [local_root / name, local_root / f"{name}_infer", Path.home() / ".paddlex" / "official_models" / name]
    directory = Path(configured).expanduser() if configured else next((path for path in candidates if path.is_dir()), candidates[-1])
    if not directory.is_dir():
        raise RuntimeError(f"OCR_{kind}_MODEL_DIR 未配置可用模型目录，请先部署 PaddleOCR 模型。")
    return str(directory.resolve())


def _get_engine():
    global _engine
    if _engine is None:
        from paddleocr import PaddleOCR

        detection_name = os.getenv("OCR_DETECTION_MODEL_NAME", "PP-OCRv5_mobile_det")
        recognition_name = os.getenv("OCR_RECOGNITION_MODEL_NAME", "PP-OCRv5_mobile_rec")
        _engine = PaddleOCR(
            text_detection_model_name=detection_name,
            text_detection_model_dir=_model_directory("DETECTION", detection_name),
            text_recognition_model_name=recognition_name,
            text_recognition_model_dir=_model_directory("RECOGNITION", recognition_name),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=os.getenv("OCR_DEVICE", "cpu"),
            enable_mkldnn=False,
            cpu_threads=4,
        )
    return _engine


def recognize_image(image: np.ndarray) -> dict:
    with _engine_lock:
        predictions = _get_engine().predict(image, text_rec_score_thresh=0.0)
        lines = []
        for prediction in predictions:
            texts = prediction.get("rec_texts", [])
            scores = prediction.get("rec_scores", [])
            polygons = prediction.get("rec_polys", [])
            if not (len(texts) == len(scores) == len(polygons)):
                raise RuntimeError("PaddleOCR 文字、置信度、坐标数量不一致。")
            for text, score, polygon in zip(texts, scores, polygons):
                confidence = float(score)
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise RuntimeError("PaddleOCR 返回无效置信度。")
                if str(text).strip():
                    lines.append({"text": str(text), "confidence": confidence, "polygon": np.asarray(polygon, dtype=float).tolist()})
        return {"status": "COMPLETED", "lines": lines}


@app.get("/health")
def health() -> dict:
    return {"status": "ready", "model_loaded": _engine is not None, "engine": "PaddleOCR"}


@app.post("/ocr")
async def ocr(image: UploadFile = File(...), x_ocr_api_key: str = Header(default="")) -> dict:
    expected_key = os.getenv("OCR_SERVICE_API_KEY", "")
    if expected_key and not secrets.compare_digest(x_ocr_api_key, expected_key):
        raise HTTPException(status_code=401, detail="OCR authentication failed")
    if not _admission.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="OCR service busy")
    try:
        data = await image.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Image exceeds 20 MB")
        try:
            with Image.open(BytesIO(data)) as source:
                if source.width * source.height > 32_000_000:
                    raise HTTPException(status_code=413, detail="Image exceeds 32 million pixels")
                pixels = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))[:, :, ::-1].copy()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=400, detail="Invalid image") from exc
        return await asyncio.to_thread(recognize_image, pixels)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("PaddleOCR recognition failed")
        raise HTTPException(status_code=503, detail="PaddleOCR unavailable; check service logs") from exc
    finally:
        await image.close()
        _admission.release()
