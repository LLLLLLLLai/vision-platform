import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as functional
from fastapi import FastAPI, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from transformers import AutoImageProcessor, AutoModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "vision-models" / "dinov2-base"
MODEL_PATH = Path(os.getenv("DINO_MODEL_PATH", DEFAULT_MODEL_PATH)).resolve()
DEVICE_SETTING = os.getenv("DINO_DEVICE", "auto").lower()


class EmbeddingRequest(BaseModel):
    image_path: str


class SimilarityRequest(BaseModel):
    image_path: str
    reference_paths: list[str] = Field(min_length=1, max_length=100)
    top_k: int = Field(default=3, ge=1, le=20)


class Dinov2Engine:
    def __init__(self) -> None:
        self.processor: Any = None
        self.model: Any = None
        self.device = self._resolve_device()
        self.lock = asyncio.Lock()

    def _resolve_device(self) -> torch.device:
        if DEVICE_SETTING == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if DEVICE_SETTING == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("DINO_DEVICE=cuda, but CUDA is unavailable.")
        return torch.device(DEVICE_SETTING)

    def load(self) -> None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"DINOv2 model directory does not exist: {MODEL_PATH}"
            )

        self.processor = AutoImageProcessor.from_pretrained(
            MODEL_PATH,
            local_files_only=True,
        )
        self.model = AutoModel.from_pretrained(
            MODEL_PATH,
            local_files_only=True,
            torch_dtype=torch.float32,
        ).to(self.device)
        self.model.eval()

    def extract_embedding(self, image_path: str) -> torch.Tensor:
        if self.model is None or self.processor is None:
            raise RuntimeError("DINOv2 model is not loaded.")

        image_file = Path(image_path).expanduser().resolve()
        if not image_file.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_file}")

        try:
            with Image.open(image_file) as opened_image:
                image = opened_image.convert("RGB")
        except UnidentifiedImageError as exc:
            raise ValueError(f"Unsupported image file: {image_file}") from exc

        inputs = self.processor(images=image, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(
            device=self.device,
            dtype=torch.float32,
        )

        with torch.inference_mode():
            outputs = self.model(pixel_values=pixel_values)

        embedding = outputs.last_hidden_state[:, 0, :]
        return functional.normalize(embedding.float(), p=2, dim=-1)

    def gpu_memory(self) -> dict[str, float] | None:
        if self.device.type != "cuda":
            return None
        return {
            "allocated_mb": round(torch.cuda.memory_allocated() / 1024**2, 2),
            "reserved_mb": round(torch.cuda.memory_reserved() / 1024**2, 2),
        }


engine = Dinov2Engine()


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.load()
    yield


app = FastAPI(
    title="DINOv2 Similarity Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "READY" if engine.model is not None else "LOADING",
        "model": "facebook/dinov2-base",
        "model_path": str(MODEL_PATH),
        "device": str(engine.device),
        "cuda_available": torch.cuda.is_available(),
        "gpu_name": (
            torch.cuda.get_device_name(0)
            if torch.cuda.is_available()
            else None
        ),
        "gpu_memory": engine.gpu_memory(),
    }


@app.post("/v1/embedding")
async def embedding(request: EmbeddingRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with engine.lock:
            vector = engine.extract_embedding(request.image_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    embedding_vector = vector[0].cpu().numpy().tolist()
    return {
        "code": 0,
        "message": "success",
        "model": "dinov2-base",
        "dimension": len(embedding_vector),
        "embedding": embedding_vector,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@app.post("/v1/similarity")
async def similarity(request: SimilarityRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with engine.lock:
            query = engine.extract_embedding(request.image_path)
            candidates = []

            for reference_path in request.reference_paths:
                reference = engine.extract_embedding(reference_path)
                score = functional.cosine_similarity(query, reference).item()
                candidates.append(
                    {
                        "reference_path": reference_path,
                        "similarity": round(float(score), 6),
                    }
                )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    top_candidates = candidates[: request.top_k]
    top1 = top_candidates[0]["similarity"]
    top2 = (
        top_candidates[1]["similarity"]
        if len(top_candidates) > 1
        else None
    )

    return {
        "code": 0,
        "message": "success",
        "model": "dinov2-base",
        "top1_similarity": top1,
        "top2_similarity": top2,
        "margin": round(top1 - top2, 6) if top2 is not None else None,
        "candidates": top_candidates,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }

