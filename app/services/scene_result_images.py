from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from app.core.config import PROJECT_ROOT
from app.services.scenario_runtime import scenario_runtime


@dataclass(frozen=True)
class SceneResultSnapshot:
    id: int
    result: str
    output_json: dict[str, Any]


def _absolute_image_path(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _annotation_from_execution(execution: SceneResultSnapshot) -> Path | None:
    artifact_root = (PROJECT_ROOT / "uploads" / "scene_annotations" / str(execution.id)).resolve()
    traces = (execution.output_json or {}).get("traces") or []
    for trace in reversed(traces):
        if not isinstance(trace, dict) or trace.get("node_type") != "IMAGE_ANNOTATE":
            continue
        node_output = trace.get("output")
        if not isinstance(node_output, dict):
            continue
        image_path = node_output.get("image_path")
        if not isinstance(image_path, str) or not image_path:
            continue
        candidate = Path(image_path).resolve()
        if candidate.is_relative_to(artifact_root) and candidate.is_file():
            return candidate
    return None


def _detection_preview(execution: SceneResultSnapshot, input_image_path: Path) -> Path | None:
    traces = (execution.output_json or {}).get("traces") or []
    for trace in reversed(traces):
        if not isinstance(trace, dict) or trace.get("node_type") != "VISION_MODEL":
            continue
        node_input = trace.get("input")
        output = trace.get("output")
        if not isinstance(node_input, dict) or not isinstance(output, dict):
            continue
        model_image_path = node_input.get("image_path")
        objects = output.get("objects")
        if not isinstance(model_image_path, str) or not isinstance(objects, list) or not objects:
            continue
        if _absolute_image_path(model_image_path) != input_image_path:
            continue
        preview = scenario_runtime._execute_image_annotate(
            str(input_image_path),
            objects,
            None,
            {},
            execution_id=execution.id,
            node_key="api_result_boxes",
        )
        return Path(preview["image_path"])
    return None


def build_scene_result_image(execution: SceneResultSnapshot, input_image_path: str) -> Path:
    source = _absolute_image_path(input_image_path)
    annotated = _annotation_from_execution(execution) or _detection_preview(execution, source)
    selected = annotated or source
    if not selected.is_file():
        raise FileNotFoundError("场景输入图片或画框结果不存在，无法上传结果图。")
    output_directory = PROJECT_ROOT / "uploads" / "scene_annotations" / str(execution.id)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / "scene_result.jpg"
    with Image.open(selected) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        draw = ImageDraw.Draw(image)
        status = str(execution.result or "ERROR").upper()
        color = (32, 150, 85) if status == "OK" else (210, 58, 65)
        label = status if status in {"OK", "NG", "UNCERTAIN", "ERROR"} else "ERROR"
        draw.rectangle((0, 0, 116, 30), fill=color)
        draw.text((8, 8), label, fill=(255, 255, 255))
        image.save(output_path, format="JPEG", quality=95)
    return output_path
