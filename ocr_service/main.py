from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from ocr_service.engine import PaddleOcrEngine
from ocr_service.schemas import OcrRequest


engine = PaddleOcrEngine()
inference_lock = asyncio.Lock()
load_error: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global load_error
    try:
        await asyncio.to_thread(engine.load)
    except Exception as exc:
        load_error = str(exc)
    yield


app = FastAPI(title="Dedicated PaddleOCR Service", version="1.0.0", lifespan=lifespan)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "PaddleOCR", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "READY" if engine.pipeline is not None else "ERROR",
        "model": engine.model_name,
        "device": engine.device,
        "error": load_error,
    }


@app.post("/ocr")
async def recognize(request: OcrRequest) -> dict[str, Any]:
    if engine.pipeline is None:
        raise HTTPException(status_code=503, detail=load_error or "PaddleOCR is not ready.")
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(
                engine.recognize,
                request.image_path,
                request.expected_text,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PaddleOCR inference failed: {exc}") from exc
    return {
        "code": 0,
        "message": "success",
        "model": engine.model_name,
        "device": engine.device,
        **result,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
