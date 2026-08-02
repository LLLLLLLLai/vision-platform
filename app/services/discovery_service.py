from pathlib import Path
from typing import Any

import cv2


ALLOWED_OBJECT_TYPES = {
    "FUSE",
    "SCREW",
    "CONNECTOR",
    "HARNESS",
    "PCBA",
    "BUSBAR",
    "LABEL",
    "RELAY",
    "TERMINAL",
    "OBJECT",
}

OBJECT_TYPE_NAMES = {
    "FUSE": "保险丝",
    "SCREW": "螺丝",
    "CONNECTOR": "连接器",
    "HARNESS": "线束连接点",
    "PCBA": "PCBA",
    "BUSBAR": "铜排",
    "LABEL": "标签",
    "RELAY": "继电器",
    "TERMINAL": "端子",
    "OBJECT": "其他物体",
}

SPECIALIZED_TARGET_TEMPLATES = {
    "HARNESS": [
        {
            "label": "线束螺钉连接点",
            "prompt_en": "cable lug attached to bolt",
            "target_kind": "HARNESS_BOLTED_LUG",
            "max_area_ratio": 0.06,
            "min_confidence": 0.16,
            "recommend_threshold": 0.28,
        },
        {
            "label": "线束插接点",
            "prompt_en": "wire harness connector plugged into socket",
            "target_kind": "HARNESS_CONNECTOR",
            "max_area_ratio": 0.08,
            "min_confidence": 0.16,
            "recommend_threshold": 0.28,
        },
        {
            "label": "高压线束端子",
            "prompt_en": "orange high voltage cable terminal",
            "target_kind": "HARNESS_TERMINAL",
            "max_area_ratio": 0.08,
            "min_confidence": 0.16,
            "recommend_threshold": 0.28,
        },
        {
            "label": "线束固定卡扣",
            "prompt_en": "wire harness retaining clip",
            "target_kind": "HARNESS_CLIP",
            "max_area_ratio": 0.05,
            "min_confidence": 0.14,
            "recommend_threshold": 0.26,
        },
    ],
    "SCREW": [
        {
            "label": "六角螺栓头",
            "prompt_en": "silver hex bolt head",
            "target_kind": "HEX_BOLT_HEAD",
            "max_area_ratio": 0.025,
            "min_confidence": 0.12,
            "recommend_threshold": 0.24,
        },
        {
            "label": "固定螺钉",
            "prompt_en": "metal screw head",
            "target_kind": "SCREW_HEAD",
            "max_area_ratio": 0.02,
            "min_confidence": 0.12,
            "recommend_threshold": 0.24,
        },
        {
            "label": "线束固定螺栓",
            "prompt_en": "cable lug mounting bolt",
            "target_kind": "CABLE_LUG_BOLT",
            "max_area_ratio": 0.04,
            "min_confidence": 0.12,
            "recommend_threshold": 0.24,
        },
        {
            "label": "端子螺钉",
            "prompt_en": "terminal block screw",
            "target_kind": "TERMINAL_SCREW",
            "max_area_ratio": 0.03,
            "min_confidence": 0.12,
            "recommend_threshold": 0.24,
        },
    ],
}

HARNESS_SEGMENTATION_TEMPLATES = [
    {
        "label": "黑色线束",
        "prompt_en": "black wiring harness cable",
        "target_kind": "HARNESS_SEGMENT_SEED",
        "max_area_ratio": 0.55,
        "min_confidence": 0.14,
        "recommend_threshold": 0.22,
        "color": "black",
    },
    {
        "label": "黑色绝缘线缆",
        "prompt_en": "black insulated electrical cable",
        "target_kind": "HARNESS_SEGMENT_SEED",
        "max_area_ratio": 0.55,
        "min_confidence": 0.14,
        "recommend_threshold": 0.22,
        "color": "black",
    },
    {
        "label": "低压电线",
        "prompt_en": "thin electrical wire",
        "target_kind": "HARNESS_SEGMENT_SEED",
        "max_area_ratio": 0.30,
        "min_confidence": 0.14,
        "recommend_threshold": 0.22,
        "color": "unknown",
    },
    {
        "label": "连接器线束",
        "prompt_en": "wire bundle connected to connector",
        "target_kind": "HARNESS_SEGMENT_SEED",
        "max_area_ratio": 0.40,
        "min_confidence": 0.14,
        "recommend_threshold": 0.22,
        "color": "unknown",
    },
]


