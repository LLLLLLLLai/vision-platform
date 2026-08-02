import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings:
    model_id = os.getenv(
        "GROUNDING_MODEL_ID",
        "IDEA-Research/grounding-dino-base",
    )
    model_path = Path(
        os.getenv(
            "GROUNDING_MODEL_PATH",
            str(PROJECT_ROOT / "vision-models" / "grounding-dino-base"),
        )
    )
    local_files_only = os.getenv(
        "GROUNDING_LOCAL_FILES_ONLY",
        "true",
    ).lower() in {"1", "true", "yes", "on"}


settings = Settings()


class LocalizeRequest(BaseModel):
    image_path: str
    prompts: list[str] = Field(min_length=1, max_length=30)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    box_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    text_threshold: float = Field(default=0.22, ge=0.0, le=1.0)


class GroundingEngine:
    def __init__(self) -> None:
        self.model: Any = None
        self.processor: Any = None
        self.status = "NOT_LOADED"
        self.error: str | None = None
        self.loaded_from: str | None = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype_name = os.getenv("GROUNDING_DTYPE", "float32").lower()
        self.dtype = (
            torch.float16
            if self.device == "cuda" and dtype_name in {"float16", "fp16", "half"}
            else torch.float32
        )

    def load(self) -> None:
        if self.model is not None:
            return
        self.status = "LOADING"
        source = str(settings.model_path) if settings.model_path.exists() else settings.model_id
        if settings.local_files_only and not settings.model_path.exists():
            self.status = "ERROR"
            self.error = f"Local model directory does not exist: {settings.model_path}"
            raise FileNotFoundError(self.error)
        try:
            self.processor = AutoProcessor.from_pretrained(
                source,
                local_files_only=settings.local_files_only,
            )
            self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
                source,
                local_files_only=settings.local_files_only,
                dtype=self.dtype,
                low_cpu_mem_usage=True,
            ).to(self.device)
            self.model.eval()
            self.loaded_from = source
            self.status = "READY"
            self.error = None
        except Exception as exc:
            self.model = None
            self.processor = None
            self.status = "ERROR"
            self.error = str(exc)
            raise

    def localize(self, request: LocalizeRequest) -> dict[str, Any]:
        self.load()
        image_file = Path(request.image_path)
        if not image_file.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_file}")
        with Image.open(image_file) as source_image:
            image = source_image.convert("RGB")
        text = ". ".join(prompt.strip() for prompt in request.prompts if prompt.strip())
        if not text.endswith("."):
            text += "."
        inputs = self.processor(images=image, text=text, return_tensors="pt")
        for key, value in inputs.items():
            if not isinstance(value, torch.Tensor):
                continue
            if key == "pixel_values":
                inputs[key] = value.to(device=self.device, dtype=self.dtype)
            else:
                inputs[key] = value.to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        box_threshold = request.threshold or request.box_threshold
        result = self.processor.post_process_grounded_object_detection(
            outputs,
            input_ids=inputs.get("input_ids"),
            threshold=box_threshold,
            text_threshold=request.text_threshold,
            target_sizes=[image.size[::-1]],
        )[0]
        boxes = result["boxes"].detach().cpu().tolist()
        scores = result["scores"].detach().cpu().tolist()
        labels = result.get("text_labels")
        if labels is None:
            labels = result.get("labels", [])
        if isinstance(labels, torch.Tensor):
            labels = labels.detach().cpu().tolist()
        objects = [
            {
                "label": str(label),
                "score": round(float(score), 6),
                "bbox": [round(float(value), 2) for value in box],
            }
            for box, score, label in zip(boxes, scores, labels)
        ]
        return {
            "image_width": image.width,
            "image_height": image.height,
            "objects": objects,
        }


engine = GroundingEngine()
inference_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await asyncio.to_thread(engine.load)
    except Exception:
        pass
    yield


app = FastAPI(
    title="Grounding DINO Localization Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": engine.status,
        "model": settings.model_id,
        "loaded_from": engine.loaded_from,
        "device": engine.device,
        "dtype": str(engine.dtype),
        "error": engine.error,
    }


@app.post("/v1/localize")
async def localize(request: LocalizeRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(engine.localize, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "success",
        "model": settings.model_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        **result,
    }
