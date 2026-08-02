from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(
    os.getenv(
        "SAM2_MODEL_PATH",
        PROJECT_ROOT / "vision-models" / "sam2.1-hiera-small" / "sam2.1_hiera_small.pt",
    )
)
MODEL_CONFIG = os.getenv(
    "SAM2_MODEL_CONFIG",
    "configs/sam2.1/sam2.1_hiera_s.yaml",
)
DEVICE_SETTING = os.getenv("SAM2_DEVICE", "cpu").lower()
RESULT_DIRECTORY = PROJECT_ROOT / "detection_results" / "harness_segments"


class SegmentSeed(BaseModel):
    label: str = "线束"
    bbox: list[float] = Field(min_length=4, max_length=4)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    color: str = "unknown"


class SegmentRequest(BaseModel):
    image_path: str
    seeds: list[SegmentSeed] = Field(min_length=1, max_length=16)


class Sam2Engine:
    def __init__(self) -> None:
        self.predictor: SAM2ImagePredictor | None = None
        self.device = "cpu"
        self.lock = asyncio.Lock()

    def load(self) -> None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"SAM2 模型不存在：{MODEL_PATH}")
        if DEVICE_SETTING == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("SAM2_DEVICE=cuda，但 CUDA 不可用")
            self.device = "cuda"
        elif DEVICE_SETTING == "auto" and torch.cuda.is_available():
            free_memory, _ = torch.cuda.mem_get_info()
            self.device = "cuda" if free_memory >= 1536 * 1024**2 else "cpu"
        else:
            self.device = "cpu"
        model = build_sam2(
            MODEL_CONFIG,
            str(MODEL_PATH),
            device=self.device,
            apply_postprocessing=False,
        )
        self.predictor = SAM2ImagePredictor(model)

    @staticmethod
    def _absolute_box(
        bbox: list[float],
        image_width: int,
        image_height: int,
    ) -> np.ndarray:
        box = np.asarray(bbox, dtype=np.float32)
        if float(np.max(box)) <= 1.0:
            box *= np.asarray(
                [image_width, image_height, image_width, image_height],
                dtype=np.float32,
            )
        box[0::2] = np.clip(box[0::2], 0, image_width - 1)
        box[1::2] = np.clip(box[1::2], 0, image_height - 1)
        return box

    @staticmethod
    def _mask_segment(
        mask: np.ndarray,
        score: float,
        seed: SegmentSeed,
        seed_box: np.ndarray,
        image_width: int,
        image_height: int,
        index: int,
    ) -> dict[str, Any] | None:
        mask_uint8 = (mask.astype(np.uint8) * 255)
        contours, _ = cv2.findContours(
            mask_uint8,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(contour))
        image_area = float(image_width * image_height)
        if area / image_area < 0.00008:
            return None
        seed_area = max(
            1.0,
            float((seed_box[2] - seed_box[0]) * (seed_box[3] - seed_box[1])),
        )
        mask_occupancy = area / seed_area
        if score < 0.55 or mask_occupancy > 0.42:
            return None
        perimeter = cv2.arcLength(contour, True)
        contour = cv2.approxPolyDP(
            contour,
            max(2.0, perimeter * 0.004),
            True,
        )
        if len(contour) < 3:
            return None
        x, y, width, height = cv2.boundingRect(contour)
        polygon = [
            [
                round(float(point[0][0]) / image_width, 6),
                round(float(point[0][1]) / image_height, 6),
            ]
            for point in contour
        ]
        return {
            "segment_id": f"SAM2_HARNESS_{index}",
            "label": seed.label,
            "object_type": "HARNESS",
            "segmentation_mode": "GROUNDED_SAM2",
            "engine": "SAM2.1_HIERA_SMALL",
            "polygon": polygon,
            "bbox": [
                round(x / image_width, 6),
                round(y / image_height, 6),
                round((x + width) / image_width, 6),
                round((y + height) / image_height, 6),
            ],
            "area_ratio": round(area / image_area, 6),
            "confidence": round(float(score), 6),
            "seed_confidence": round(seed.confidence, 6),
            "mask_occupancy": round(mask_occupancy, 6),
            "color": seed.color,
        }

    def segment(self, request: SegmentRequest) -> dict[str, Any]:
        if self.predictor is None:
            raise RuntimeError("SAM2 模型尚未加载")
        image = cv2.imread(request.image_path)
        if image is None:
            raise ValueError(f"无法读取图片：{request.image_path}")
        image_height, image_width = image.shape[:2]
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self.predictor.set_image(image_rgb)

        segments: list[dict[str, Any]] = []
        masks_for_overlay: list[np.ndarray] = []
        for seed in request.seeds:
            box = self._absolute_box(seed.bbox, image_width, image_height)
            masks, scores, _ = self.predictor.predict(
                box=box,
                multimask_output=True,
            )
            segment = None
            selected_mask = None
            for mask_index in np.argsort(scores)[::-1]:
                segment = self._mask_segment(
                    masks[mask_index],
                    float(scores[mask_index]),
                    seed,
                    box,
                    image_width,
                    image_height,
                    len(segments) + 1,
                )
                if segment is not None:
                    selected_mask = masks[mask_index]
                    break
            if segment is None:
                continue
            if any(
                _bbox_iou(segment["bbox"], existing["bbox"]) >= 0.55
                for existing in segments
            ):
                continue
            segments.append(segment)
            masks_for_overlay.append(selected_mask)

        RESULT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        result_id = uuid4().hex
        mask_file = RESULT_DIRECTORY / f"{result_id}_sam2_mask.png"
        overlay_file = RESULT_DIRECTORY / f"{result_id}_sam2_overlay.jpg"
        combined_mask = np.zeros((image_height, image_width), dtype=np.uint8)
        for mask in masks_for_overlay:
            combined_mask[mask.astype(bool)] = 255
        overlay = image.copy()
        teal = np.zeros_like(image)
        teal[:, :] = (180, 180, 0)
        alpha = np.where(combined_mask[..., None] > 0, 0.38, 0.0).astype(np.float32)
        overlay = (overlay * (1.0 - alpha) + teal * alpha).astype(np.uint8)
        cv2.imwrite(str(mask_file), combined_mask)
        cv2.imwrite(str(overlay_file), overlay)
        self.predictor.reset_predictor()
        return {
            "mode": "GROUNDED_SAM2",
            "engine": "SAM2.1_HIERA_SMALL",
            "supported_scope": "橙色、黑色、灰色及低压线束",
            "device": self.device,
            "image_width": image_width,
            "image_height": image_height,
            "segment_count": len(segments),
            "segments": segments,
            "mask_path": f"/results/harness_segments/{mask_file.name}",
            "overlay_path": f"/results/harness_segments/{overlay_file.name}",
        }


engine = Sam2Engine()


def _bbox_iou(first: list[float], second: list[float]) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.load()
    yield


app = FastAPI(title="SAM2 Harness Segmentation Service", lifespan=lifespan)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "SAM2 Harness Segmentation Service",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "READY" if engine.predictor is not None else "LOADING",
        "model": "sam2.1-hiera-small",
        "device": engine.device,
        "model_path": str(MODEL_PATH),
    }


@app.post("/v1/segment")
async def segment(request: SegmentRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with engine.lock:
            result = await asyncio.to_thread(engine.segment, request)
        result["code"] = 0
        result["message"] = "success"
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SAM2 分割失败：{exc}") from exc
