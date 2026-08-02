from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from app.core.config import PROJECT_ROOT


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


def merge_harness_segmentations(
    sam2_result: dict[str, Any],
    color_result: dict[str, Any],
) -> dict[str, Any]:
    merged = list(sam2_result.get("segments", []))
    for segment in color_result.get("segments", []):
        if any(
            _bbox_iou(segment["bbox"], existing["bbox"]) >= 0.45
            for existing in merged
        ):
            continue
        merged.append(segment)
    for index, segment in enumerate(merged, start=1):
        segment["segment_id"] = f"HARNESS_SEG_{index}"
    return {
        **sam2_result,
        "mode": "GROUNDED_SAM2_WITH_COLOR_FALLBACK",
        "supported_scope": "橙色、黑色、灰色及低压线束",
        "segments": merged,
        "segment_count": len(merged),
        "color_fallback_count": len(color_result.get("segments", [])),
    }


def harness_segments_to_candidates(
    segmentation: dict[str, Any],
    limit: int = 20,
) -> list[dict[str, Any]]:
    segments = [
        segment
        for segment in segmentation.get("segments", [])
        if isinstance(segment, dict)
        and isinstance(segment.get("bbox"), list)
        and len(segment["bbox"]) == 4
    ]
    segments.sort(
        key=lambda segment: (
            segment.get("engine") == "SAM2.1_HIERA_SMALL",
            float(segment.get("area_ratio") or 0.0),
        ),
        reverse=True,
    )
    candidates: list[dict[str, Any]] = []
    for segment in segments[:limit]:
        x1, y1, x2, y2 = [
            max(0.0, min(1.0, float(value)))
            for value in segment["bbox"]
        ]
        if x2 <= x1 or y2 <= y1:
            continue
        engine = str(segment.get("engine") or segmentation.get("mode") or "SEGMENTATION")
        source_segment_id = str(
            segment.get("segment_id") or f"HARNESS_SEG_{len(candidates) + 1}"
        )
        candidates.append(
            {
                "candidate_id": f"HARNESS_MASK_{len(candidates) + 1}",
                "label": segment.get("label") or f"线束分割 {len(candidates) + 1:02d}",
                "object_type": "HARNESS",
                "target_kind": "HARNESS_SEGMENT",
                "confidence": round(float(segment.get("confidence") or 0.0), 4),
                "bbox": [round(value, 6) for value in (x1, y1, x2, y2)],
                "x_ratio": round(x1, 6),
                "y_ratio": round(y1, 6),
                "width_ratio": round(x2 - x1, 6),
                "height_ratio": round(y2 - y1, 6),
                "engine": engine,
                "review_status": "REVIEW_REQUIRED",
                "batch_confirmable": False,
                "source_segment_id": source_segment_id,
                "segmentation_mode": segment.get("segmentation_mode") or segmentation.get("mode"),
                "polygon": segment.get("polygon", []),
            }
        )
    return candidates


def _normalized_polygon(
    contour: np.ndarray,
    image_width: int,
    image_height: int,
) -> list[list[float]]:
    perimeter = cv2.arcLength(contour, True)
    approximation = cv2.approxPolyDP(contour, max(2.0, perimeter * 0.006), True)
    return [
        [
            round(float(point[0][0]) / image_width, 6),
            round(float(point[0][1]) / image_height, 6),
        ]
        for point in approximation
    ]


def segment_orange_harness(
    image_path: str,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    image_file = Path(image_path)
    image = cv2.imread(str(image_file))
    if image is None:
        raise ValueError(f"无法读取线束分割图片：{image_path}")

    image_height, image_width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    orange_mask = cv2.inRange(
        hsv,
        np.array([4, 85, 65], dtype=np.uint8),
        np.array([20, 255, 255], dtype=np.uint8),
    )

    open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    orange_mask = cv2.morphologyEx(orange_mask, cv2.MORPH_OPEN, open_kernel)
    orange_mask = cv2.morphologyEx(orange_mask, cv2.MORPH_CLOSE, close_kernel)

    contours, _ = cv2.findContours(
        orange_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    image_area = float(image_width * image_height)
    minimum_area = max(120.0, image_area * 0.00018)
    segments: list[dict[str, Any]] = []
    accepted_contours: list[np.ndarray] = []

    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = float(cv2.contourArea(contour))
        if area < minimum_area:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        if max(width / image_width, height / image_height) < 0.025:
            continue
        polygon = _normalized_polygon(contour, image_width, image_height)
        if len(polygon) < 3:
            continue
        rectangular_area = max(1.0, float(width * height))
        color_density = min(1.0, area / rectangular_area)
        confidence = min(0.99, 0.55 + color_density * 0.35)
        accepted_contours.append(contour)
        segments.append(
            {
                "segment_id": f"HARNESS_SEG_{len(segments) + 1}",
                "label": f"橙色高压线束 {len(segments) + 1:02d}",
                "object_type": "HARNESS",
                "segmentation_mode": "ORANGE_HARNESS_HSV",
                "polygon": polygon,
                "bbox": [
                    round(x / image_width, 6),
                    round(y / image_height, 6),
                    round((x + width) / image_width, 6),
                    round((y + height) / image_height, 6),
                ],
                "area_ratio": round(area / image_area, 6),
                "color_density": round(color_density, 6),
                "confidence": round(confidence, 6),
                "color": "orange",
            }
        )

    result_directory = output_directory or (
        PROJECT_ROOT / "detection_results" / "harness_segments"
    )
    result_directory.mkdir(parents=True, exist_ok=True)
    result_id = uuid4().hex
    mask_file = result_directory / f"{result_id}_mask.png"
    overlay_file = result_directory / f"{result_id}_overlay.jpg"

    filtered_mask = np.zeros_like(orange_mask)
    if accepted_contours:
        cv2.drawContours(filtered_mask, accepted_contours, -1, 255, thickness=cv2.FILLED)
    overlay = image.copy()
    orange_layer = np.zeros_like(image)
    orange_layer[:, :] = (0, 115, 249)
    alpha = np.where(filtered_mask[..., None] > 0, 0.42, 0.0).astype(np.float32)
    overlay = (overlay * (1.0 - alpha) + orange_layer * alpha).astype(np.uint8)
    if accepted_contours:
        cv2.drawContours(overlay, accepted_contours, -1, (0, 80, 255), thickness=3)

    cv2.imwrite(str(mask_file), filtered_mask)
    cv2.imwrite(str(overlay_file), overlay)

    if output_directory is None:
        mask_path = f"/results/harness_segments/{mask_file.name}"
        overlay_path = f"/results/harness_segments/{overlay_file.name}"
    else:
        mask_path = str(mask_file)
        overlay_path = str(overlay_file)

    return {
        "mode": "ORANGE_HARNESS_HSV",
        "supported_scope": "橙色高压线束",
        "image_width": image_width,
        "image_height": image_height,
        "segment_count": len(segments),
        "segments": segments,
        "mask_path": mask_path,
        "overlay_path": overlay_path,
    }
