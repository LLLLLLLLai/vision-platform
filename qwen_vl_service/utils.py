import json
from typing import Any


def parse_json_object(value: str) -> dict[str, Any] | None:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_partial_object_list(value: str, key: str = "objects") -> list[dict[str, Any]]:
    marker = f'"{key}"'
    marker_index = value.find(marker)
    if marker_index < 0:
        return []
    array_start = value.find("[", marker_index + len(marker))
    if array_start < 0:
        return []
    decoder = json.JSONDecoder()
    position = array_start + 1
    items: list[dict[str, Any]] = []
    while position < len(value):
        while position < len(value) and value[position] in " \r\n\t,":
            position += 1
        if position >= len(value) or value[position] == "]":
            break
        try:
            item, position = decoder.raw_decode(value, position)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            items.append(item)
    return items
