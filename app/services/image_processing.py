from pathlib import Path

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


def color_ratio(image_path: str, color_name: str) -> float:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Unable to read image: {image_path}")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    ranges = COLOR_RANGES.get(color_name.upper())
    if not ranges:
        raise ValueError(f"Unsupported color: {color_name}")
    combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in ranges:
        mask = cv2.inRange(
            hsv,
            np.array(lower, dtype=np.uint8),
            np.array(upper, dtype=np.uint8),
        )
        combined = cv2.bitwise_or(combined, mask)
    return float(np.count_nonzero(combined)) / float(combined.size)


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

