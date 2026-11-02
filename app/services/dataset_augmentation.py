"""Configurable, reproducible image augmentation for YOLO training datasets.

The module intentionally keeps geometry transforms and their annotations
together.  A generated image without its transformed box/polygon labels is
not useful training material and can silently corrupt a training run.
"""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import random
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class DatasetAugmentationError(ValueError):
    """Raised when an augmentation configuration or source image is invalid."""


_RANGE_DEFAULTS = {
    "rotation": ("rotation_min_degrees", "rotation_max_degrees", 0.0),
    "brightness": ("brightness_min", "brightness_max", 1.0),
    "contrast": ("contrast_min", "contrast_max", 1.0),
    "saturation": ("saturation_min", "saturation_max", 1.0),
    "blur": ("gaussian_blur_radius_min", "gaussian_blur_radius_max", 0.0),
    "noise": ("gaussian_noise_std_min", "gaussian_noise_std_max", 0.0),
}


def validate_augmentation_options(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize user options and reject configurations that do nothing."""

    normalized = dict(options)
    normalized["horizontal_flip"] = bool(normalized.get("horizontal_flip", False))
    normalized["vertical_flip"] = bool(normalized.get("vertical_flip", False))

    has_effect = normalized["horizontal_flip"] or normalized["vertical_flip"]
    for label, (minimum_key, maximum_key, neutral) in _RANGE_DEFAULTS.items():
        try:
            minimum = float(normalized.get(minimum_key, neutral))
            maximum = float(normalized.get(maximum_key, neutral))
        except (TypeError, ValueError) as exc:
            raise DatasetAugmentationError(f"{label} 的范围必须是有效数字。") from exc
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise DatasetAugmentationError(f"{label} 的范围必须是有效数字。")
        if minimum > maximum:
            raise DatasetAugmentationError(f"{label} 的最小值不能大于最大值。")
        normalized[minimum_key] = minimum
        normalized[maximum_key] = maximum
        if not (math.isclose(minimum, neutral) and math.isclose(maximum, neutral)):
            has_effect = True

    if not has_effect:
        raise DatasetAugmentationError("请至少选择一种会改变图片的增强方式。")
    return normalized


def _sample_value(rng: random.Random, minimum: float, maximum: float) -> float:
    return minimum if math.isclose(minimum, maximum) else rng.uniform(minimum, maximum)


def _is_normalized(values: list[float]) -> bool:
    return all(-0.000001 <= value <= 1.000001 for value in values)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _transform_point(
    x: float,
    y: float,
    *,
    width: int,
    height: int,
    horizontal_flip: bool,
    vertical_flip: bool,
    rotation_degrees: float,
) -> tuple[float, float]:
    if horizontal_flip:
        x = width - x
    if vertical_flip:
        y = height - y
    if not math.isclose(rotation_degrees, 0.0):
        radians = math.radians(rotation_degrees)
        center_x = width / 2
        center_y = height / 2
        delta_x = x - center_x
        delta_y = y - center_y
        cosine = math.cos(radians)
        sine = math.sin(radians)
        x = center_x + (cosine * delta_x) + (sine * delta_y)
        y = center_y - (sine * delta_x) + (cosine * delta_y)
    return _clamp(x, 0.0, float(width)), _clamp(y, 0.0, float(height))


def _transform_boxes(
    boxes: list[Any],
    *,
    width: int,
    height: int,
    horizontal_flip: bool,
    vertical_flip: bool,
    rotation_degrees: float,
) -> list[dict[str, Any]]:
    transformed: list[dict[str, Any]] = []
    for raw_box in boxes:
        if not isinstance(raw_box, dict):
            continue
        try:
            x = float(raw_box["x"])
            y = float(raw_box["y"])
            box_width = float(raw_box["width"])
            box_height = float(raw_box["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if box_width <= 0 or box_height <= 0:
            continue
        normalized = _is_normalized([x, y, box_width, box_height])
        if normalized:
            x *= width
            y *= height
            box_width *= width
            box_height *= height
        corners = (
            (x, y),
            (x + box_width, y),
            (x + box_width, y + box_height),
            (x, y + box_height),
        )
        points = [
            _transform_point(
                point_x,
                point_y,
                width=width,
                height=height,
                horizontal_flip=horizontal_flip,
                vertical_flip=vertical_flip,
                rotation_degrees=rotation_degrees,
            )
            for point_x, point_y in corners
        ]
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points)
        bottom = max(point[1] for point in points)
        if right - left < 0.5 or bottom - top < 0.5:
            continue
        box = dict(raw_box)
        if normalized:
            box.update(
                {
                    "x": round(left / width, 6),
                    "y": round(top / height, 6),
                    "width": round((right - left) / width, 6),
                    "height": round((bottom - top) / height, 6),
                }
            )
        else:
            box.update(
                {
                    "x": round(left, 3),
                    "y": round(top, 3),
                    "width": round(right - left, 3),
                    "height": round(bottom - top, 3),
                }
            )
        transformed.append(box)
    return transformed


def _transform_segments(
    segments: list[Any],
    *,
    width: int,
    height: int,
    horizontal_flip: bool,
    vertical_flip: bool,
    rotation_degrees: float,
) -> list[dict[str, Any]]:
    transformed: list[dict[str, Any]] = []
    for raw_segment in segments:
        if not isinstance(raw_segment, dict):
            continue
        raw_points = raw_segment.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 3:
            continue
        points: list[tuple[float, float]] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                continue
            try:
                points.append((float(raw_point[0]), float(raw_point[1])))
            except (TypeError, ValueError):
                continue
        if len(points) < 3:
            continue
        normalized = _is_normalized([coordinate for point in points for coordinate in point])
        updated_points = [
            _transform_point(
                point_x * width if normalized else point_x,
                point_y * height if normalized else point_y,
                width=width,
                height=height,
                horizontal_flip=horizontal_flip,
                vertical_flip=vertical_flip,
                rotation_degrees=rotation_degrees,
            )
            for point_x, point_y in points
        ]
        if len(set(updated_points)) < 3:
            continue
        segment = dict(raw_segment)
        if normalized:
            segment["points"] = [
                [round(point_x / width, 6), round(point_y / height, 6)]
                for point_x, point_y in updated_points
            ]
        else:
            segment["points"] = [[round(point_x, 3), round(point_y, 3)] for point_x, point_y in updated_points]
        transformed.append(segment)
    return transformed


def _transform_annotation(
    annotation: dict[str, Any],
    annotation_type: str,
    *,
    width: int,
    height: int,
    horizontal_flip: bool,
    vertical_flip: bool,
    rotation_degrees: float,
) -> dict[str, Any]:
    result = deepcopy(annotation or {})
    if annotation_type == "DETECTION":
        result["boxes"] = _transform_boxes(
            list(result.get("boxes") or []),
            width=width,
            height=height,
            horizontal_flip=horizontal_flip,
            vertical_flip=vertical_flip,
            rotation_degrees=rotation_degrees,
        )
    elif annotation_type == "SEGMENTATION":
        field = "segments" if "segments" in result or "polygons" not in result else "polygons"
        result[field] = _transform_segments(
            list(result.get(field) or []),
            width=width,
            height=height,
            horizontal_flip=horizontal_flip,
            vertical_flip=vertical_flip,
            rotation_degrees=rotation_degrees,
        )
    return result


def _open_rgb_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except Exception as exc:
        raise DatasetAugmentationError(f"无法读取图片：{path.name}") from exc


def augment_image_and_annotation(
    source_path: str | Path,
    annotation: dict[str, Any],
    annotation_type: str,
    options: dict[str, Any],
    *,
    seed: int,
) -> tuple[Image.Image, dict[str, Any], dict[str, Any]]:
    """Apply one deterministic variation and return the image, labels and audit data."""

    normalized_options = validate_augmentation_options(options)
    image = _open_rgb_image(Path(source_path))
    rng = random.Random(seed)
    numpy_rng = np.random.default_rng(seed)

    horizontal_flip = normalized_options["horizontal_flip"]
    vertical_flip = normalized_options["vertical_flip"]
    rotation_degrees = _sample_value(
        rng,
        normalized_options["rotation_min_degrees"],
        normalized_options["rotation_max_degrees"],
    )
    brightness = _sample_value(rng, normalized_options["brightness_min"], normalized_options["brightness_max"])
    contrast = _sample_value(rng, normalized_options["contrast_min"], normalized_options["contrast_max"])
    saturation = _sample_value(rng, normalized_options["saturation_min"], normalized_options["saturation_max"])
    blur_radius = _sample_value(
        rng,
        normalized_options["gaussian_blur_radius_min"],
        normalized_options["gaussian_blur_radius_max"],
    )
    noise_std = _sample_value(
        rng,
        normalized_options["gaussian_noise_std_min"],
        normalized_options["gaussian_noise_std_max"],
    )

    width, height = image.size
    transformed_annotation = _transform_annotation(
        annotation,
        annotation_type.upper(),
        width=width,
        height=height,
        horizontal_flip=horizontal_flip,
        vertical_flip=vertical_flip,
        rotation_degrees=rotation_degrees,
    )

    if horizontal_flip:
        image = ImageOps.mirror(image)
    if vertical_flip:
        image = ImageOps.flip(image)
    if not math.isclose(rotation_degrees, 0.0):
        image = image.rotate(
            rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=(0, 0, 0),
        )
    if not math.isclose(brightness, 1.0):
        image = ImageEnhance.Brightness(image).enhance(brightness)
    if not math.isclose(contrast, 1.0):
        image = ImageEnhance.Contrast(image).enhance(contrast)
    if not math.isclose(saturation, 1.0):
        image = ImageEnhance.Color(image).enhance(saturation)
    if blur_radius > 0:
        image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    if noise_std > 0:
        pixels = np.asarray(image, dtype=np.float32)
        noise = numpy_rng.normal(loc=0.0, scale=noise_std, size=pixels.shape)
        image = Image.fromarray(np.clip(pixels + noise, 0, 255).astype(np.uint8))

    applied = {
        "horizontal_flip": horizontal_flip,
        "vertical_flip": vertical_flip,
        "rotation_degrees": round(rotation_degrees, 4),
        "brightness": round(brightness, 4),
        "contrast": round(contrast, 4),
        "saturation": round(saturation, 4),
        "gaussian_blur_radius": round(blur_radius, 4),
        "gaussian_noise_std": round(noise_std, 4),
    }
    return image, transformed_annotation, applied


def save_augmented_image(image: Image.Image, target_path: str | Path) -> None:
    """Persist an augmented RGB image with a format suitable for its suffix."""

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        image.save(target, format="JPEG", quality=95, optimize=True)
    elif suffix == ".png":
        image.save(target, format="PNG", optimize=True)
    elif suffix == ".webp":
        image.save(target, format="WEBP", quality=95)
    else:
        image.save(target)
