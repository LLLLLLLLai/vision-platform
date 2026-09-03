from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.models.recipe import RegionOfInterest


COLOR_RANGES = {
    "YELLOW": [((15, 60, 60), (40, 255, 255))],
    "RED": [((0, 70, 50), (10, 255, 255)), ((170, 70, 50), (180, 255, 255))],
    "BLUE": [((90, 60, 50), (135, 255, 255))],
    "GREEN": [((35, 45, 40), (90, 255, 255))],
    "WHITE": [((0, 0, 175), (180, 70, 255))],
    "BLACK": [((0, 0, 0), (180, 255, 55))],
    "ORANGE": [((5, 80, 70), (22, 255, 255))],
    "GRAY": [((0, 0, 56), (180, 70, 190))],
}

COLOR_DISPLAY_NAMES = {
    "YELLOW": "黄色",
    "RED": "红色",
    "BLUE": "蓝色",
    "GREEN": "绿色",
    "WHITE": "白色",
    "BLACK": "黑色",
    "ORANGE": "橙色",
    "GRAY": "灰色",
}


def roi_pixel_box(
    roi: RegionOfInterest,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    x1 = round(roi.x_ratio * image_width) - roi.padding
    y1 = round(roi.y_ratio * image_height) - roi.padding
    x2 = round((roi.x_ratio + roi.width_ratio) * image_width) + roi.padding
    y2 = round((roi.y_ratio + roi.height_ratio) * image_height) + roi.padding
    return (
        max(0, x1),
        max(0, y1),
        min(image_width, x2),
        min(image_height, y2),
    )


def crop_roi(
    source_path: str,
    roi: RegionOfInterest,
    destination_path: Path,
) -> tuple[Path, tuple[int, int, int, int]]:
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
        box = roi_pixel_box(roi, image.width, image.height)
        cropped = image.crop(box)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(destination_path, quality=95)
    return destination_path, box


def align_image_to_reference(
    source_path: str,
    reference_path: str,
    destination_path: Path,
    *,
    max_shift_ratio: float,
    minimum_response: float,
    max_dimension: int,
) -> tuple[Path | None, dict[str, Any]]:
    reference = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if reference is None or source is None:
        return None, {"status": "SKIPPED", "reason": "图像或基准图无法读取"}

    reference_height, reference_width = reference.shape[:2]
    source = cv2.resize(source, (reference_width, reference_height), interpolation=cv2.INTER_AREA)
    scale = min(1.0, float(max(1, max_dimension)) / max(reference_width, reference_height))
    working_size = (
        max(32, round(reference_width * scale)),
        max(32, round(reference_height * scale)),
    )
    reference_gray = cv2.cvtColor(
        cv2.resize(reference, working_size, interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2GRAY,
    )
    source_gray = cv2.cvtColor(
        cv2.resize(source, working_size, interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2GRAY,
    )
    reference_gray = cv2.GaussianBlur(reference_gray, (5, 5), 0)
    source_gray = cv2.GaussianBlur(source_gray, (5, 5), 0)
    window = cv2.createHanningWindow((working_size[0], working_size[1]), cv2.CV_64F)
    shift, response = cv2.phaseCorrelate(
        reference_gray.astype(np.float64),
        source_gray.astype(np.float64),
        window,
    )
    shift_x = float(shift[0] / scale)
    shift_y = float(shift[1] / scale)
    max_shift_x = reference_width * max(0.0, max_shift_ratio)
    max_shift_y = reference_height * max(0.0, max_shift_ratio)
    metadata = {
        "status": "SKIPPED",
        "method": "PHASE_CORRELATION_TRANSLATION",
        "shift_x": round(shift_x, 3),
        "shift_y": round(shift_y, 3),
        "response": round(float(response), 6),
        "max_shift_x": round(max_shift_x, 3),
        "max_shift_y": round(max_shift_y, 3),
    }
    if float(response) < minimum_response:
        metadata["reason"] = "定位置信度不足，保留原图检测"
        return None, metadata
    if abs(shift_x) > max_shift_x or abs(shift_y) > max_shift_y:
        metadata["reason"] = "检测到的偏移超过允许范围，保留原图检测"
        return None, metadata

    transform = np.float32([[1, 0, -shift_x], [0, 1, -shift_y]])
    aligned = cv2.warpAffine(
        source,
        transform,
        (reference_width, reference_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination_path), aligned):
        raise ValueError(f"无法写入对齐图片: {destination_path}")
    metadata["status"] = "APPLIED"
    return destination_path, metadata


def _expand_box(
    box: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    margin_ratio: float,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    margin = max(12, round(max(x2 - x1, y2 - y1) * max(0.0, margin_ratio)))
    return (
        max(0, x1 - margin),
        max(0, y1 - margin),
        min(image_width, x2 + margin),
        min(image_height, y2 + margin),
    )


def align_image_with_anchor(
    source_path: str,
    reference_path: str,
    anchor_roi: RegionOfInterest,
    destination_path: Path,
    *,
    max_shift_ratio: float,
    search_margin_ratio: float,
    minimum_inliers: int,
    minimum_inlier_ratio: float,
    maximum_rotation_degrees: float,
) -> tuple[Path | None, dict[str, Any]]:
    reference = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if reference is None or source is None:
        return None, {"status": "SKIPPED", "reason": "图像或基准图无法读取"}

    reference_height, reference_width = reference.shape[:2]
    source = cv2.resize(source, (reference_width, reference_height), interpolation=cv2.INTER_AREA)
    anchor_box = roi_pixel_box(anchor_roi, reference_width, reference_height)
    search_box = _expand_box(
        anchor_box,
        reference_width,
        reference_height,
        search_margin_ratio,
    )
    x1, y1, x2, y2 = search_box
    reference_crop = reference[y1:y2, x1:x2]
    source_crop = source[y1:y2, x1:x2]
    if min(reference_crop.shape[:2]) < 32 or min(source_crop.shape[:2]) < 32:
        return None, {"status": "SKIPPED", "reason": "对齐基准区域过小"}

    orb = cv2.ORB_create(nfeatures=1200, fastThreshold=8)
    reference_gray = cv2.cvtColor(reference_crop, cv2.COLOR_BGR2GRAY)
    source_gray = cv2.cvtColor(source_crop, cv2.COLOR_BGR2GRAY)
    reference_keypoints, reference_descriptors = orb.detectAndCompute(reference_gray, None)
    source_keypoints, source_descriptors = orb.detectAndCompute(source_gray, None)
    metadata: dict[str, Any] = {
        "status": "SKIPPED",
        "method": "ANCHOR_ORB_AFFINE",
        "anchor_roi_code": anchor_roi.code,
        "anchor_box": list(anchor_box),
        "search_box": list(search_box),
        "reference_keypoints": len(reference_keypoints),
        "source_keypoints": len(source_keypoints),
    }
    if reference_descriptors is None or source_descriptors is None:
        metadata["reason"] = "对齐基准缺少可用特征点"
        return None, metadata

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = matcher.knnMatch(source_descriptors, reference_descriptors, k=2)
    good_matches = [
        first
        for first, second in raw_matches
        if first.distance < 0.75 * second.distance
    ]
    metadata["match_count"] = len(good_matches)
    if len(good_matches) < max(3, minimum_inliers):
        metadata["reason"] = "对齐基准匹配点不足"
        return None, metadata

    source_points = np.float32(
        [
            (
                source_keypoints[match.queryIdx].pt[0] + x1,
                source_keypoints[match.queryIdx].pt[1] + y1,
            )
            for match in good_matches
        ]
    ).reshape(-1, 1, 2)
    reference_points = np.float32(
        [
            (
                reference_keypoints[match.trainIdx].pt[0] + x1,
                reference_keypoints[match.trainIdx].pt[1] + y1,
            )
            for match in good_matches
        ]
    ).reshape(-1, 1, 2)
    transform, inlier_mask = cv2.estimateAffinePartial2D(
        source_points,
        reference_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=3000,
        confidence=0.995,
        refineIters=10,
    )
    if transform is None or inlier_mask is None:
        metadata["reason"] = "无法根据特征点计算对齐变换"
        return None, metadata
    inliers = int(np.count_nonzero(inlier_mask))
    inlier_ratio = inliers / len(good_matches)
    scale = float(np.sqrt(transform[0, 0] ** 2 + transform[1, 0] ** 2))
    rotation = float(np.degrees(np.arctan2(transform[1, 0], transform[0, 0])))
    translation_x = float(transform[0, 2])
    translation_y = float(transform[1, 2])
    max_shift_x = reference_width * max(0.0, max_shift_ratio)
    max_shift_y = reference_height * max(0.0, max_shift_ratio)
    metadata.update(
        {
            "inliers": inliers,
            "inlier_ratio": round(inlier_ratio, 6),
            "scale": round(scale, 6),
            "rotation_degrees": round(rotation, 4),
            "shift_x": round(translation_x, 3),
            "shift_y": round(translation_y, 3),
        }
    )
    if inliers < minimum_inliers or inlier_ratio < minimum_inlier_ratio:
        metadata["reason"] = "对齐基准内点不足，回退整图平移对齐"
        return None, metadata
    if abs(rotation) > maximum_rotation_degrees or not 0.90 <= scale <= 1.10:
        metadata["reason"] = "对齐变换超出允许的旋转或缩放范围"
        return None, metadata
    if abs(translation_x) > max_shift_x or abs(translation_y) > max_shift_y:
        metadata["reason"] = "对齐基准检测到的偏移超过允许范围"
        return None, metadata

    aligned = cv2.warpAffine(
        source,
        transform.astype(np.float32),
        (reference_width, reference_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination_path), aligned):
        raise ValueError(f"无法写入对齐图片: {destination_path}")
    metadata["status"] = "APPLIED"
    return destination_path, metadata


def _hue_mask(hsv: np.ndarray, center: float, tolerance: float) -> np.ndarray:
    lower = int(round(center - tolerance))
    upper = int(round(center + tolerance))
    saturation_floor = 30
    value_floor = 25
    if lower >= 0 and upper <= 179:
        return cv2.inRange(
            hsv,
            np.array((lower, saturation_floor, value_floor), dtype=np.uint8),
            np.array((upper, 255, 255), dtype=np.uint8),
        )
    ranges = []
    if lower < 0:
        ranges.append(((0, saturation_floor, value_floor), (upper, 255, 255)))
        ranges.append(((180 + lower, saturation_floor, value_floor), (179, 255, 255)))
    else:
        ranges.append(((lower, saturation_floor, value_floor), (179, 255, 255)))
        ranges.append(((0, saturation_floor, value_floor), (upper - 180, 255, 255)))
    result = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower_bound, upper_bound in ranges:
        result = cv2.bitwise_or(
            result,
            cv2.inRange(hsv, np.array(lower_bound, dtype=np.uint8), np.array(upper_bound, dtype=np.uint8)),
        )
    return result


def color_ratio(
    image_path: str,
    color_name: str,
    color_profile: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Unable to read image: {image_path}")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    ranges = COLOR_RANGES.get(color_name.upper())
    if not ranges:
        raise ValueError(f"Unsupported color: {color_name}")
    combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
    profile_used = False
    if color_profile and color_profile.get("hue_center") is not None:
        combined = _hue_mask(
            hsv,
            float(color_profile["hue_center"]),
            max(6.0, float(color_profile.get("hue_tolerance", 16.0))),
        )
        saturation_floor = int(max(20, float(color_profile.get("saturation_floor", 35)) * 0.65))
        value_floor = int(max(15, float(color_profile.get("value_floor", 30)) * 0.55))
        combined = cv2.bitwise_and(
            combined,
            cv2.inRange(
                hsv,
                np.array((0, saturation_floor, value_floor), dtype=np.uint8),
                np.array((179, 255, 255), dtype=np.uint8),
            ),
        )
        profile_used = True
    else:
        for lower, upper in ranges:
            mask = cv2.inRange(
                hsv,
                np.array(lower, dtype=np.uint8),
                np.array(upper, dtype=np.uint8),
            )
            combined = cv2.bitwise_or(combined, mask)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
    ratio = float(np.count_nonzero(combined)) / float(combined.size)
    return ratio, {"profile_used": profile_used, "mask_pixels": int(np.count_nonzero(combined))}


def analyze_roi_color(
    image_path: str,
    roi: RegionOfInterest,
) -> dict[str, object]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Unable to read image: {image_path}")
    x1, y1, x2, y2 = roi_pixel_box(roi, image.shape[1], image.shape[0])
    region = image[y1:y2, x1:x2]
    if region.size == 0:
        raise ValueError("ROI does not contain image pixels.")

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    ratios: dict[str, float] = {}
    masks: dict[str, np.ndarray] = {}
    for color_name, ranges in COLOR_RANGES.items():
        combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in ranges:
            mask = cv2.inRange(
                hsv,
                np.array(lower, dtype=np.uint8),
                np.array(upper, dtype=np.uint8),
            )
            combined = cv2.bitwise_or(combined, mask)
        masks[color_name] = combined
        ratios[color_name] = float(np.count_nonzero(combined)) / float(combined.size)

    chromatic = ("RED", "ORANGE", "YELLOW", "GREEN", "BLUE")
    chromatic_color = max(chromatic, key=lambda name: ratios[name])
    if ratios[chromatic_color] >= 0.05:
        dominant = chromatic_color
    else:
        dominant = max(("WHITE", "GRAY", "BLACK"), key=lambda name: ratios[name])

    dominant_pixels = region[masks[dominant] > 0]
    if dominant_pixels.size:
        median_bgr = np.median(dominant_pixels, axis=0).astype(np.uint8)
    else:
        median_bgr = np.median(region.reshape(-1, 3), axis=0).astype(np.uint8)
    blue, green, red = (int(value) for value in median_bgr)
    dominant_hsv = hsv[masks[dominant] > 0]
    if dominant_hsv.size == 0:
        dominant_hsv = hsv.reshape(-1, 3)
    hue_values = dominant_hsv[:, 0].astype(np.float32)
    hue_center = float(np.median(hue_values))
    hue_deviation = np.abs(hue_values - hue_center)
    hue_deviation = np.minimum(hue_deviation, 180 - hue_deviation)
    profile = {
        "color": dominant,
        "hue_center": round(hue_center, 3),
        "hue_tolerance": round(max(8.0, min(30.0, float(np.percentile(hue_deviation, 90)) + 8.0)), 3),
        "saturation_floor": round(max(20.0, float(np.percentile(dominant_hsv[:, 1], 10))), 3),
        "value_floor": round(max(15.0, float(np.percentile(dominant_hsv[:, 2], 5))), 3),
        "baseline_ratio": round(ratios[dominant], 6),
    }
    ranked = sorted(ratios.items(), key=lambda item: item[1], reverse=True)
    return {
        "color": dominant,
        "display_name": COLOR_DISPLAY_NAMES[dominant],
        "ratio": round(ratios[dominant], 6),
        "hex": f"#{red:02X}{green:02X}{blue:02X}",
        "profile": profile,
        "candidates": [
            {
                "color": color_name,
                "display_name": COLOR_DISPLAY_NAMES[color_name],
                "ratio": round(ratio, 6),
            }
            for color_name, ratio in ranked[:4]
        ],
        "roi_box": [x1, y1, x2, y2],
    }


def annotate_image(
    source_path: str,
    annotations: list[dict],
    destination_path: Path,
) -> Path:
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
        draw = ImageDraw.Draw(image)
        for annotation in annotations:
            color = (35, 170, 95) if annotation["status"] == "OK" else (220, 55, 65)
            draw.rectangle(annotation["box"], outline=color, width=5)
            label = f'{annotation["code"]}: {annotation["status"]}'
            x1, y1, _, _ = annotation["box"]
            draw.rectangle((x1, max(0, y1 - 24), x1 + 180, y1), fill=color)
            draw.text((x1 + 4, max(0, y1 - 21)), label, fill=(255, 255, 255))
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination_path, quality=95)
    return destination_path