def build_localization_groups(
    inventory: list[dict[str, Any]],
    requested_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_text = " ".join(requested_types or []).lower()
    inventory_types = {item["object_type"] for item in inventory}
    include_harness = "HARNESS" in inventory_types or any(
        token in requested_text for token in ("harness", "wire", "cable", "线束")
    )
    include_screw = "SCREW" in inventory_types or any(
        token in requested_text for token in ("screw", "bolt", "螺丝", "螺钉")
    )
    general_inventory = [
        {
            **item,
            "target_kind": item.get("target_kind") or item["object_type"],
            "max_area_ratio": 0.75,
            "min_confidence": 0.22,
            "recommend_threshold": 0.40,
            "type_limit": max(item["expected_count"] + 2, round(item["expected_count"] * 1.5)),
        }
        for item in inventory
        if item["object_type"] not in {"HARNESS", "SCREW"}
    ]
    groups: list[dict[str, Any]] = []
    if general_inventory:
        groups.append(
            {
                "name": "GENERAL_OBJECTS",
                "threshold": 0.22,
                "text_threshold": 0.18,
                "inventory": general_inventory,
            }
        )

    def specialized_group(object_type: str, name: str) -> dict[str, Any]:
        source_counts = [
            item["expected_count"]
            for item in inventory
            if item["object_type"] == object_type
        ]
        expected_count = max(source_counts, default=4)
        targets = [
            {
                **template,
                "object_type": object_type,
                "expected_count": expected_count,
                "type_limit": max(8, expected_count * 2),
            }
            for template in SPECIALIZED_TARGET_TEMPLATES[object_type]
        ]
        return {
            "name": name,
            "threshold": 0.10,
            "text_threshold": 0.10,
            "inventory": targets,
        }

    if include_harness:
        groups.append(
            {
                "name": "HARNESS_SEGMENTATION_SEEDS",
                "threshold": 0.12,
                "text_threshold": 0.12,
                "inventory": [
                    {
                        **template,
                        "object_type": "HARNESS",
                        "expected_count": 8,
                        "type_limit": 10,
                    }
                    for template in HARNESS_SEGMENTATION_TEMPLATES
                ],
            }
        )
        groups.append(specialized_group("HARNESS", "HARNESS_KEYPOINTS"))
    if include_screw:
        groups.append(specialized_group("SCREW", "FASTENER_KEYPOINTS"))
    return groups


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_box(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    coordinates = [_number(item) for item in value]
    if any(item is None for item in coordinates):
        return None
    scale = 1.0 if max(coordinates) <= 1.0 else 1000.0
    x1, y1, x2, y2 = [max(0.0, min(1.0, item / scale)) for item in coordinates]
    if x2 <= x1 or y2 <= y1:
        return None
    if (x2 - x1) * (y2 - y1) < 0.0001:
        return None
    return [x1, y1, x2, y2]


def _absolute_box(value: Any, width: int, height: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4 or width <= 0 or height <= 0:
        return None
    coordinates = [_number(item) for item in value]
    if any(item is None for item in coordinates):
        return None
    x1, y1, x2, y2 = coordinates
    return _normalized_box([x1 / width, y1 / height, x2 / width, y2 / height])


def _intersection_over_union(left: list[float], right: list[float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def normalize_discovery_result(
    response: dict[str, Any],
    max_objects: int = 30,
) -> list[dict[str, Any]]:
    parsed = response.get("result", {}).get("parsed")
    raw_objects = parsed.get("objects", []) if isinstance(parsed, dict) else []
    candidates: list[dict[str, Any]] = []
    for raw in raw_objects:
        if not isinstance(raw, dict):
            continue
        box = _normalized_box(raw.get("bbox"))
        if box is None:
            continue
        object_type = str(raw.get("object_type") or "OBJECT").upper().strip()
        if object_type not in ALLOWED_OBJECT_TYPES:
            object_type = "OBJECT"
        confidence = _number(raw.get("confidence"))
        confidence = max(0.0, min(1.0, confidence if confidence is not None else 0.5))
        label = str(raw.get("label") or object_type).strip()[:100]
        prompt = str(raw.get("prompt_en") or label).strip()[:200]
        duplicate = next(
            (
                item
                for item in candidates
                if item["object_type"] == object_type
                and _intersection_over_union(item["bbox"], box) >= 0.88
            ),
            None,
        )
        candidate = {
            "candidate_id": f"CAND_{len(candidates) + 1}",
            "label": label,
            "object_type": object_type,
            "prompt_en": prompt,
            "confidence": round(confidence, 4),
            "bbox": [round(item, 6) for item in box],
            "x_ratio": round(box[0], 6),
            "y_ratio": round(box[1], 6),
            "width_ratio": round(box[2] - box[0], 6),
            "height_ratio": round(box[3] - box[1], 6),
        }
        if duplicate is not None:
            if candidate["confidence"] > duplicate["confidence"]:
                duplicate.update(candidate)
            continue
        candidates.append(candidate)
        if len(candidates) >= max_objects:
            break
    type_counts: dict[str, int] = {}
    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"CAND_{index}"
        object_type = candidate["object_type"]
        type_counts[object_type] = type_counts.get(object_type, 0) + 1
        candidate["source_label"] = candidate["label"]
        candidate["label"] = (
            f"{OBJECT_TYPE_NAMES[object_type]} {type_counts[object_type]:02d}"
        )
    return candidates


def normalize_inventory_result(
    response: dict[str, Any],
    max_types: int = 12,
) -> list[dict[str, Any]]:
    parsed = response.get("result", {}).get("parsed")
    raw_objects = parsed.get("objects", []) if isinstance(parsed, dict) else []
    inventory: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_objects:
        if not isinstance(raw, dict):
            continue
        object_type = str(raw.get("object_type") or "OBJECT").upper().strip()
        if object_type not in ALLOWED_OBJECT_TYPES:
            object_type = "OBJECT"
        prompt = str(raw.get("prompt_en") or raw.get("label") or "").strip().lower()
        if not prompt:
            continue
        identity = (object_type, prompt)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            expected_count = int(raw.get("expected_count") or 1)
        except (TypeError, ValueError):
            expected_count = 1
        inventory.append(
            {
                "label": str(raw.get("label") or OBJECT_TYPE_NAMES[object_type])[:100],
                "object_type": object_type,
                "prompt_en": prompt[:200],
                "expected_count": max(1, min(50, expected_count)),
            }
        )
        if len(inventory) >= max_types:
            break
    return inventory


def _inventory_match(
    detected_label: str,
    inventory: list[dict[str, Any]],
) -> dict[str, Any] | None:
    label = detected_label.lower().strip().strip(".")
    exact = next((item for item in inventory if item["prompt_en"] == label), None)
    if exact is not None:
        return exact
    return next(
        (
            item
            for item in inventory
            if item["prompt_en"] in label or label in item["prompt_en"]
        ),
        None,
    )


def normalize_grounding_result(
    response: dict[str, Any],
    inventory: list[dict[str, Any]],
    max_objects: int = 40,
) -> list[dict[str, Any]]:
    width = int(response.get("image_width") or 0)
    height = int(response.get("image_height") or 0)
    raw_objects = response.get("objects", [])
    candidates: list[dict[str, Any]] = []
    for raw in raw_objects:
        if not isinstance(raw, dict):
            continue
        box = _absolute_box(raw.get("bbox"), width, height)
        if box is None:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.75:
            continue
        inventory_item = _inventory_match(str(raw.get("label") or ""), inventory)
        if inventory_item is None:
            continue
        area_ratio = (box[2] - box[0]) * (box[3] - box[1])
        if area_ratio > float(inventory_item.get("max_area_ratio", 0.75)):
            continue
        score = _number(raw.get("score"))
        score = max(0.0, min(1.0, score if score is not None else 0.0))
        if score < float(inventory_item.get("min_confidence", 0.0)):
            continue
        candidates.append(
            {
                "object_type": inventory_item["object_type"],
                "target_kind": inventory_item.get("target_kind"),
                "target_label": inventory_item.get("label"),
                "source_label": str(raw.get("label") or ""),
                "prompt_en": inventory_item["prompt_en"],
                "expected_count": inventory_item["expected_count"],
                "type_limit": inventory_item.get("type_limit"),
                "recommend_threshold": inventory_item.get("recommend_threshold", 0.40),
                "confidence": round(score, 4),
                "bbox": [round(item, 6) for item in box],
                "x_ratio": round(box[0], 6),
                "y_ratio": round(box[1], 6),
                "width_ratio": round(box[2] - box[0], 6),
                "height_ratio": round(box[3] - box[1], 6),
                "engine": "GROUNDING_DINO",
            }
        )
    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    filtered: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    for candidate in candidates:
        object_type = candidate["object_type"]
        expected_count = candidate["expected_count"]
        type_limit = int(
            candidate.get("type_limit")
            or max(expected_count + 2, round(expected_count * 1.5))
        )
        if type_counts.get(object_type, 0) >= type_limit:
            continue
        if any(
            item["object_type"] == object_type
            and _intersection_over_union(item["bbox"], candidate["bbox"]) >= 0.55
            for item in filtered
        ):
            continue
        type_counts[object_type] = type_counts.get(object_type, 0) + 1
        candidate["candidate_id"] = f"CAND_{len(filtered) + 1}"
        candidate["review_status"] = (
            "RECOMMENDED"
            if candidate["confidence"] >= candidate["recommend_threshold"]
            else "REVIEW_REQUIRED"
        )
        base_label = candidate.get("target_label") or OBJECT_TYPE_NAMES[object_type]
        candidate["label"] = f"{base_label} {type_counts[object_type]:02d}"
        filtered.append(candidate)
        if len(filtered) >= max_objects:
            break
    return filtered


def enrich_harness_connection_candidates(
    image_path: str,
    candidates: list[dict[str, Any]],
    max_objects: int = 60,
) -> list[dict[str, Any]]:
    image_file = Path(image_path)
    image = cv2.imread(str(image_file)) if image_file.is_file() else None
    if image is None:
        return candidates[:max_objects]
    image_height, image_width = image.shape[:2]
    derived: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("object_type") not in {"SCREW", "CONNECTOR", "TERMINAL"}:
            continue
        box = candidate.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        margin_x = max(0.018, width * 1.4)
        margin_y = max(0.018, height * 1.4)
        search_box = [
            max(0.0, x1 - margin_x),
            max(0.0, y1 - margin_y),
            min(1.0, x2 + margin_x),
            min(1.0, y2 + margin_y),
        ]
        px1 = max(0, int(search_box[0] * image_width))
        py1 = max(0, int(search_box[1] * image_height))
        px2 = min(image_width, int(search_box[2] * image_width))
        py2 = min(image_height, int(search_box[3] * image_height))
        region = image[py1:py2, px1:px2]
        if region.size == 0:
            continue
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        orange_mask = cv2.inRange(hsv, (4, 85, 65), (28, 255, 255))
        orange_ratio = float(cv2.countNonZero(orange_mask)) / float(orange_mask.size)
        if orange_ratio < 0.015:
            continue
        source_type = candidate["object_type"]
        target_kind = (
            "HARNESS_BOLTED_LUG"
            if source_type == "SCREW"
            else "HARNESS_CONNECTOR"
        )
        label = (
            "橙色线束螺钉连接点"
            if source_type == "SCREW"
            else "橙色线束插接点"
        )
        confidence = min(
            0.95,
            float(candidate.get("confidence") or 0.0) + min(0.35, orange_ratio * 3.0),
        )
        derived.append(
            {
                "object_type": "HARNESS",
                "target_kind": target_kind,
                "target_label": label,
                "source_label": candidate.get("label", ""),
                "prompt_en": "orange cable connection near fastener or connector",
                "expected_count": 1,
                "confidence": round(confidence, 4),
                "orange_ratio": round(orange_ratio, 4),
                "bbox": [round(item, 6) for item in search_box],
                "x_ratio": round(search_box[0], 6),
                "y_ratio": round(search_box[1], 6),
                "width_ratio": round(search_box[2] - search_box[0], 6),
                "height_ratio": round(search_box[3] - search_box[1], 6),
                "engine": "GROUNDING_DINO + OPENCV_COLOR_ADJACENCY",
                "review_status": (
                    "RECOMMENDED"
                    if orange_ratio >= 0.04 and confidence >= 0.28
                    else "REVIEW_REQUIRED"
                ),
            }
        )
    if derived:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get("object_type") != "HARNESS"
            or float(candidate.get("confidence") or 0.0) >= 0.22
        ]
    merged = sorted(candidates + derived, key=lambda item: item["confidence"], reverse=True)
    filtered: list[dict[str, Any]] = []
    label_counts: dict[str, int] = {}
    for candidate in merged:
        if any(
            item.get("object_type") == candidate.get("object_type")
            and _intersection_over_union(item["bbox"], candidate["bbox"]) >= 0.55
            for item in filtered
        ):
            continue
        base_label = candidate.get("target_label")
        if base_label:
            label_counts[base_label] = label_counts.get(base_label, 0) + 1
            candidate["label"] = f"{base_label} {label_counts[base_label]:02d}"
        candidate["candidate_id"] = f"CAND_{len(filtered) + 1}"
        filtered.append(candidate)
        if len(filtered) >= max_objects:
            break
    return filtered
