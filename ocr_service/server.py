import asyncio
from io import BytesIO
import logging
import math
import os
from pathlib import Path
import secrets
import threading

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


app = FastAPI(title="Vision Platform PaddleOCR")
logger = logging.getLogger("uvicorn.error")
_engine = None
_engine_features = {"use_orientation_classify": False, "use_doc_unwarping": False}
_engine_lock = threading.Lock()
_queue_capacity = max(1, int(os.getenv("OCR_MAX_PENDING_REQUESTS", "32")))
_queue_wait_seconds = max(0.01, float(os.getenv("OCR_QUEUE_WAIT_SECONDS", "60")))
_admission = threading.BoundedSemaphore(_queue_capacity)
_execution = threading.BoundedSemaphore(1)
_inflight: set[asyncio.Task] = set()


def _model_directory(kind: str, name: str) -> str:
    configured = os.getenv(f"OCR_{kind}_MODEL_DIR", "").strip()
    local_root = Path(__file__).resolve().parents[1] / "vision-models" / "paddleocr"
    candidates = [local_root / name, local_root / f"{name}_infer", Path.home() / ".paddlex" / "official_models" / name]
    directory = Path(configured).expanduser() if configured else next((path for path in candidates if path.is_dir()), candidates[-1])
    if not directory.is_dir():
        raise RuntimeError(f"OCR_{kind}_MODEL_DIR 未配置可用模型目录，请先部署 PaddleOCR 模型。")
    return str(directory.resolve())


class OcrModelConfigurationError(RuntimeError):
    pass


def _get_engine(*, use_orientation_classify: bool = False, use_doc_unwarping: bool = False):
    global _engine
    features = {
        "use_orientation_classify": _engine_features["use_orientation_classify"] or use_orientation_classify,
        "use_doc_unwarping": _engine_features["use_doc_unwarping"] or use_doc_unwarping,
    }
    if _engine is None or features != _engine_features:
        from paddleocr import PaddleOCR

        detection_name = os.getenv("OCR_DETECTION_MODEL_NAME", "PP-OCRv5_mobile_det")
        recognition_name = os.getenv("OCR_RECOGNITION_MODEL_NAME", "PP-OCRv5_mobile_rec")
        optional_models = {}
        if features["use_orientation_classify"]:
            optional_models.update({
                "doc_orientation_classify": ("DOC_ORIENTATION", "PP-LCNet_x1_0_doc_ori"),
                "textline_orientation": ("TEXTLINE_ORIENTATION", "PP-LCNet_x1_0_textline_ori"),
            })
        if features["use_doc_unwarping"]:
            optional_models["doc_unwarping"] = ("UNWARPING", "UVDoc")
        model_options = {}
        for parameter, (kind, default_name) in optional_models.items():
            name = os.getenv(f"OCR_{kind}_MODEL_NAME", default_name)
            try:
                directory = _model_directory(kind, name)
            except RuntimeError as exc:
                raise OcrModelConfigurationError(f"OCR 预处理模型 {name} 未部署，请配置 OCR_{kind}_MODEL_DIR 或将模型放入 vision-models/paddleocr。") from exc
            model_options[f"{parameter}_model_name"] = name
            model_options[f"{parameter}_model_dir"] = directory
        engine = PaddleOCR(
            text_detection_model_name=detection_name,
            text_detection_model_dir=_model_directory("DETECTION", detection_name),
            text_recognition_model_name=recognition_name,
            text_recognition_model_dir=_model_directory("RECOGNITION", recognition_name),
            use_doc_orientation_classify=features["use_orientation_classify"],
            use_doc_unwarping=features["use_doc_unwarping"],
            use_textline_orientation=features["use_orientation_classify"],
            device=os.getenv("OCR_DEVICE", "cpu"),
            enable_mkldnn=False,
            cpu_threads=4,
            **model_options,
        )
        _engine = engine
        _engine_features.update(features)
    return _engine


def recognize_image(image: np.ndarray, *, use_orientation_classify: bool = False, use_doc_unwarping: bool = False) -> dict:
    with _engine_lock:
        predictions = _get_engine(
            use_orientation_classify=use_orientation_classify, use_doc_unwarping=use_doc_unwarping,
        ).predict(
            image, text_rec_score_thresh=0.0,
            use_doc_orientation_classify=use_orientation_classify,
            use_textline_orientation=use_orientation_classify,
            use_doc_unwarping=use_doc_unwarping,
        )
        lines = []
        doc_angle = None
        for prediction in predictions:
            angle = (prediction.get("doc_preprocessor_res") or {}).get("angle", -1)
            if angle in (0, 90, 180, 270):
                doc_angle = int(angle)
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
        return {"status": "COMPLETED", "lines": lines, "preprocessing": {
            "use_orientation_classify": use_orientation_classify,
            "use_doc_unwarping": use_doc_unwarping,
            "doc_angle_degrees": doc_angle,
            "coordinate_space": "PREPROCESSED_IMAGE" if use_orientation_classify or use_doc_unwarping else "INPUT_IMAGE",
        }}


@app.get("/health")
def health() -> dict:
    return {"status": "ready", "model_loaded": _engine is not None, "engine": "PaddleOCR",
            "max_pending_requests": _queue_capacity, "queue_wait_seconds": _queue_wait_seconds}


def _recognize_admitted(pixels: np.ndarray, **flags) -> dict:
    try:
        return recognize_image(pixels, **flags)
    finally:
        _execution.release()
        _admission.release()


def _finish_inference(task: asyncio.Task) -> None:
    _inflight.discard(task)
    if not task.cancelled():
        task.exception()


@app.post("/ocr")
async def ocr(
    image: UploadFile = File(...), x_ocr_api_key: str = Header(default=""),
    use_orientation_classify: bool = Form(default=False),
    use_doc_unwarping: bool = Form(default=False),
) -> dict:
    expected_key = os.getenv("OCR_SERVICE_API_KEY", "")
    if expected_key and not secrets.compare_digest(x_ocr_api_key, expected_key):
        raise HTTPException(status_code=401, detail="OCR authentication failed")
    if not _admission.acquire(blocking=False):
        await image.close()
        raise HTTPException(status_code=429, detail="OCR service busy: waiting queue is full", headers={"Retry-After": "1"})
    owns_admission = True
    owns_execution = False
    try:
        deadline = asyncio.get_running_loop().time() + _queue_wait_seconds
        while not _execution.acquire(blocking=False):
            if asyncio.get_running_loop().time() >= deadline:
                raise HTTPException(status_code=429, detail="OCR service busy: queue wait timed out", headers={"Retry-After": "1"})
            await asyncio.sleep(0.02)
        owns_execution = True
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
        task = asyncio.create_task(asyncio.to_thread(
            _recognize_admitted, pixels,
            use_orientation_classify=use_orientation_classify, use_doc_unwarping=use_doc_unwarping,
        ))
        _inflight.add(task)
        task.add_done_callback(_finish_inference)
        owns_execution = owns_admission = False
        return await asyncio.shield(task)
    except HTTPException:
        raise
    except OcrModelConfigurationError as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("PaddleOCR recognition failed")
        raise HTTPException(status_code=503, detail="PaddleOCR unavailable; check service logs") from exc
    finally:
        if owns_execution:
            _execution.release()
        if owns_admission:
            _admission.release()
        await image.close()
