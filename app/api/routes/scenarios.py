import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
from secrets import compare_digest
import shutil
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session, selectinload

from app.core.config import PROJECT_ROOT, settings
from app.db.session import get_db
from app.models.intelligence import (
    AutomationJob,
    Dataset,
    DatasetItem,
    InspectionScenario,
    InspectionScenarioVersion,
    RoiScenarioBinding,
    ScenarioEdge,
    ScenarioExecution,
    ScenarioExecutionReview,
    ScenarioNode,
    VlmModelConfig,
    VisionModel,
    VisionModelVersion,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.services.loop_body_graph import loop_body_connections
from app.services.node_trace_status import with_node_trace_states
from app.services.openai_compatible_vlm import VlmConfigurationError, VlmRequestError
from app.services.scene_design_assistant import (
    DesignConversation, SceneDesign, generate_scene_design, validate_design_references,
)
from app.services.scenario_runtime import ScenarioRuntimeError, scenario_runtime
from app.services.dataset_collection_service import is_collection_only, scene_collection_targets
from app.services.scene_result_images import SceneResultSnapshot, build_scene_result_image
from app.services.scene_s3_storage import InvalidSceneS3KeyError, SceneS3Storage, SceneS3StorageError
from app.services.vision_model_runtime import resolve_weights_path, task_output_profile


# Management endpoints stay on ``router``.  Published scene invocation is
# intentionally exposed through ``public_router`` so the production inspection
# service can run it without carrying every management endpoint.
router = APIRouter()
public_router = APIRouter()

SCENE_MODES = {"VLM_DIRECT", "WORKFLOW"}
SCENE_API_FAILURE_CODE = 5001
SCENE_API_RESULT_UPLOAD_FAILURE_CODE = 5002
NODE_TYPES = {
    "START",
    "VLM",
    "VISION_MODEL",
    "OCR",
    "IMAGE_CROP",
    "IMAGE_ANNOTATE",
    "RULE",
    "WEB_API",
    "S3_UPLOAD",
    "IF",
    "LOOP",
    "ITERATION",
    "CONDITION_LOOP",
    "END",
}
REVIEW_VERDICTS = {"OK", "NG", "UNCERTAIN"}
IMAGE_INPUT_NAMES = {
    "image", "image_path", "image_paths", "image_file",
    "original_image_path", "source_image_path", "roi_bbox", "result_s3_key",
}
_DYNAMIC_BBOX_REFERENCE = re.compile(
    r"^\s*\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects(?:\.\d+)?\.bbox\s*\}\}\s*$"
)
_DYNAMIC_OBJECTS_REFERENCE = re.compile(
    r"^\s*\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects\s*\}\}\s*$"
)
_DYNAMIC_NODE_REFERENCE = re.compile(
    r"\{\{\s*nodes\.([A-Za-z0-9_-]+)(?:\.|\s*\}\})"
)
_DYNAMIC_NODE_OUTPUT_REFERENCE = re.compile(
    r"^\s*\{\{\s*nodes\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\s*\}\}\s*$"
)
_DYNAMIC_RESPONSE_OUTPUT_REFERENCE = re.compile(
    r"^\s*\{\{\s*response\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\s*\}\}\s*$"
)


def _trim_text(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _generate_scene_code(database: Session) -> str:
    for _ in range(5):
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        code = f"SCENE_{timestamp}_{uuid4().hex[:6].upper()}"
        exists = database.scalar(
            select(InspectionScenario.id).where(InspectionScenario.code == code)
        )
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="无法生成唯一场景编码，请重试。")


class ScenarioCreate(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="GENERAL", max_length=100)
    mode: str = "VLM_DIRECT"
    run_mode: Literal["DETECT", "COLLECT_ONLY"] = "DETECT"
    description: str | None = Field(default=None, max_length=1000)
    version: str = Field(default="1.0", max_length=50)
    prompt_template: str | None = Field(default=None, max_length=8000)
    primary_vlm_model_id: int | None = None
    review_vlm_model_id: int | None = None

    @field_validator("code", "name", "category", "mode", "version", mode="before")
    @classmethod
    def trim_required_text(cls, value: Any) -> Any:
        return _trim_text(value)

    @field_validator("description", "prompt_template", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("primary_vlm_model_id", "review_vlm_model_id", mode="before")
    @classmethod
    def normalize_optional_model_id(cls, value: Any) -> Any:
        return _blank_to_none(value)


class ScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


class VersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    source_version_id: int | None = None
    prompt_template: str | None = Field(default=None, max_length=8000)
    primary_vlm_model_id: int | None = None
    review_vlm_model_id: int | None = None
    input_schema_json: dict[str, Any] = Field(default_factory=dict)
    output_schema_json: dict[str, Any] = Field(default_factory=dict)
    definition_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("definition_json")
    @classmethod
    def validate_run_mode(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("run_mode", "DETECT") not in {"DETECT", "COLLECT_ONLY"}:
            raise ValueError("运行模式仅支持 DETECT 或 COLLECT_ONLY")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def trim_version(cls, value: Any) -> Any:
        return _trim_text(value)

    @field_validator("prompt_template", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("primary_vlm_model_id", "review_vlm_model_id", mode="before")
    @classmethod
    def normalize_optional_model_id(cls, value: Any) -> Any:
        return _blank_to_none(value)


class VersionUpdate(BaseModel):
    prompt_template: str | None = Field(default=None, max_length=8000)
    primary_vlm_model_id: int | None = None
    review_vlm_model_id: int | None = None
    input_schema_json: dict[str, Any] | None = None
    output_schema_json: dict[str, Any] | None = None
    definition_json: dict[str, Any] | None = None

    @field_validator("definition_json")
    @classmethod
    def validate_run_mode(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and value.get("run_mode", "DETECT") not in {"DETECT", "COLLECT_ONLY"}:
            raise ValueError("运行模式仅支持 DETECT 或 COLLECT_ONLY")
        return value

    @field_validator("prompt_template", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("primary_vlm_model_id", "review_vlm_model_id", mode="before")
    @classmethod
    def normalize_optional_model_id(cls, value: Any) -> Any:
        return _blank_to_none(value)


class NodeCreate(BaseModel):
    node_key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    node_type: str
    config_json: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    canvas_x: float = 0.0
    canvas_y: float = 0.0
    auto_connect: bool = True


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config_json: dict[str, Any] | None = None
    sort_order: int | None = None
    canvas_x: float | None = None
    canvas_y: float | None = None
    enabled: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        return _blank_to_none(value)


class EdgeCreate(BaseModel):
    source_node_key: str = Field(min_length=1, max_length=100)
    target_node_key: str = Field(min_length=1, max_length=100)
    mapping_json: dict[str, Any] = Field(default_factory=dict)


class EdgeUpdate(BaseModel):
    mapping_json: dict[str, Any] = Field(default_factory=dict)


class RoiBindingRequest(BaseModel):
    scenario_version_id: int
    input_mapping_json: dict[str, Any] = Field(default_factory=dict)


def _scene_input_fields(version: InspectionScenarioVersion) -> list[dict[str, Any]]:
    """Return operator-configurable inputs exposed by a published scene.

    Image data is injected by the recipe runtime, so it is intentionally not
    exposed as a selectable ROI mapping field.
    """

    schema: Any = version.input_schema_json or {}
    if version.scenario.mode == "WORKFLOW":
        start = next(
            (node for node in version.nodes if node.enabled and node.node_type.upper() == "START"),
            None,
        )
        schema = (start.config_json or {}).get("inputs", []) if start else []

    raw_fields: Any
    if isinstance(schema, dict):
        raw_fields = schema.get("fields", schema.get("inputs", schema))
    else:
        raw_fields = schema

    fields: list[dict[str, Any]] = []
    if isinstance(raw_fields, dict):
        raw_fields = [
            {"name": key, **(value if isinstance(value, dict) else {"label": value})}
            for key, value in raw_fields.items()
        ]
    if not isinstance(raw_fields, list):
        return fields
    seen: set[str] = set()
    for item in raw_fields:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "").strip()
        if not name or name.lower() in IMAGE_INPUT_NAMES or name in seen:
            continue
        seen.add(name)
        field = {
            "name": name,
            "label": str(item.get("label") or item.get("title") or name),
            "type": str(item.get("type") or "TEXT").upper(),
        }
        if bool(item.get("required", False)):
            field["required"] = True
        fields.append(field)
    return fields


class ScenarioExecuteRequest(BaseModel):
    image_path: str = Field(min_length=1, max_length=1000)
    context: dict[str, Any] = Field(default_factory=dict)
    enqueue_review: bool = False


class PublishedScenarioInvokeRequest(BaseModel):
    """Stable external API payload for the currently published scene version.

    ``image_path`` is a reserved, top-level input injected into the Start node.
    Business inputs belong in ``inputs``. The current runtime executes one image
    per request; callers with multiple images should invoke the scene once for
    each image, or use the recipe-level ``/api/detect`` integration endpoint.
    """

    request_id: str | None = Field(default=None, max_length=100)
    image_path: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    enqueue_review: bool = False
    result_s3_key: str | None = Field(default=None, min_length=1, max_length=900)

    @field_validator("request_id", "image_path", mode="before")
    @classmethod
    def normalize_request_text(cls, value: Any) -> Any:
        return _blank_to_none(value)


class ManualReviewRequest(BaseModel):
    verdict: str
    note: str | None = Field(default=None, max_length=2000)


def _validate_vlm(database: Session, model_id: int | None) -> None:
    if model_id is None:
        return
    model = database.get(VlmModelConfig, model_id)
    if model is None or model.is_deleted:
        raise HTTPException(status_code=400, detail="选择的 VLM 模型不存在。")


def _validate_published_vision_model_node(database: Session, node: ScenarioNode) -> None:
    raw_version_id = (node.config_json or {}).get("model_version_id")
    try:
        version_id = int(raw_version_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"模型节点“{node.name}”必须选择一个已发布模型版本。",
        ) from exc
    version = database.scalar(
        select(VisionModelVersion)
        .options(selectinload(VisionModelVersion.vision_model))
        .where(VisionModelVersion.id == version_id)
    )
    if version is None or version.is_deleted or version.status not in {"PUBLISHED", "ARCHIVED"}:
        raise HTTPException(
            status_code=400,
            detail=f"模型节点“{node.name}”引用的模型版本不存在或尚未发布。",
        )
    model: VisionModel | None = version.vision_model
    if model is None or model.is_deleted or not model.enabled:
        raise HTTPException(
            status_code=400,
            detail=f"模型节点“{node.name}”引用的训练模型不存在或已停用。",
        )
    weights_path = resolve_weights_path(version.weights_path)
    if weights_path is None or not weights_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"模型节点“{node.name}”引用的权重文件不存在，不能发布场景。",
        )


def _validate_ocr_node(node: ScenarioNode) -> None:
    config = node.config_json or {}
    try:
        score = float(config.get("min_score", 0.0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="OCR 最低文字置信度必须是 0 到 1 的数字。") from exc
    if not 0 <= score <= 1:
        raise HTTPException(status_code=400, detail="OCR 最低文字置信度必须在 0 到 1 之间。")
    image = (config.get("input_mapping") or {}).get("image_path") or config.get("image_path")
    if not isinstance(image, str) or not image.strip():
        raise HTTPException(status_code=400, detail="OCR 节点必须配置输入图片。")


def _validate_image_crop_node(
    database: Session,
    scenario_version: InspectionScenarioVersion,
    node: ScenarioNode,
) -> None:
    config = node.config_json or {}
    input_mapping = config.get("input_mapping") or config.get("context") or {}
    mapped_bbox = input_mapping.get("bbox") if isinstance(input_mapping, dict) else None
    mapped_objects = input_mapping.get("objects") if isinstance(input_mapping, dict) else None
    configured_bbox = config.get("bbox")
    configured_objects = config.get("objects")
    if all(value in (None, "", []) for value in (configured_bbox, mapped_bbox, configured_objects, mapped_objects)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"图片裁剪节点“{node.name}”必须配置 bbox 或目标列表，"
                "例如引用上游训练模型的 {{ nodes.model_1.objects }}。"
            ),
        )

    for value, capability in (
        (mapped_bbox, "supports_bbox"),
        (configured_bbox, "supports_bbox"),
        (mapped_objects, "supports_objects"),
        (configured_objects, "supports_objects"),
    ):
        source_match = (
            _DYNAMIC_BBOX_REFERENCE.fullmatch(value)
            if capability == "supports_bbox" and isinstance(value, str)
            else _DYNAMIC_OBJECTS_REFERENCE.fullmatch(value)
            if isinstance(value, str)
            else None
        )
        source_key = source_match.group(1) if source_match else None
        if not source_key:
            continue
        source_node = next(
            (item for item in scenario_version.nodes if item.enabled and item.node_key == source_key),
            None,
        )
        if source_node is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"图片裁剪节点“{node.name}”引用了不存在的上游节点“{source_key}”。"
                ),
            )
        if source_node.node_type.upper() != "VISION_MODEL":
            # Web/API nodes may legitimately declare their own bbox contract.
            continue
        raw_version_id = (source_node.config_json or {}).get("model_version_id")
        try:
            model_version_id = int(raw_version_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"上游模型节点“{source_node.name}”尚未选择已发布模型版本。",
            ) from exc
        model_version = database.scalar(
            select(VisionModelVersion)
            .options(selectinload(VisionModelVersion.vision_model))
            .where(VisionModelVersion.id == model_version_id)
        )
        model = model_version.vision_model if model_version is not None else None
        if model is None:
            continue
        profile = task_output_profile(model.task_type)
        if not profile[capability]:
            noun = "bbox" if capability == "supports_bbox" else "目标列表"
            missing_output = "定位框" if capability == "supports_bbox" else "目标列表"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"图片裁剪节点“{node.name}”不能引用“{source_node.name}”的{noun}："
                    f"{profile['display_name']}不输出{missing_output}。"
                    "请改用目标检测/分割模型，或填写固定 bbox。"
                ),
            )


def _validate_image_annotate_node(
    database: Session,
    scenario_version: InspectionScenarioVersion,
    node: ScenarioNode,
) -> None:
    """Reject impossible geometry mappings before a workflow is published."""

    config = node.config_json or {}
    input_mapping = config.get("input_mapping") or config.get("context") or {}
    mapped_objects = input_mapping.get("objects") if isinstance(input_mapping, dict) else None
    mapped_bbox = input_mapping.get("bbox") if isinstance(input_mapping, dict) else None
    configured_objects = config.get("objects")
    configured_bbox = config.get("bbox")
    if all(value in (None, "", []) for value in (mapped_objects, mapped_bbox, configured_objects, configured_bbox)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"图片画框节点“{node.name}”必须配置目标列表或 bbox，"
                "例如引用上游训练模型的 {{ nodes.model_1.objects }}。"
            ),
        )

    source_values = (
        (mapped_objects, "objects"),
        (configured_objects, "objects"),
        (mapped_bbox, "bbox"),
        (configured_bbox, "bbox"),
    )
    for value, expected_capability in source_values:
        if not isinstance(value, str):
            continue
        object_match = _DYNAMIC_OBJECTS_REFERENCE.fullmatch(value)
        bbox_match = _DYNAMIC_BBOX_REFERENCE.fullmatch(value)
        source_key = (object_match or bbox_match).group(1) if (object_match or bbox_match) else None
        if not source_key:
            continue
        source_node = next(
            (item for item in scenario_version.nodes if item.enabled and item.node_key == source_key),
            None,
        )
        if source_node is None:
            raise HTTPException(
                status_code=400,
                detail=f"图片画框节点“{node.name}”引用了不存在的上游节点“{source_key}”。",
            )
        if source_node.node_type.upper() != "VISION_MODEL":
            continue
        raw_version_id = (source_node.config_json or {}).get("model_version_id")
        try:
            model_version_id = int(raw_version_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"上游模型节点“{source_node.name}”尚未选择已发布模型版本。",
            ) from exc
        model_version = database.scalar(
            select(VisionModelVersion)
            .options(selectinload(VisionModelVersion.vision_model))
            .where(VisionModelVersion.id == model_version_id)
        )
        model = model_version.vision_model if model_version is not None else None
        if model is None:
            continue
        profile = task_output_profile(model.task_type)
        capability = "supports_objects" if expected_capability == "objects" else "supports_bbox"
        if not profile[capability]:
            noun = "目标列表" if expected_capability == "objects" else "定位框"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"图片画框节点“{node.name}”不能引用“{source_node.name}”的{noun}："
                    f"{profile['display_name']}不输出该信息。"
                    "请改用目标检测/分割模型，或填写固定 bbox。"
                ),
            )

def _validate_vlm_node(database: Session, node: ScenarioNode) -> None:
    config = node.config_json or {}
    referenced_version_id = config.get("referenced_scenario_version_id")
    if referenced_version_id:
        try:
            referenced_id = int(referenced_version_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"VLM 节点“{node.name}”引用的场景版本格式不正确。",
            ) from exc
        referenced = database.scalar(
            select(InspectionScenarioVersion)
            .options(selectinload(InspectionScenarioVersion.scenario))
            .where(InspectionScenarioVersion.id == referenced_id)
        )
        if (
            referenced is None
            or referenced.is_deleted
            or referenced.status != "PUBLISHED"
            or is_collection_only(referenced)
            or referenced.scenario.mode != "VLM_DIRECT"
            or not referenced.primary_vlm_model_id
            or not referenced.prompt_template
        ):
            raise HTTPException(
                status_code=400,
                detail=f"VLM 节点“{node.name}”必须引用一个已发布且配置完整的 VLM 场景。",
            )
        return
    if not config.get("vlm_model_id"):
        raise HTTPException(status_code=400, detail=f"VLM 节点“{node.name}”必须选择 VLM 模型。")
    if not config.get("prompt"):
        raise HTTPException(status_code=400, detail=f"VLM 节点“{node.name}”必须填写检测提示词。")
    try:
        _validate_vlm(database, int(config["vlm_model_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"VLM 节点“{node.name}”的模型编号格式不正确。",
        ) from exc


def _validate_workflow_graph(version: InspectionScenarioVersion) -> None:
    nodes = [node for node in version.nodes if node.enabled]
    starts = [node for node in nodes if node.node_type.upper() == "START"]
    ends = [node for node in nodes if node.node_type.upper() == "END"]
    if len(starts) != 1 or len(ends) != 1:
        raise HTTPException(status_code=400, detail="工作流必须且只能包含一个开始和一个结束节点。")
    known_keys = {node.node_key for node in nodes}
    adjacency: dict[str, set[str]] = {key: set() for key in known_keys}
    reverse: dict[str, set[str]] = {key: set() for key in known_keys}
    indegree: dict[str, int] = {key: 0 for key in known_keys}
    for edge in version.edges:
        if edge.source_node_key not in known_keys or edge.target_node_key not in known_keys:
            continue
        adjacency[edge.source_node_key].add(edge.target_node_key)
        reverse[edge.target_node_key].add(edge.source_node_key)
        indegree[edge.target_node_key] += 1
    start_key = starts[0].node_key
    end_key = ends[0].node_key
    reachable: set[str] = set()
    stack = [start_key]
    while stack:
        key = stack.pop()
        if key in reachable:
            continue
        reachable.add(key)
        stack.extend(adjacency[key])
    if end_key not in reachable:
        raise HTTPException(status_code=400, detail="工作流没有从开始节点到结束节点的有效连线。")
    reaches_end: set[str] = set()
    stack = [end_key]
    while stack:
        key = stack.pop()
        if key in reaches_end:
            continue
        reaches_end.add(key)
        stack.extend(reverse[key])
    inactive = [node.name for node in nodes if node.node_key not in reachable or node.node_key not in reaches_end]
    if inactive:
        raise HTTPException(
            status_code=400,
            detail=f"以下节点未接入完整的开始到结束路径：{'、'.join(inactive)}。",
        )
    ready = [key for key, value in indegree.items() if value == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for target in adjacency[key]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(known_keys):
        raise HTTPException(status_code=400, detail="工作流主画布不能存在闭环；请使用遍历处理或条件循环节点。")


def _workflow_upstream_keys(
    version: InspectionScenarioVersion,
    node_key: str,
) -> set[str]:
    """Return every executable node that can provide data to ``node_key``."""

    reverse: dict[str, set[str]] = {}
    for edge in version.edges:
        reverse.setdefault(edge.target_node_key, set()).add(edge.source_node_key)
    upstream: set[str] = set()
    pending = list(reverse.get(node_key, set()))
    while pending:
        current = pending.pop()
        if current in upstream:
            continue
        upstream.add(current)
        pending.extend(reverse.get(current, set()))
    return upstream


def _configured_node_references(value: Any) -> set[str]:
    """Extract ``{{ nodes.<key>... }}`` references from an arbitrary config."""

    if isinstance(value, str):
        return set(_DYNAMIC_NODE_REFERENCE.findall(value))
    if isinstance(value, dict):
        references: set[str] = set()
        for nested in value.values():
            references.update(_configured_node_references(nested))
        return references
    if isinstance(value, (list, tuple)):
        references = set()
        for nested in value:
            references.update(_configured_node_references(nested))
        return references
    return set()


def _vision_model_for_node(database: Session, node: ScenarioNode) -> VisionModel | None:
    try:
        version_id = int((node.config_json or {}).get("model_version_id"))
    except (TypeError, ValueError):
        return None
    version = database.get(VisionModelVersion, version_id)
    return version.vision_model if version is not None else None


def _vision_output_field_contract(database: Session, node: ScenarioNode, field: str) -> tuple[bool, str | None]:
    model = _vision_model_for_node(database, node)
    if model is None:
        return True, None
    profile = task_output_profile(model.task_type)
    mapping = (node.config_json or {}).get("output_mapping")
    aliases = mapping if isinstance(mapping, dict) else {}
    if field in aliases:
        expression = aliases[field]
        response_reference = (
            _DYNAMIC_RESPONSE_OUTPUT_REFERENCE.fullmatch(expression)
            if isinstance(expression, str) else None
        )
        if response_reference is None:
            return True, None
        field = response_reference.group(1)
    normalized = re.sub(r"(?<=\.)\d+(?=\.|$)", "0", field)
    available = set(profile["output_keys"]) | {
        "result", "reason", "status", "model_version_id", "model_code", "parameters", "output_profile",
    }
    valid = (
        normalized in available
        or normalized.split(".", 1)[0] in {"parameters", "output_profile"}
        or profile["supports_classification"] and re.fullmatch(r"classification\.top5\.0\.(label|class_id|confidence)", normalized)
        or profile["supports_bbox"] and re.fullmatch(r"objects\.0\.bbox\.0", normalized)
    )
    if not valid:
        return False, None
    numeric_fields = {
        "confidence", "object_count", "detection_count", "width", "height",
        "class_id", "top1_class_id", "top1_confidence",
    }
    text_fields = {
        "predicted_label", "label", "top1_label", "reason", "result", "status",
        "model_code", "task_type", "bbox_format", "mask_format",
    }
    last_field = normalized.rsplit(".", 1)[-1]
    if last_field in numeric_fields:
        return True, "NUMBER"
    return True, "TEXT" if last_field in text_fields else None


def _is_numeric_text(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _validate_loop_body(database: Session, loop_node: ScenarioNode, add: Any) -> None:
    config = loop_node.config_json or {}
    body = config.get("body_nodes")
    if not isinstance(body, list) or not 1 <= len(body) <= 20:
        add("ERROR", "LOOP_BODY_REQUIRED", f"循环节点“{loop_node.name}”需要添加 1 到 20 个循环体节点。", node=loop_node)
        return
    allowed = {"VLM", "VISION_MODEL", "OCR", "IMAGE_CROP", "IMAGE_ANNOTATE", "RULE", "IF", "WEB_API", "S3_UPLOAD"}
    previous_keys = {"loop_start"}
    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(body, start=1):
        if not isinstance(entry, dict):
            add("ERROR", "LOOP_BODY_INVALID", f"循环体第 {index} 个节点格式不正确。", node=loop_node)
            continue
        key = str(entry.get("node_key") or "")
        node_type = str(entry.get("node_type") or "")
        node_config = entry.get("config_json")
        if node_type not in allowed or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in previous_keys or key == "loop_end" or not isinstance(node_config, dict):
            add("ERROR", "LOOP_BODY_INVALID", f"循环体第 {index} 个节点类型、键或配置无效。", node=loop_node)
            continue
        child = ScenarioNode(node_key=key, name=str(entry.get("name") or key), node_type=node_type, config_json=node_config)
        for source in _configured_node_references(node_config):
            if source not in previous_keys:
                add("ERROR", "LOOP_BODY_REFERENCE_INVALID", f"循环体节点“{child.name}”引用了非上游节点“{source}”。", node=loop_node)
        try:
            if node_type == "VLM":
                _validate_vlm_node(database, child)
            elif node_type == "VISION_MODEL":
                _validate_published_vision_model_node(database, child)
            elif node_type == "OCR":
                _validate_ocr_node(child)
            elif node_type == "IMAGE_CROP" and not (node_config.get("bbox") or node_config.get("objects") or (node_config.get("input_mapping") or {}).get("bbox") or (node_config.get("input_mapping") or {}).get("objects")):
                raise HTTPException(status_code=400, detail="图片裁剪节点需要 bbox 或目标列表。")
            elif node_type == "IMAGE_ANNOTATE" and not (node_config.get("bbox") or node_config.get("objects") or (node_config.get("input_mapping") or {}).get("bbox") or (node_config.get("input_mapping") or {}).get("objects")):
                raise HTTPException(status_code=400, detail="图片画框节点需要 bbox 或目标列表。")
            elif node_type in {"RULE", "IF"} and not node_config.get("actual"):
                raise HTTPException(status_code=400, detail="规则节点需要实际值来源。")
            elif node_type == "WEB_API" and not node_config.get("url"):
                raise HTTPException(status_code=400, detail="Web 接口节点需要地址。")
            elif node_type == "S3_UPLOAD" and not node_config.get("image_path"):
                raise HTTPException(status_code=400, detail="S3 上传节点需要图片路径。")
        except HTTPException as exc:
            add("ERROR", "LOOP_BODY_NODE_INVALID", f"循环体节点“{child.name}”：{exc.detail}", node=loop_node)
        previous_keys.add(key)
        entries.append(entry)
    graph_keys = {entry["node_key"] for entry in entries}
    graph_keys.update({"loop_start", "loop_end"})
    node_types = {entry["node_key"]: entry["node_type"] for entry in entries}
    node_types.update({"loop_start": "START", "loop_end": "END"})
    adjacency: dict[str, set[str]] = {key: set() for key in graph_keys}
    outgoing: dict[str, set[str]] = {key: set() for key in graph_keys}
    incoming: dict[str, int] = {key: 0 for key in graph_keys}
    connections = loop_body_connections(entries, config.get("body_edges"))
    if not connections or len(connections) > 2 * len(entries) + 1:
        add("ERROR", "LOOP_BODY_EDGE_INVALID", "循环体连线数量无效。", node=loop_node)
    for connection in connections:
        if not isinstance(connection, dict):
            add("ERROR", "LOOP_BODY_EDGE_INVALID", "循环体连线格式无效。", node=loop_node)
            continue
        source = str(connection.get("source_node_key") or "")
        target = str(connection.get("target_node_key") or "")
        branch = str(connection.get("branch") or "").lower()
        if source not in graph_keys or target not in graph_keys or source == "loop_end" or target == "loop_start" or source == target:
            add("ERROR", "LOOP_BODY_EDGE_INVALID", f"循环体连线 {source} → {target} 无效。", node=loop_node)
            continue
        if node_types[source] == "IF" and branch not in {"true", "false"}:
            add("ERROR", "LOOP_BODY_EDGE_INVALID", f"条件节点“{source}”的连线须选择成立或不成立。", node=loop_node)
            continue
        if node_types[source] != "IF" and branch:
            add("ERROR", "LOOP_BODY_EDGE_INVALID", f"节点“{source}”不支持分支连线。", node=loop_node)
            continue
        route = branch or "default"
        if route in outgoing[source]:
            add("ERROR", "LOOP_BODY_EDGE_INVALID", f"节点“{source}”的 {route} 连线重复。", node=loop_node)
            continue
        outgoing[source].add(route)
        adjacency[source].add(target)
        incoming[target] += 1
    for key in graph_keys - {"loop_end"}:
        expected = {"true", "false"} if node_types[key] == "IF" else {"default"}
        if outgoing[key] != expected:
            add("ERROR", "LOOP_BODY_DISCONNECTED", f"循环体节点“{key}”缺少输出连线。", node=loop_node)
    for key in graph_keys - {"loop_start"}:
        if incoming[key] == 0:
            add("ERROR", "LOOP_BODY_DISCONNECTED", f"循环体节点“{key}”缺少输入连线。", node=loop_node)
    visited: set[str] = set()
    pending = ["loop_start"]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current] - visited)
    if graph_keys - visited:
        add("ERROR", "LOOP_BODY_DISCONNECTED", f"循环体存在未连接的节点：{'、'.join(sorted(graph_keys - visited))}。", node=loop_node)
    indegree = {key: 0 for key in graph_keys}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    ready = [key for key, degree in indegree.items() if degree == 0]
    ordered = 0
    while ready:
        current = ready.pop()
        ordered += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if ordered != len(graph_keys):
        add("ERROR", "LOOP_BODY_CYCLE", "循环体内部不能再形成闭环，请使用循环控制节点的集合输入。", node=loop_node)
    for source in _configured_node_references(config.get("body_output") or {}):
        if source not in previous_keys:
            add("ERROR", "LOOP_BODY_OUTPUT_INVALID", f"循环体输出引用了不存在的节点“{source}”。", node=loop_node)


def _workflow_preflight(
    database: Session,
    version: InspectionScenarioVersion,
) -> dict[str, Any]:
    """Inspect a draft/published scene without changing it.

    The designer needs a complete, node-oriented answer before an operator
    presses publish.  Publishing reuses this exact inspection so the canvas and
    production rules cannot drift apart.
    """

    diagnostics: list[dict[str, Any]] = []

    def add(
        level: str,
        code: str,
        message: str,
        *,
        node: ScenarioNode | None = None,
        suggestion: str | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "level": level,
            "code": code,
            "message": message,
        }
        if node is not None:
            item.update(
                {
                    "node_id": node.id,
                    "node_key": node.node_key,
                    "node_name": node.name,
                    "node_type": node.node_type,
                }
            )
        if suggestion:
            item["suggestion"] = suggestion
        diagnostics.append(item)

    scene = version.scenario
    if is_collection_only(version):
        add("INFO", "COLLECTION_ONLY", "仅采集图片：无需模型，不执行质量判定，detect 按约定返回 NG。")
        if not any(item.enabled and item.auto_collect_enabled for item in scene_collection_targets(database, version)):
            add("WARNING", "COLLECTION_TARGET_REQUIRED", "尚未关联已启用自动采集的数据集。发布后请到数据集维护关联本场景。")
    elif scene.mode == "VLM_DIRECT":
        if not version.primary_vlm_model_id:
            add("ERROR", "DIRECT_VLM_REQUIRED", "直接 VLM 场景必须配置主 VLM。", suggestion="在“检测配置”中选择可用的 VLM 模型。")
        else:
            try:
                _validate_vlm(database, version.primary_vlm_model_id)
            except HTTPException as exc:
                add("ERROR", "DIRECT_VLM_INVALID", str(exc.detail), suggestion="重新选择一个可用的 VLM 模型。")
        if not str(version.prompt_template or "").strip():
            add("ERROR", "DIRECT_PROMPT_REQUIRED", "直接 VLM 场景必须填写检测提示词。", suggestion="说明检测对象、OK/NG 规则和返回格式。")
        if not any(item["level"] == "ERROR" for item in diagnostics):
            add("INFO", "DIRECT_READY", "主 VLM、提示词和接口参数已具备执行条件。")
    else:
        nodes = [node for node in version.nodes if node.enabled]
        node_by_key = {node.node_key: node for node in nodes}
        node_types = {node.node_type.upper() for node in nodes}
        upload_nodes = [node for node in nodes if node.node_type.upper() == "S3_UPLOAD"]
        if len(upload_nodes) > 1:
            add("ERROR", "S3_UPLOAD_DUPLICATE", "一个场景流程只能有一个 S3 上传节点，避免同次执行覆盖对象键。")
        has_loop_vlm = any(
            node.node_type.upper() == "LOOP" and (node.config_json or {}).get("operation") == "VLM"
            for node in nodes
        )
        has_loop_body_vision = any(
            node.node_type.upper() in {"LOOP", "ITERATION", "CONDITION_LOOP"}
            and (node.node_type.upper() != "LOOP" or (node.config_json or {}).get("operation") == "SUBFLOW")
            and any(
                isinstance(child, dict) and child.get("node_type") in {"VLM", "VISION_MODEL", "OCR"}
                for child in ((node.config_json or {}).get("body_nodes") or [])
            ) for node in nodes
        )
        if node_types.isdisjoint({"VLM", "VISION_MODEL", "OCR"}) and not has_loop_vlm and not has_loop_body_vision:
            add("ERROR", "VISION_NODE_REQUIRED", "工作流至少需要一个 VLM、训练模型或 OCR 节点。", suggestion="从节点库添加并连接识别节点。")
        try:
            _validate_workflow_graph(version)
        except HTTPException as exc:
            add("ERROR", "GRAPH_INVALID", str(exc.detail), suggestion="检查开始、结束节点之间的连线，并移除未接入主路径的节点。")

        validators: dict[str, Any] = {
            "VLM": lambda node: _validate_vlm_node(database, node),
            "VISION_MODEL": lambda node: _validate_published_vision_model_node(database, node),
            "OCR": lambda node: _validate_ocr_node(node),
            "IMAGE_CROP": lambda node: _validate_image_crop_node(database, version, node),
            "IMAGE_ANNOTATE": lambda node: _validate_image_annotate_node(database, version, node),
        }
        for node in nodes:
            node_type = node.node_type.upper()
            validator = validators.get(node_type)
            if validator is not None:
                try:
                    validator(node)
                except HTTPException as exc:
                    add("ERROR", f"{node_type}_INVALID", str(exc.detail), node=node)

            if node_type in {"RULE", "IF"}:
                rule_config = node.config_json or {}
                actual = rule_config.get("actual")
                if actual is None or (isinstance(actual, str) and not actual.strip()):
                    add(
                        "ERROR",
                        f"{node_type}_ACTUAL_REQUIRED",
                        f"节点“{node.name}”尚未选择实际值来源。",
                        node=node,
                        suggestion="从已连接的上游节点选择输出字段，例如分类模型的 predicted_label。",
                    )
                actual_contract: tuple[bool, str | None] | None = None
                actual_source: ScenarioNode | None = None
                expected_contract: tuple[bool, str | None] | None = None
                for field_name in ("actual", "expected"):
                    value = rule_config.get(field_name)
                    reference = _DYNAMIC_NODE_OUTPUT_REFERENCE.fullmatch(value) if isinstance(value, str) else None
                    if reference is None:
                        continue
                    source_key, output_field = reference.groups()
                    source = node_by_key.get(source_key)
                    if source is None or source.node_type.upper() != "VISION_MODEL":
                        continue
                    contract = _vision_output_field_contract(database, source, output_field)
                    if not contract[0]:
                        add(
                            "ERROR", f"{node_type}_OUTPUT_FIELD_INVALID",
                            f"节点“{node.name}”的{'实际值' if field_name == 'actual' else '期望值'}引用了模型“{source.name}”不存在的输出“{output_field}”。",
                            node=node,
                            suggestion="从该模型节点的可用输出重新选择字段；分类模型可选择 predicted_label 或 classification.top1_label。",
                        )
                    if field_name == "actual":
                        actual_contract = contract
                        actual_source = source
                    else:
                        expected_contract = contract
                expected = rule_config.get("expected")
                expected_type: str | None = expected_contract[1] if expected_contract else None
                if isinstance(expected, str):
                    input_reference = re.fullmatch(r"\s*\{\{\s*input\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*", expected)
                    if input_reference is not None:
                        input_field = next(
                            (field for field in _scene_input_fields(version) if field["name"] == input_reference.group(1)),
                            None,
                        )
                        if input_field is not None:
                            expected_type = "NUMBER" if input_field["type"] in {"NUMBER", "INTEGER", "FLOAT"} else "TEXT" if input_field["type"] in {"TEXT", "STRING"} else None
                if (
                    actual_contract is not None and actual_contract == (True, "NUMBER")
                    and actual_source is not None and str(rule_config.get("operator") or "EQUALS").upper() == "EQUALS"
                    and isinstance(expected, str) and expected.strip()
                ):
                    if expected_type == "TEXT" or ("{{" not in expected and not _is_numeric_text(expected)):
                        add(
                            "ERROR", f"{node_type}_VALUE_TYPE_MISMATCH",
                            f"节点“{node.name}”的实际值来自“{actual_source.name}”的数字输出，却要与文字类型的期望值“{expected}”比较。",
                            node=node,
                            suggestion="若要比较类别，请改选分类模型的 predicted_label / classification.top1_label；若要比较数量，请填写数字并选择“数值等于”。",
                        )
                    elif "{{" not in expected and _is_numeric_text(expected):
                        add(
                            "ERROR", f"{node_type}_NUMBER_OPERATOR_REQUIRED",
                            f"节点“{node.name}”的实际值是数字，但期望值“{expected}”以文字保存；直接使用“等于”会始终不相等。",
                            node=node,
                            suggestion="将运算符改为“数值等于”，或改用文字类型的模型输出。",
                        )

            if node_type == "WEB_API":
                url = str((node.config_json or {}).get("url") or "").strip()
                if not url:
                    add("ERROR", "WEB_API_URL_REQUIRED", f"Web 接口节点“{node.name}”尚未填写接口地址。", node=node)
            if node_type == "S3_UPLOAD":
                image_path = str((node.config_json or {}).get("image_path") or "").strip()
                if not image_path:
                    add("ERROR", "S3_UPLOAD_IMAGE_REQUIRED", f"S3 上传节点“{node.name}”尚未指定图片来源。", node=node)
                try:
                    SceneS3Storage().validate_request(None)
                except SceneS3StorageError as exc:
                    add("ERROR", "S3_UPLOAD_CONFIG_INVALID", str(exc), node=node)
            if node_type in {"LOOP", "ITERATION", "CONDITION_LOOP"}:
                config = node.config_json or {}
                mapping = config.get("input_mapping") or config.get("context") or {}
                condition_loop = node_type == "CONDITION_LOOP"
                if not condition_loop and not config.get("items") and not (isinstance(mapping, dict) and mapping.get("items")):
                    add("ERROR", "LOOP_ITEMS_REQUIRED", f"遍历节点“{node.name}”尚未指定输入数组。", node=node)
                operation = "SUBFLOW" if node_type == "ITERATION" else str(config.get("operation") or "PASS_THROUGH").upper()
                if node_type == "LOOP" and operation not in {"PASS_THROUGH", "VLM", "SUBFLOW"}:
                    add("ERROR", "LOOP_OPERATION_INVALID", f"旧版循环节点“{node.name}”的执行方式不受支持。", node=node)
                limit = 20 if condition_loop else 30 if operation == "VLM" else 100 if operation == "SUBFLOW" else 1000
                try:
                    max_iterations = int(config.get("max_iterations", 10))
                    if not 1 <= max_iterations <= limit:
                        raise ValueError
                except (TypeError, ValueError):
                    add("ERROR", "LOOP_LIMIT_INVALID", f"循环节点“{node.name}”的最大次数必须在 1 到 {limit} 之间。", node=node)
                if not condition_loop and operation in {"VLM", "SUBFLOW"}:
                    item_name = str(config.get("item_name") or "item").strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item_name) or item_name in {"input", "nodes", "params", "traces", "parent", "state"}:
                        add("ERROR", "LOOP_ITEM_NAME_INVALID", f"循环节点“{node.name}”的当前项变量名不合法。", node=node)
                if condition_loop:
                    try:
                        timeout_seconds = float(config.get("timeout_seconds", 30))
                        if not 0 < timeout_seconds <= 300:
                            raise ValueError
                    except (TypeError, ValueError):
                        add("ERROR", "LOOP_TIMEOUT_INVALID", f"条件循环“{node.name}”的超时必须在 0 到 300 秒之间。", node=node)
                    if not isinstance(config.get("initial_state"), dict) or not isinstance(config.get("state_updates"), dict):
                        add("ERROR", "LOOP_STATE_INVALID", f"条件循环“{node.name}”需要键值形式的初始变量和变量更新。", node=node)
                    else:
                        for key, update in config["state_updates"].items():
                            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                                add("ERROR", "LOOP_STATE_INVALID", f"条件循环“{node.name}”的变量名“{key}”不合法。", node=node)
                            if isinstance(update, dict) and "operation" in update and update["operation"] not in {"INCREMENT", "APPEND"}:
                                add("ERROR", "LOOP_STATE_INVALID", f"条件循环“{node.name}”的变量“{key}”更新方式不受支持。", node=node)
                    stop = config.get("stop_condition") or {}
                    if not isinstance(stop, dict) or not stop.get("actual"):
                        add("ERROR", "LOOP_STOP_REQUIRED", f"条件循环“{node.name}”需要退出条件。", node=node)
                    elif str(stop.get("operator") or "EQUALS").upper() not in {"EQUALS", "CONTAINS", "EXISTS", "NUMBER_EQUALS", "NUMBER_GT", "NUMBER_GTE", "NUMBER_LT", "NUMBER_LTE"}:
                        add("ERROR", "LOOP_STOP_INVALID", f"条件循环“{node.name}”的退出比较方式不受支持。", node=node)
                    for source in _configured_node_references({"updates": config.get("state_updates"), "stop": stop}):
                        if source not in {entry.get("node_key") for entry in config.get("body_nodes") or [] if isinstance(entry, dict)}:
                            add("ERROR", "LOOP_STATE_REFERENCE_INVALID", f"条件循环“{node.name}”引用了不存在的内部节点“{source}”。", node=node)
                if operation == "SUBFLOW" or condition_loop:
                    _validate_loop_body(database, node, add)
                    if not condition_loop and str(config.get("aggregate") or "ALL_OK").upper() not in {"ALL_OK", "ANY_OK", "COLLECT_ONLY"}:
                        add("ERROR", "LOOP_AGGREGATE_INVALID", f"循环节点“{node.name}”的汇总方式不受支持。", node=node)
                if node_type == "LOOP" and operation == "VLM":
                    items_reference = (
                        mapping.get("items")
                        if isinstance(mapping, dict) and mapping.get("items")
                        else config.get("items")
                    )
                    direct_objects = (
                        _DYNAMIC_OBJECTS_REFERENCE.fullmatch(items_reference)
                        if isinstance(items_reference, str)
                        else None
                    )
                    source_node = node_by_key.get(direct_objects.group(1)) if direct_objects else None
                    if source_node is not None and source_node.node_type.upper() == "VISION_MODEL":
                        add(
                            "ERROR", "LOOP_ITEMS_NEED_IMAGE",
                            f"逐项 VLM 节点“{node.name}”需要含 image_path 的裁剪图列表，不能直接使用模型 objects。",
                            node=node,
                            suggestion="先增加图片裁剪节点并选择“目标列表”，再引用其 crops 输出。",
                        )
                    if not config.get("prompt"):
                        add("ERROR", "LOOP_PROMPT_REQUIRED", f"逐项 VLM 节点“{node.name}”需要检测提示词。", node=node)
                    try:
                        model_id = int(config.get("vlm_model_id"))
                        _validate_vlm(database, model_id)
                        if not database.get(VlmModelConfig, model_id).enabled:
                            raise ValueError
                    except (TypeError, ValueError, HTTPException):
                        add("ERROR", "LOOP_VLM_INVALID", f"逐项 VLM 节点“{node.name}”需要选择可用的 VLM 模型。", node=node)
                    try:
                        parallelism = int(config.get("max_parallelism", 3))
                        if not 1 <= parallelism <= 3:
                            raise ValueError
                    except (TypeError, ValueError):
                        add("ERROR", "LOOP_PARALLELISM_INVALID", f"逐项 VLM 节点“{node.name}”的并发数必须在 1 到 3 之间。", node=node)
                    if str(config.get("aggregate") or "ALL_OK").upper() not in {"ALL_OK", "ANY_OK", "COLLECT_ONLY"}:
                        add("ERROR", "LOOP_AGGREGATE_INVALID", f"逐项 VLM 节点“{node.name}”的汇总方式不受支持。", node=node)
                elif node_type == "LOOP" and operation == "PASS_THROUGH":
                    add("WARNING", "LOOP_PASS_THROUGH", f"循环节点“{node.name}”目前只整理集合，不会逐项执行处理节点。", node=node, suggestion="如需逐项处理，请切换为“自定义循环体”并添加节点。")

            if node_type != "START":
                upstream = _workflow_upstream_keys(version, node.node_key)
                reference_config = dict(node.config_json or {})
                if node_type in {"ITERATION", "CONDITION_LOOP"} or (node_type == "LOOP" and reference_config.get("operation") == "SUBFLOW"):
                    reference_config.pop("body_nodes", None)
                    reference_config.pop("body_output", None)
                if node_type == "CONDITION_LOOP":
                    reference_config.pop("state_updates", None)
                    reference_config.pop("stop_condition", None)
                for source_key in sorted(_configured_node_references(reference_config)):
                    source = node_by_key.get(source_key)
                    if source is None:
                        add(
                            "ERROR",
                            "VARIABLE_SOURCE_MISSING",
                            f"节点“{node.name}”引用了不存在的变量来源“{source_key}”。",
                            node=node,
                            suggestion="使用变量选择器重新选择开始输入或已连通的上游节点输出。",
                        )
                    elif source_key not in upstream:
                        add(
                            "ERROR",
                            "VARIABLE_SOURCE_NOT_UPSTREAM",
                            f"节点“{node.name}”引用了“{source.name}”的输出，但两者没有上游数据路径。",
                            node=node,
                            suggestion="先连接数据来源节点，或改为引用当前节点真正上游的变量。",
                        )

            if node_type == "VISION_MODEL":
                model = _vision_model_for_node(database, node)
                if model is not None and task_output_profile(model.task_type)["supports_classification"]:
                    has_downstream_rule = any(
                        candidate.node_type.upper() in {"RULE", "IF"}
                        and node.node_key in _workflow_upstream_keys(version, candidate.node_key)
                        for candidate in nodes
                    )
                    if not has_downstream_rule:
                        add(
                            "WARNING",
                            "CLASSIFICATION_RULE_MISSING",
                            f"分类节点“{node.name}”只输出预测类别，下游没有规则判断节点。",
                            node=node,
                            suggestion="将 predicted_label 与开始节点传入的期望类别交给规则节点比较，再由结束节点返回规则结果。",
                        )

        end = next((node for node in nodes if node.node_type.upper() == "END"), None)
        if end is not None and not (end.config_json or {}).get("output"):
            add(
                "INFO",
                "END_DEFAULT_OUTPUT",
                "结束节点未声明自定义返回字段，将返回最后一个执行节点的完整结果。",
                node=end,
            )
        if end is not None:
            for field_name, value in ((end.config_json or {}).get("output") or {}).items():
                if not isinstance(value, str):
                    continue
                reference = _DYNAMIC_NODE_OUTPUT_REFERENCE.fullmatch(value)
                if reference is None:
                    continue
                source_key, output_field = reference.groups()
                source = node_by_key.get(source_key)
                if source is None or source.node_type.upper() != "VISION_MODEL":
                    continue
                if not _vision_output_field_contract(database, source, output_field)[0]:
                    add(
                        "ERROR",
                        "END_OUTPUT_FIELD_INVALID",
                        f"结束节点返回字段“{field_name}”引用了模型节点“{source.name}”不存在的输出“{output_field}”。",
                        node=end,
                        suggestion="分类模型可引用 classification.top1_label、predicted_label 或整个模型节点。",
                    )

        if not any(item["level"] == "ERROR" for item in diagnostics):
            add("INFO", "WORKFLOW_READY", "流程图、节点配置与变量数据路径检查通过，可测试或发布。")

    errors = sum(item["level"] == "ERROR" for item in diagnostics)
    warnings = sum(item["level"] == "WARNING" for item in diagnostics)
    return {
        "version_id": version.id,
        "scene_id": version.scenario_id,
        "mode": scene.mode,
        "ready": errors == 0,
        "affected_roi_count": database.query(RoiScenarioBinding).join(InspectionScenarioVersion).filter(
            InspectionScenarioVersion.scenario_id == version.scenario_id,
            RoiScenarioBinding.enabled.is_(True),
        ).count(),
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "infos": sum(item["level"] == "INFO" for item in diagnostics),
        },
        "diagnostics": diagnostics,
    }


def _validate_preview_rule_contract(database: Session, version: InspectionScenarioVersion) -> None:
    if is_collection_only(version) or version.scenario.mode != "WORKFLOW":
        return
    rule_errors = {
        "RULE_ACTUAL_REQUIRED", "IF_ACTUAL_REQUIRED",
        "RULE_OUTPUT_FIELD_INVALID", "IF_OUTPUT_FIELD_INVALID",
        "RULE_VALUE_TYPE_MISMATCH", "IF_VALUE_TYPE_MISMATCH",
        "RULE_NUMBER_OPERATOR_REQUIRED", "IF_NUMBER_OPERATOR_REQUIRED",
    }
    issue = next(
        (item for item in _workflow_preflight(database, version)["diagnostics"] if item["code"] in rule_errors),
        None,
    )
    if issue is not None:
        raise HTTPException(status_code=422, detail=f"{issue['message']} {issue.get('suggestion', '')}".strip())


def _version_query(version_id: int):
    return (
        select(InspectionScenarioVersion)
        .options(
            selectinload(InspectionScenarioVersion.scenario),
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
        )
        .where(InspectionScenarioVersion.id == version_id)
    )


def _version_or_404(database: Session, version_id: int) -> InspectionScenarioVersion:
    version = database.scalar(_version_query(version_id))
    if version is None or version.is_deleted:
        raise HTTPException(status_code=404, detail="场景版本不存在。")
    return version


def _ensure_draft(version: InspectionScenarioVersion) -> None:
    if version.status != "DRAFT":
        raise HTTPException(status_code=409, detail="已发布或归档的场景版本不能直接修改，请先新建草稿版本。")


def _create_workflow_skeleton(database: Session, version: InspectionScenarioVersion) -> None:
    """Create the start/end frame used by every new workflow."""

    start = ScenarioNode(
        scenario_version=version,
        node_key="start",
        name="开始",
        node_type="START",
        config_json={
            "inputs": [
                {
                    "name": "image_path",
                    "label": "检测图片",
                    "type": "IMAGE",
                    "required": True,
                }
            ]
        },
        sort_order=10,
        canvas_x=80,
        canvas_y=120,
    )
    end = ScenarioNode(
        scenario_version=version,
        node_key="end",
        name="结束",
        node_type="END",
        sort_order=20,
        canvas_x=700,
        canvas_y=120,
    )
    database.add_all((start, end))
    database.flush()
    database.add(
        ScenarioEdge(
            scenario_version=version,
            source_node_key=start.node_key,
            target_node_key=end.node_key,
        )
    )


def _clone_workflow_definition(
    database: Session,
    source: InspectionScenarioVersion,
    target: InspectionScenarioVersion,
) -> None:
    for node in source.nodes:
        database.add(
            ScenarioNode(
                scenario_version=target,
                node_key=node.node_key,
                name=node.name,
                node_type=node.node_type,
                config_json=dict(node.config_json or {}),
                sort_order=node.sort_order,
                canvas_x=node.canvas_x,
                canvas_y=node.canvas_y,
                enabled=node.enabled,
            )
        )
    database.flush()
    for edge in source.edges:
        database.add(
            ScenarioEdge(
                scenario_version=target,
                source_node_key=edge.source_node_key,
                target_node_key=edge.target_node_key,
                mapping_json=dict(edge.mapping_json or {}),
            )
        )


def _insert_workflow_node(
    database: Session,
    version: InspectionScenarioVersion,
    payload: NodeCreate,
) -> ScenarioNode:
    """Create a node for either the legacy linear API or the visual canvas.

    Existing API clients did not send graph edges and relied on automatic
    insertion.  The canvas sends ``auto_connect=false`` and owns every line.
    Keeping both modes avoids breaking already-created automation flows.
    """

    end_node = next(
        (node for node in version.nodes if node.enabled and node.node_type.upper() == "END"),
        None,
    )
    if payload.auto_connect and end_node is not None:
        insertion_order = end_node.sort_order
        end_node.sort_order += 10
    else:
        insertion_order = max((node.sort_order for node in version.nodes), default=0) + 10
    if not payload.auto_connect:
        user_nodes = [
            node
            for node in version.nodes
            if node.enabled and node.node_type.upper() not in {"START", "END"}
        ]
        if not user_nodes:
            for edge in list(version.edges):
                if edge.source_node_key == "start" and edge.target_node_key == "end":
                    version.edges.remove(edge)
                    database.delete(edge)
    node = ScenarioNode(
        scenario_version=version,
        node_key=payload.node_key,
        name=payload.name,
        node_type=payload.node_type,
        config_json=payload.config_json,
        sort_order=insertion_order,
        canvas_x=payload.canvas_x,
        canvas_y=payload.canvas_y,
    )
    database.add(node)
    database.flush()
    if payload.auto_connect and end_node is not None:
        predecessors = [
            item
            for item in version.nodes
            if item.enabled and item.id != end_node.id and item.id != node.id and item.node_type.upper() != "END"
        ]
        predecessor = max(predecessors, key=lambda item: (item.sort_order, item.id), default=None)
        if predecessor is not None:
            for edge in list(version.edges):
                if edge.source_node_key == predecessor.node_key and edge.target_node_key == end_node.node_key:
                    version.edges.remove(edge)
                    database.delete(edge)
            database.flush()
            database.add_all((
                ScenarioEdge(scenario_version=version, source_node_key=predecessor.node_key, target_node_key=node.node_key),
                ScenarioEdge(scenario_version=version, source_node_key=node.node_key, target_node_key=end_node.node_key),
            ))
    return node


def _would_create_cycle(
    version: InspectionScenarioVersion,
    source_node_key: str,
    target_node_key: str,
) -> bool:
    """Return true when adding ``source -> target`` makes the visible graph cyclic."""

    adjacency: dict[str, set[str]] = {}
    for edge in version.edges:
        adjacency.setdefault(edge.source_node_key, set()).add(edge.target_node_key)
    adjacency.setdefault(source_node_key, set()).add(target_node_key)
    stack = [target_node_key]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current == source_node_key:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, set()))
    return False


def _node_or_404(
    version: InspectionScenarioVersion,
    node_id: int,
) -> ScenarioNode:
    node = next((item for item in version.nodes if item.id == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="流程节点不存在。")
    return node


def _node_payload(node: ScenarioNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "node_key": node.node_key,
        "name": node.name,
        "node_type": node.node_type,
        "config_json": node.config_json,
        "sort_order": node.sort_order,
        "canvas_x": node.canvas_x,
        "canvas_y": node.canvas_y,
        "enabled": node.enabled,
    }


def _version_payload(version: InspectionScenarioVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "scenario_id": version.scenario_id,
        "version": version.version,
        "status": version.status,
        "prompt_template": version.prompt_template,
        "input_schema_json": version.input_schema_json,
        "output_schema_json": version.output_schema_json,
        "definition_json": version.definition_json,
        "run_mode": "COLLECT_ONLY" if is_collection_only(version) else "DETECT",
        "primary_vlm_model_id": version.primary_vlm_model_id,
        "review_vlm_model_id": version.review_vlm_model_id,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "updated_at": version.updated_at.isoformat() if version.updated_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "nodes": [_node_payload(node) for node in sorted(version.nodes, key=lambda item: (item.sort_order, item.id))],
        "edges": [
            {
                "id": edge.id,
                "source_node_key": edge.source_node_key,
                "target_node_key": edge.target_node_key,
                "mapping_json": edge.mapping_json,
            }
            for edge in version.edges
        ],
    }


def _scenario_payload(scene: InspectionScenario, *, include_collection: bool = True) -> dict[str, Any]:
    versions = sorted(scene.versions, key=lambda item: item.id, reverse=True)
    database = object_session(scene)
    return {
        "id": scene.id,
        "code": scene.code,
        "name": scene.name,
        "category": scene.category,
        "mode": scene.mode,
        "description": scene.description,
        "published_version_id": scene.published_version_id,
        "enabled": scene.enabled,
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
        "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
        "versions": [_version_payload(version) for version in versions],
        "collection_datasets": [
            {"id": dataset.id, "name": dataset.name,
             "enabled": bool(dataset.enabled and dataset.auto_collect_enabled),
             "count": database.scalar(select(func.count(DatasetItem.id)).where(
                 DatasetItem.dataset_id == dataset.id, DatasetItem.source == "AUTO_ROI",
                 DatasetItem.is_deleted.is_(False),
             )) or 0,
             "limit": dataset.auto_collect_limit}
            for dataset in scene_collection_targets(database, versions[0])
        ] if include_collection and versions and database is not None else [],
    }


def _execution_payload(execution: ScenarioExecution) -> dict[str, Any]:
    review = execution.review
    return {
        "id": execution.id,
        "scenario_version_id": execution.scenario_version_id,
        "detection_task_id": execution.detection_task_id,
        "roi_id": execution.roi_id,
        "dataset_item_id": execution.dataset_item_id,
        "source": execution.source,
        "status": execution.status,
        "result": execution.result,
        "score": execution.score,
        "input_json": execution.input_json,
        "output_json": with_node_trace_states(execution.output_json),
        "elapsed_ms": execution.elapsed_ms,
        "error_message": execution.error_message,
        "created_at": execution.created_at.isoformat(),
        "review": {
            "id": review.id,
            "status": review.status,
            "verdict": review.verdict,
            "confidence": review.confidence,
            "reason": review.reason,
            "manual_verdict": review.manual_verdict,
            "manual_note": review.manual_note,
            "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None,
            "manually_reviewed_at": (
                review.manually_reviewed_at.isoformat()
                if review.manually_reviewed_at
                else None
            ),
        }
        if review
        else None,
    }


def _scene_api_endpoint_path(scene_code: str) -> str:
    return f"/api/v1/scenarios/invoke/{scene_code}"


def _published_scene_for_invoke(
    database: Session,
    scene_code: str,
) -> tuple[InspectionScenario, InspectionScenarioVersion]:
    normalized_code = str(scene_code or "").strip()
    scene = database.scalar(
        select(InspectionScenario).where(
            InspectionScenario.code == normalized_code,
            InspectionScenario.is_deleted.is_(False),
        )
    )
    if scene is None:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    if not scene.enabled:
        raise HTTPException(status_code=409, detail="检测场景已停用，不能通过外部接口调用。")
    if not scene.published_version_id:
        raise HTTPException(status_code=409, detail="检测场景尚未发布，不能通过外部接口调用。")

    version = database.scalar(
        select(InspectionScenarioVersion)
        .options(
            selectinload(InspectionScenarioVersion.nodes),
            selectinload(InspectionScenarioVersion.edges),
            selectinload(InspectionScenarioVersion.scenario),
        )
        .where(InspectionScenarioVersion.id == scene.published_version_id)
    )
    if (
        version is None
        or version.scenario_id != scene.id
        or version.status != "PUBLISHED"
    ):
        raise HTTPException(status_code=409, detail="检测场景没有可用的已发布版本。")
    return scene, version


def _validate_scene_api_key(api_key: str | None) -> None:
    configured_key = settings.scene_api_key.strip()
    if not configured_key:
        return
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 X-Scene-API-Key 请求头。")
    if not compare_digest(configured_key, api_key):
        raise HTTPException(status_code=401, detail="X-Scene-API-Key 无效。")


def _validate_published_scene_inputs(
    version: InspectionScenarioVersion,
    inputs: dict[str, Any],
) -> None:
    if is_collection_only(version):
        return
    input_fields = _scene_input_fields(version)
    allowed_names = {str(field["name"]) for field in input_fields}
    unknown_names = sorted(set(inputs) - allowed_names)
    if unknown_names:
        raise HTTPException(
            status_code=422,
            detail="存在未声明的场景参数：" + "、".join(unknown_names) + "。",
        )

    missing_labels: list[str] = []
    for field in input_fields:
        if not field.get("required"):
            continue
        value = inputs.get(field["name"])
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_labels.append(str(field["label"]))
    if missing_labels:
        raise HTTPException(
            status_code=422,
            detail="缺少必填场景参数：" + "、".join(missing_labels) + "。",
        )


def _published_scene_api_contract(
    scene: InspectionScenario,
    version: InspectionScenarioVersion,
) -> dict[str, Any]:
    collecting = is_collection_only(version)
    input_fields = [] if collecting else _scene_input_fields(version)
    request_inputs = {
        field["name"]: f"<{field['label']}>" for field in input_fields
    }
    api_key_required = bool(settings.scene_api_key.strip())
    has_upload_node = not collecting and any(
        node.enabled and node.node_type.upper() == "S3_UPLOAD" for node in version.nodes
    )
    uploads_result = not collecting and settings.scene_result_s3_enabled and (
        has_upload_node or settings.scene_result_s3_auto_publish
    )
    return {
        "scene": {
            "code": scene.code,
            "name": scene.name,
            "mode": scene.mode,
            "run_mode": "COLLECT_ONLY" if is_collection_only(version) else "DETECT",
            "version": version.version,
        },
        "method": "POST",
        "endpoint_path": _scene_api_endpoint_path(scene.code),
        "authentication": {
            "required": api_key_required,
            "header_name": "X-Scene-API-Key" if api_key_required else None,
            "description": (
                "生产环境请在 .env 配置 SCENE_API_KEY，并通过请求头传入。"
                if api_key_required
                else "当前未配置 SCENE_API_KEY，仅适用于本地或受控网络测试。"
            ),
        },
        "input_fields": input_fields,
        "image_input": {
            "field_name": "image_path",
            "required": True,
            "description": "系统保留的检测图片字段，自动注入开始节点；不要填写到 inputs 中。",
        },
        "batch_images": {
            "supported": False,
            "description": "当前场景接口一次处理一张图片。多相机或多张图片请逐张调用，或交由 /api/detect 按配方并发分发。",
        },
        "result_image": {
            "s3_enabled": settings.scene_result_s3_enabled,
            "upload_node": has_upload_node,
            "auto_publish": not collecting and settings.scene_result_s3_auto_publish,
            "destination_field": "result_s3_key",
            "description": (
                "流程中的 S3 上传节点负责上传指定的图片；result_s3_key 可覆盖节点默认对象键。"
                if has_upload_node
                else "已启用兼容模式，场景完成后自动上传结果图。"
                if uploads_result
                else "当前场景不上传 S3；流程中添加 S3 上传节点后才会上传。"
            ),
        },
        "request_example": {
            "request_id": "scene-call-001",
            "image_path": "C:/vision-share/sample.jpg",
            "inputs": request_inputs,
            "enqueue_review": False,
            **({"result_s3_key": "external/scene-call-001_result.jpg"} if uploads_result else {}),
        },
        "response_example": {
            "code": 0,
            "message": "仅采集图片，未执行质量检测，按配置返回 NG" if collecting else "success",
            "request_id": "scene-call-001",
            "scene": {
                "code": scene.code,
                "name": scene.name,
                "mode": scene.mode,
                "version": version.version,
            },
            "execution_id": 123,
            "result": "NG" if collecting else "OK",
            "score": None if collecting else 0.98,
            "elapsed_ms": 135.2,
            "output": {"result": "NOT_INSPECTED" if collecting else "OK"},
            "image_paths": ["C:/vision-share/sample.jpg"] if collecting else (["s3://<bucket>/<prefix>/external/scene-call-001_result.jpg"] if uploads_result else []),
            "result_image_path": ("s3://<bucket>/<prefix>/external/scene-call-001_result.jpg" if uploads_result else None),
            "review_queued": False,
        },
    }


@router.get("")
def list_scenarios(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    scenes = database.scalars(
        select(InspectionScenario)
        .options(
            selectinload(InspectionScenario.versions).selectinload(
                InspectionScenarioVersion.nodes
            ),
            selectinload(InspectionScenario.versions).selectinload(
                InspectionScenarioVersion.edges
            ),
        )
        .where(InspectionScenario.is_deleted.is_(False))
        .order_by(InspectionScenario.id.desc())
    ).all()
    return [_scenario_payload(scene, include_collection=False) for scene in scenes]


@router.get("/published")
def list_published_scenarios(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = database.execute(
        select(InspectionScenario, InspectionScenarioVersion)
        .options(selectinload(InspectionScenarioVersion.nodes))
        .join(
            InspectionScenarioVersion,
            InspectionScenario.published_version_id == InspectionScenarioVersion.id,
        )
        .where(
            InspectionScenario.is_deleted.is_(False),
            InspectionScenario.enabled.is_(True),
            InspectionScenarioVersion.status == "PUBLISHED",
        )
        .order_by(InspectionScenario.category, InspectionScenario.name)
    ).all()
    return [
        {
            "scenario_id": scene.id,
            "scenario_code": scene.code,
            "scenario_name": scene.name,
            "category": scene.category,
            "mode": scene.mode,
            "run_mode": "COLLECT_ONLY" if is_collection_only(version) else "DETECT",
            "scenario_version_id": version.id,
            "version": version.version,
            "input_fields": _scene_input_fields(version),
        }
        for scene, version in rows
    ]


@public_router.get("/invoke/{scene_code}")
def get_published_scene_api_contract(
    scene_code: str,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the stable public contract of a scene's active version."""

    scene, version = _published_scene_for_invoke(database, scene_code)
    return _published_scene_api_contract(scene, version)


@public_router.post("/invoke/{scene_code}")
async def invoke_published_scene(
    scene_code: str,
    payload: PublishedScenarioInvokeRequest,
    x_scene_api_key: str | None = Header(default=None, alias="X-Scene-API-Key"),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Execute the active published version for an external caller.

    The route deliberately resolves the scene's ``published_version_id`` instead
    of accepting a version id. Drafts and archived versions can therefore never
    be selected by an outside system.
    """

    _validate_scene_api_key(x_scene_api_key)
    scene, version = _published_scene_for_invoke(database, scene_code)
    _validate_published_scene_inputs(version, payload.inputs)
    has_upload_node = not is_collection_only(version) and any(
        node.enabled and node.node_type.upper() == "S3_UPLOAD" for node in version.nodes
    )
    auto_publish = not is_collection_only(version) and settings.scene_result_s3_enabled and settings.scene_result_s3_auto_publish
    storage = SceneS3Storage()
    if payload.result_s3_key and not (has_upload_node or auto_publish):
        raise HTTPException(status_code=422, detail="当前场景未配置 S3 上传节点，不能指定 result_s3_key。")
    if has_upload_node or auto_publish:
        try:
            storage.validate_request(payload.result_s3_key)
        except InvalidSceneS3KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SceneS3StorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    execution = await scenario_runtime.execute(
        database,
        version,
        image_path=payload.image_path,
        source="SCENE_API",
        context={**payload.inputs, **({"result_s3_key": payload.result_s3_key} if payload.result_s3_key else {})},
    )
    review_queued = False
    if payload.enqueue_review and execution.result in REVIEW_VERDICTS:
        database.add(
            AutomationJob(
                job_type="VLM_REVIEW",
                scenario_version_id=version.id,
                config_json={"execution_id": execution.id},
                input_snapshot_json={"execution_id": execution.id},
            )
        )
        review_queued = True
    database.commit()
    database.refresh(execution)

    execution_output = execution.output_json or {}
    output = execution_output.get("result") if isinstance(execution_output, dict) else None
    if output is None:
        output = {}
    succeeded = execution.status == "COMPLETED" and execution.result != "ERROR"
    result_image_path: str | None = None
    upload_error: str | None = None
    if succeeded and has_upload_node:
        for trace in reversed(execution_output.get("traces") or []):
            if trace.get("node_type") == "S3_UPLOAD" and isinstance(trace.get("output"), dict):
                result_image_path = trace["output"].get("s3_uri")
                if result_image_path:
                    break
    if succeeded and auto_publish and not has_upload_node:
        try:
            result_snapshot = SceneResultSnapshot(
                id=execution.id,
                result=execution.result or "ERROR",
                output_json=dict(execution_output),
            )
            local_result_path = await asyncio.to_thread(
                build_scene_result_image, result_snapshot, payload.image_path
            )
            result_image_path = await asyncio.to_thread(
                storage.publish_result,
                local_result_path,
                scene_code=scene.code,
                execution_id=execution.id,
                requested_key=payload.result_s3_key,
            )
        except (OSError, ValueError, ScenarioRuntimeError, SceneS3StorageError) as exc:
            upload_error = str(exc)
        execution.output_json = {
            **execution_output,
            "result_image_upload": {
                "status": "FAILED" if upload_error else "COMPLETED",
                "s3_uri": result_image_path,
                "error": upload_error,
            },
        }
        database.commit()
    if result_image_path:
        output = {**output, "result_image_path": result_image_path}
    return {
        "code": (SCENE_API_RESULT_UPLOAD_FAILURE_CODE if upload_error else 0 if succeeded else SCENE_API_FAILURE_CODE),
        "message": (
            f"场景检测已完成，但结果图上传失败：{upload_error}"
            if upload_error
            else (output.get("reason") or "仅采集图片，未执行质量检测") if is_collection_only(version)
            else "success" if succeeded else (execution.error_message or "场景执行失败。")
        ),
        "request_id": payload.request_id or f"scene-{execution.id}",
        "scene": {
            "code": scene.code,
            "name": scene.name,
            "mode": scene.mode,
            "version": version.version,
        },
        "execution_id": execution.id,
        "result": "NG" if is_collection_only(version) else execution.result or "ERROR",
        "score": execution.score,
        "elapsed_ms": execution.elapsed_ms,
        "output": output,
        "image_paths": [payload.image_path] if is_collection_only(version) else [result_image_path] if result_image_path else [],
        "result_image_path": result_image_path,
        "review_queued": review_queued,
    }


@router.post("")
def create_scenario(
    payload: ScenarioCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    code = payload.code.strip() if payload.code else _generate_scene_code(database)
    mode = payload.mode.strip().upper()
    if mode not in SCENE_MODES:
        raise HTTPException(status_code=400, detail="场景类型仅支持 VLM_DIRECT 或 WORKFLOW。")
    if database.scalar(select(InspectionScenario.id).where(InspectionScenario.code == code)):
        raise HTTPException(status_code=409, detail=f"场景编码“{code}”已存在，请更换后再创建。")
    _validate_vlm(database, payload.primary_vlm_model_id)
    _validate_vlm(database, payload.review_vlm_model_id)
    try:
        scene = InspectionScenario(
            code=code,
            name=payload.name.strip(),
            category=payload.category.strip() or "GENERAL",
            mode=mode,
            description=payload.description,
        )
        database.add(scene)
        database.flush()
        version = InspectionScenarioVersion(
            scenario=scene,
            version=payload.version,
            prompt_template=payload.prompt_template,
            primary_vlm_model_id=payload.primary_vlm_model_id,
            review_vlm_model_id=payload.review_vlm_model_id,
            definition_json={"run_mode": payload.run_mode},
        )
        database.add(version)
        database.flush()
        if mode == "WORKFLOW":
            _create_workflow_skeleton(database, version)
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"场景编码“{code}”已存在，请更换后再创建。",
        ) from exc
    return {"id": scene.id, "version_id": version.id, "status": "DRAFT"}


def _assistant_catalog(database: Session) -> dict[str, Any]:
    vlms = database.scalars(select(VlmModelConfig).where(
        VlmModelConfig.enabled.is_(True), VlmModelConfig.is_deleted.is_(False),
    )).all()
    versions = database.scalars(select(VisionModelVersion).join(VisionModel).where(
        VisionModelVersion.status == "PUBLISHED", VisionModelVersion.is_deleted.is_(False),
        VisionModel.enabled.is_(True), VisionModel.is_deleted.is_(False),
    )).all()
    return {
        "vlm": [{"id": model.id, "name": model.name} for model in vlms],
        "trained": [{"id": version.id, "name": version.vision_model.name,
                     "task_type": version.vision_model.task_type,
                     "labels": version.vision_model.labels_json} for version in versions],
    }


def _assistant_version(mode: str, design: SceneDesign, catalog: dict[str, Any]) -> InspectionScenarioVersion:
    if design.mode != mode:
        raise ValueError("生成的场景类型与当前场景不一致")
    validate_design_references(design)
    vlm_ids = {model["id"] for model in catalog["vlm"]}
    trained_ids = {model["id"] for model in catalog["trained"]}
    if (mode == "VLM_DIRECT" or design.primary_vlm_model_id is not None) and design.primary_vlm_model_id not in vlm_ids:
        raise ValueError("请选择可用的检测VLM模型")
    allowed_config = {
        "START": set(), "END": {"output"},
        "VLM": {"vlm_model_id", "prompt", "image_path"},
        "VISION_MODEL": {"model_version_id", "image_path", "confidence"},
        "OCR": {"image_path", "min_score"},
        "RULE": {"actual", "operator", "expected"},
        "IMAGE_CROP": {"image_path", "bbox", "objects", "padding_ratio"},
        "IMAGE_ANNOTATE": {"image_path", "bbox", "objects"},
    }
    for node in design.nodes:
        config = node.config_json
        if set(config) - allowed_config[node.node_type]:
            raise ValueError(f"节点 {node.name} 包含不支持的配置项")
        if node.node_type == "VLM" and (not isinstance(config.get("prompt"), str) or not 1 <= len(config["prompt"].strip()) <= 8000):
            raise ValueError(f"节点 {node.name} 的提示词必须是1至8000字的文字")
        if node.node_type == "RULE" and config.get("operator") not in {
            "EQUALS", "CONTAINS", "EXISTS", "NUMBER_EQUALS", "NUMBER_GT", "NUMBER_GTE", "NUMBER_LT", "NUMBER_LTE",
        }:
            raise ValueError(f"节点 {node.name} 使用了不支持的规则运算符")
        if node.node_type == "END" and not isinstance(config.get("output"), dict):
            raise ValueError("结束节点必须配置输出字段映射")
        for field, permitted in (("vlm_model_id", vlm_ids), ("model_version_id", trained_ids)):
            if (node.node_type == "VLM" and field == "vlm_model_id") or (node.node_type == "VISION_MODEL" and field == "model_version_id"):
                value = config.get(field)
                if type(value) is not int or value not in permitted:
                    raise ValueError(f"节点 {node.name} 未选择有效模型版本：{field}")
        if "confidence" in config and (type(config["confidence"]) not in (int, float) or not 0 <= config["confidence"] <= 1):
            raise ValueError("模型置信度必须是0至1的数字")
        if "image_path" in config and not re.fullmatch(
            r"\s*\{\{\s*(?:input\.image_path|nodes\.[A-Za-z_][A-Za-z0-9_]*\.(?:crops\.\d+\.)?image_path)\s*\}\}\s*",
            str(config["image_path"]),
        ):
            raise ValueError("图片路径必须引用输入图片或上游节点，不能由AI填写本地路径")
    fields = [field.model_dump() for field in design.input_fields]
    version = InspectionScenarioVersion(
        id=0, scenario_id=0, scenario=InspectionScenario(mode=mode), status="DRAFT",
        prompt_template=design.prompt_template or None,
        primary_vlm_model_id=design.primary_vlm_model_id,
        input_schema_json={"fields": fields}, output_schema_json={}, definition_json={},
    )
    for index, node in enumerate(design.nodes):
        config = dict(node.config_json)
        if node.node_type == "START":
            config = {"inputs": [{"name": "image_path", "label": "检测图片", "type": "IMAGE", "required": True}, *fields]}
        version.nodes.append(ScenarioNode(
            id=index + 1, node_key=node.node_key, name=node.name, node_type=node.node_type,
            config_json=config, enabled=True, sort_order=(index + 1) * 10,
            canvas_x=80 + (index % 5) * 320, canvas_y=120 + (index // 5) * 210,
        ))
    for index, edge in enumerate(design.edges):
        version.edges.append(ScenarioEdge(id=index + 1, **edge.model_dump(), mapping_json={}))
    return version


def _check_assistant_design(database: Session, mode: str, design: SceneDesign) -> InspectionScenarioVersion:
    version = _assistant_version(mode, design, _assistant_catalog(database))
    preflight = _workflow_preflight(database, version)
    errors = [item["message"] for item in preflight["diagnostics"] if item["level"] == "ERROR"]
    if errors:
        raise ValueError("；".join(errors))
    return version


@router.post("/{scenario_id}/design-assistant")
async def assist_scene_design(
    scenario_id: int,
    vlm_model_id: int = Form(...),
    requirements: str = Form(..., min_length=1, max_length=8000),
    conversation_json: str = Form("{}", max_length=150000),
    image: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.get(InspectionScenario, scenario_id)
    if scene is None or scene.is_deleted:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    model = database.get(VlmModelConfig, vlm_model_id)
    if model is None or model.is_deleted or not model.enabled:
        raise HTTPException(status_code=422, detail="请选择已启用的VLM接口配置。")
    if not requirements.strip():
        raise HTTPException(status_code=422, detail="请填写检测要求。")
    try:
        conversation = DesignConversation.model_validate_json(conversation_json)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="对话内容格式不正确或过长。") from exc
    content = await image.read(20 * 1024 * 1024 + 1)
    await image.close()
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="请上传不超过20MB的图片。")
    with TemporaryDirectory(prefix="vision-scene-design-") as temporary:
        image_path = Path(temporary) / "sample.png"
        image_path.write_bytes(content)
        try:
            with Image.open(image_path) as uploaded:
                if uploaded.width * uploaded.height > 40_000_000:
                    raise ValueError("图片像素超过4000万，请缩小后上传。")
                uploaded.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="图片无法读取或像素过大，请重新上传。") from exc
        try:
            design = await generate_scene_design(
                model, image_path=str(image_path), mode=scene.mode, requirements=requirements.strip(),
                conversation=conversation, catalog=_assistant_catalog(database),
                validate=lambda generated: _check_assistant_design(database, scene.mode, generated),
            )
        except (VlmRequestError, VlmConfigurationError) as exc:
            raise HTTPException(status_code=502, detail=f"AI设计服务调用失败：{exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"design": design.model_dump(), "message": "生成完成，尚未修改场景。请确认后创建新草稿并测试。"}


@router.post("/{scenario_id}/design-assistant/apply")
def apply_assisted_scene_design(
    scenario_id: int, design: SceneDesign, database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.get(InspectionScenario, scenario_id)
    if scene is None or scene.is_deleted:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    try:
        generated = _check_assistant_design(database, scene.mode, design)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    version = InspectionScenarioVersion(
        scenario_id=scene.id, version=f"AI-{datetime.utcnow():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}",
        status="DRAFT", prompt_template=generated.prompt_template,
        primary_vlm_model_id=generated.primary_vlm_model_id,
        input_schema_json=generated.input_schema_json, output_schema_json={},
        definition_json={"design_assistant": {"explanation": design.explanation}},
    )
    database.add(version)
    database.flush()
    for node in generated.nodes:
        database.add(ScenarioNode(
            scenario_version_id=version.id, node_key=node.node_key, name=node.name,
            node_type=node.node_type, config_json=node.config_json, sort_order=node.sort_order,
            canvas_x=node.canvas_x, canvas_y=node.canvas_y, enabled=True,
        ))
    for edge in generated.edges:
        database.add(ScenarioEdge(
            scenario_version_id=version.id, source_node_key=edge.source_node_key,
            target_node_key=edge.target_node_key, mapping_json={},
        ))
    database.commit()
    return {"version_id": version.id, "version": version.version, "status": "DRAFT"}


@router.get("/{scenario_id}")
def get_scenario(
    scenario_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.scalar(
        select(InspectionScenario)
        .options(
            selectinload(InspectionScenario.versions).selectinload(
                InspectionScenarioVersion.nodes
            ),
            selectinload(InspectionScenario.versions).selectinload(
                InspectionScenarioVersion.edges
            ),
        )
        .where(InspectionScenario.id == scenario_id)
    )
    if scene is None or scene.is_deleted:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    return _scenario_payload(scene)


@router.put("/{scenario_id}")
def update_scenario(
    scenario_id: int,
    payload: ScenarioUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.get(InspectionScenario, scenario_id)
    if scene is None or scene.is_deleted:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(scene, key, value.strip() if isinstance(value, str) else value)
    database.commit()
    return {"id": scene.id, "updated": True}


@router.post("/{scenario_id}/versions")
def create_scenario_version(
    scenario_id: int,
    payload: VersionCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.get(InspectionScenario, scenario_id)
    if scene is None or scene.is_deleted:
        raise HTTPException(status_code=404, detail="检测场景不存在。")
    if database.scalar(
        select(InspectionScenarioVersion.id).where(
            InspectionScenarioVersion.scenario_id == scene.id,
            InspectionScenarioVersion.version == payload.version,
        )
    ):
        raise HTTPException(status_code=409, detail="该场景版本已存在。")
    _validate_vlm(database, payload.primary_vlm_model_id)
    _validate_vlm(database, payload.review_vlm_model_id)
    source_version: InspectionScenarioVersion | None = None
    if payload.source_version_id:
        source_version = _version_or_404(database, payload.source_version_id)
        if source_version.scenario_id != scene.id:
            raise HTTPException(status_code=400, detail="新版本只能复制当前场景的版本定义。")
    version_values = payload.model_dump(exclude={"source_version_id"})
    if source_version is not None:
        for field in ("definition_json", "input_schema_json", "output_schema_json", "prompt_template", "primary_vlm_model_id", "review_vlm_model_id"):
            if field not in payload.model_fields_set:
                version_values[field] = getattr(source_version, field)
    version = InspectionScenarioVersion(scenario=scene, **version_values)
    database.add(version)
    database.flush()
    if scene.mode == "WORKFLOW":
        if source_version is not None:
            _clone_workflow_definition(database, source_version, version)
        else:
            _create_workflow_skeleton(database, version)
    database.commit()
    database.refresh(version)
    return {"id": version.id, "version": version.version, "status": version.status}


@router.get("/versions/{version_id}")
def get_scenario_version(
    version_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    return _version_payload(_version_or_404(database, version_id))


@router.get("/versions/{version_id}/preflight")
def preflight_scenario_version(
    version_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a non-mutating design-time inspection for the selected version."""

    return _workflow_preflight(database, _version_or_404(database, version_id))


@router.put("/versions/{version_id}")
def update_scenario_version(
    version_id: int,
    payload: VersionUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    values = payload.model_dump(exclude_unset=True)
    _validate_vlm(database, values.get("primary_vlm_model_id"))
    _validate_vlm(database, values.get("review_vlm_model_id"))
    for key, value in values.items():
        setattr(version, key, value)
    database.commit()
    return _version_payload(_version_or_404(database, version_id))


@router.post("/versions/{version_id}/nodes")
def add_node(
    version_id: int,
    payload: NodeCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    node_type = payload.node_type.upper()
    if node_type not in NODE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的节点类型。")
    if version.scenario.mode != "WORKFLOW":
        raise HTTPException(status_code=400, detail="只有流程检测场景可以维护流程节点。")
    if any(node.node_key == payload.node_key for node in version.nodes):
        raise HTTPException(status_code=409, detail="节点键已存在。")
    existing_types = {node.node_type.upper() for node in version.nodes if node.enabled}
    if node_type in {"START", "END"} and node_type in existing_types:
        raise HTTPException(status_code=409, detail=f"流程已经包含{payload.name}节点，无需重复添加。")
    normalized_payload = payload.model_copy(update={"node_type": node_type})
    node = _insert_workflow_node(database, version, normalized_payload)
    database.commit()
    database.refresh(node)
    return _node_payload(node)


@router.put("/versions/{version_id}/nodes/{node_id}")
def update_node(
    version_id: int,
    node_id: int,
    payload: NodeUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    node = _node_or_404(version, node_id)
    values = payload.model_dump(exclude_unset=True)
    if node.node_type.upper() in {"START", "END"} and "enabled" in values and not values["enabled"]:
        raise HTTPException(status_code=400, detail="开始和结束节点不能停用。")
    for key, value in values.items():
        setattr(node, key, value)
    database.commit()
    database.refresh(node)
    return _node_payload(node)


@router.delete("/versions/{version_id}/nodes/{node_id}")
def delete_node(
    version_id: int,
    node_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    node = _node_or_404(version, node_id)
    if node.node_type.upper() in {"START", "END"}:
        raise HTTPException(status_code=400, detail="开始和结束节点由系统维护，不能删除。")
    for edge in list(version.edges):
        if edge.source_node_key == node.node_key or edge.target_node_key == node.node_key:
            database.delete(edge)
    database.delete(node)
    database.commit()
    return {"id": node_id, "deleted": True}


@router.post("/versions/{version_id}/edges")
def add_edge(
    version_id: int,
    payload: EdgeCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    keys = {node.node_key for node in version.nodes}
    if payload.source_node_key not in keys or payload.target_node_key not in keys:
        raise HTTPException(status_code=400, detail="连线必须连接当前版本中已有的节点。")
    duplicate = database.scalar(
        select(ScenarioEdge.id).where(
            ScenarioEdge.scenario_version_id == version.id,
            ScenarioEdge.source_node_key == payload.source_node_key,
            ScenarioEdge.target_node_key == payload.target_node_key,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该节点连线已存在。")
    if payload.source_node_key == payload.target_node_key or _would_create_cycle(
        version,
        payload.source_node_key,
        payload.target_node_key,
    ):
        raise HTTPException(status_code=400, detail="连线不能形成闭环；循环请使用“循环控制”节点配置。")
    edge = ScenarioEdge(scenario_version=version, **payload.model_dump())
    database.add(edge)
    database.commit()
    return {"id": edge.id, "created": True}


@router.put("/versions/{version_id}/edges/{edge_id}")
def update_edge(
    version_id: int,
    edge_id: int,
    payload: EdgeUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    edge = next((item for item in version.edges if item.id == edge_id), None)
    if edge is None:
        raise HTTPException(status_code=404, detail="流程连线不存在。")
    edge.mapping_json = payload.mapping_json
    database.commit()
    return {"id": edge.id, "updated": True}


@router.delete("/versions/{version_id}/edges/{edge_id}")
def delete_edge(
    version_id: int,
    edge_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    edge = next((item for item in version.edges if item.id == edge_id), None)
    if edge is None:
        raise HTTPException(status_code=404, detail="流程连线不存在。")
    database.delete(edge)
    database.commit()
    return {"id": edge_id, "deleted": True}


@router.post("/versions/{version_id}/publish")
def publish_scenario_version(
    version_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    _ensure_draft(version)
    scene = version.scenario
    preflight = _workflow_preflight(database, version)
    blocking = [item for item in preflight["diagnostics"] if item["level"] == "ERROR"]
    if blocking:
        details = "；".join(str(item["message"]) for item in blocking[:3])
        more = "" if len(blocking) <= 3 else f"；另有 {len(blocking) - 3} 项问题"
        raise HTTPException(status_code=400, detail=f"发布前检查未通过：{details}{more}")
    if is_collection_only(version):
        pass
    elif scene.mode == "VLM_DIRECT":
        if not version.primary_vlm_model_id or not version.prompt_template:
            raise HTTPException(status_code=400, detail="直接 VLM 场景必须配置主 VLM 和提示词。")
    else:
        node_types = {node.node_type.upper() for node in version.nodes if node.enabled}
        has_nested_vision = any(
            node.enabled and node.node_type.upper() in {"LOOP", "ITERATION", "CONDITION_LOOP"}
            and any(isinstance(child, dict) and child.get("node_type") in {"VLM", "VISION_MODEL", "OCR"} for child in (node.config_json or {}).get("body_nodes", []))
            for node in version.nodes
        )
        if node_types.isdisjoint({"VLM", "VISION_MODEL", "OCR"}) and not has_nested_vision:
            raise HTTPException(status_code=400, detail="工作流至少需要一个 VLM、训练模型或 OCR 节点。")
        _validate_workflow_graph(version)
        for node in version.nodes:
            if node.enabled and node.node_type.upper() == "VLM":
                _validate_vlm_node(database, node)
            if node.enabled and node.node_type.upper() == "VISION_MODEL":
                _validate_published_vision_model_node(database, node)
            if node.enabled and node.node_type.upper() == "OCR":
                _validate_ocr_node(node)
            if node.enabled and node.node_type.upper() == "IMAGE_CROP":
                _validate_image_crop_node(database, version, node)
            if node.enabled and node.node_type.upper() == "IMAGE_ANNOTATE":
                _validate_image_annotate_node(database, version, node)
            if node.enabled and node.node_type.upper() == "S3_UPLOAD":
                if not str((node.config_json or {}).get("image_path") or "").strip():
                    raise HTTPException(status_code=400, detail="S3 上传节点必须配置图片来源。")
    previously_published_version_ids = database.scalars(
        select(InspectionScenarioVersion.id).where(
            InspectionScenarioVersion.scenario_id == scene.id,
            InspectionScenarioVersion.id != version.id,
            InspectionScenarioVersion.status == "PUBLISHED",
        )
    ).all()
    database.query(InspectionScenarioVersion).filter(
        InspectionScenarioVersion.scenario_id == scene.id,
        InspectionScenarioVersion.id != version.id,
        InspectionScenarioVersion.status == "PUBLISHED",
    ).update({"status": "ARCHIVED"}, synchronize_session=False)
    version.status = "PUBLISHED"
    version.published_at = datetime.utcnow()
    scene.published_version_id = version.id
    # ROI and dataset collection associations represent a logical scene, not
    # an immutable historical release.  Follow the current published release
    # at publish time so production calls cannot end up referencing an archived
    # scenario version.  Historical ScenarioExecution rows retain their own
    # version IDs and remain fully traceable.
    rebound_roi_count = 0
    rebound_dataset_count = 0
    if previously_published_version_ids:
        rebound_roi_count = database.query(RoiScenarioBinding).filter(
            RoiScenarioBinding.enabled.is_(True),
            RoiScenarioBinding.scenario_version_id.in_(previously_published_version_ids),
        ).update({"scenario_version_id": version.id}, synchronize_session=False)
        rebound_dataset_count = database.query(Dataset).filter(
            Dataset.is_deleted.is_(False),
            Dataset.collection_scenario_version_id.in_(previously_published_version_ids),
        ).update({"collection_scenario_version_id": version.id}, synchronize_session=False)
    database.commit()
    return {
        "id": version.id,
        "status": version.status,
        "published": True,
        "api_endpoint_path": _scene_api_endpoint_path(scene.code),
        "rebound_roi_count": rebound_roi_count,
        "rebound_dataset_count": rebound_dataset_count,
    }


@router.put("/rois/{roi_id}/binding")
def bind_roi_to_scenario(
    roi_id: int,
    payload: RoiBindingRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None or roi.is_deleted:
        raise HTTPException(status_code=404, detail="检测区域不存在。")
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is not None and recipe.status == "PUBLISHED":
        raise HTTPException(status_code=409, detail="已发布配方不能直接修改场景关联，请先复制配方。")
    version = _version_or_404(database, payload.scenario_version_id)
    if version.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="ROI 只能关联已发布的场景版本。")
    input_fields = _scene_input_fields(version)
    allowed_input_names = {item["name"] for item in input_fields}
    unknown_fields = sorted(set(payload.input_mapping_json) - allowed_input_names)
    if unknown_fields:
        raise HTTPException(
            status_code=422,
            detail=(
                "ROI 校验值只能绑定所选场景已声明的输入字段："
                + "、".join(unknown_fields)
            ),
        )
    binding = database.scalar(
        select(RoiScenarioBinding).where(RoiScenarioBinding.roi_id == roi.id)
    )
    if binding is None:
        binding = RoiScenarioBinding(
            roi_id=roi.id,
            scenario_version_id=version.id,
            input_mapping_json=payload.input_mapping_json,
        )
        database.add(binding)
    else:
        binding.scenario_version_id = version.id
        binding.input_mapping_json = payload.input_mapping_json
        binding.enabled = True
    database.commit()
    return {
        "roi_id": roi.id,
        "scenario_version_id": version.id,
        "scenario_code": version.scenario.code,
        "scenario_name": version.scenario.name,
        "version": version.version,
        "input_mapping_json": binding.input_mapping_json,
        "input_fields": input_fields,
    }


@router.get("/rois/{roi_id}/binding")
def get_roi_scenario_binding(
    roi_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    binding = database.scalar(
        select(RoiScenarioBinding)
        .options(
            selectinload(RoiScenarioBinding.scenario_version).selectinload(
                InspectionScenarioVersion.scenario
            )
        )
        .where(
            RoiScenarioBinding.roi_id == roi_id,
            RoiScenarioBinding.enabled.is_(True),
        )
    )
    if binding is None:
        return {"roi_id": roi_id, "scenario_version_id": None}
    version = binding.scenario_version
    return {
        "roi_id": roi_id,
        "scenario_version_id": version.id,
        "scenario_code": version.scenario.code,
        "scenario_name": version.scenario.name,
        "version": version.version,
        "input_mapping_json": binding.input_mapping_json,
        "input_fields": _scene_input_fields(version),
    }


@router.delete("/rois/{roi_id}/binding")
def unbind_roi_from_scenario(
    roi_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    binding = database.scalar(
        select(RoiScenarioBinding).where(RoiScenarioBinding.roi_id == roi_id)
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="该 ROI 未关联检测场景。")
    binding.enabled = False
    database.commit()
    return {"roi_id": roi_id, "unbound": True}


@router.post("/versions/{version_id}/execute")
async def execute_scenario_version(
    version_id: int,
    payload: ScenarioExecuteRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version_or_404(database, version_id)
    execution = await scenario_runtime.execute(
        database,
        version,
        image_path=payload.image_path,
        source="TEST",
        context=payload.context,
    )
    if payload.enqueue_review and not is_collection_only(version):
        database.add(
            AutomationJob(
                job_type="VLM_REVIEW",
                scenario_version_id=version.id,
                config_json={"execution_id": execution.id},
                input_snapshot_json={"execution_id": execution.id},
            )
        )
    database.commit()
    database.refresh(execution)
    return _execution_payload(execution)


@router.post("/versions/{version_id}/preview")
async def preview_scenario_version(
    version_id: int,
    payload: ScenarioExecuteRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run a draft or published version for the designer without publishing it."""

    version = _version_or_404(database, version_id)
    _validate_preview_rule_contract(database, version)
    execution = await scenario_runtime.execute(
        database,
        version,
        image_path=payload.image_path,
        source="SCENE_DESIGN_TEST",
        context=payload.context,
        allow_draft=True,
    )
    database.commit()
    database.refresh(execution)
    return _execution_payload(execution)


@router.post("/versions/{version_id}/preview-upload")
async def preview_scenario_upload(
    version_id: int,
    image: UploadFile = File(...),
    context_json: str = Form("{}"),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Store one designer test image locally, then run the draft scene against it."""

    version = _version_or_404(database, version_id)
    _validate_preview_rule_contract(database, version)
    try:
        context = json.loads(context_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="测试参数必须是有效 JSON 对象。") from exc
    if not isinstance(context, dict):
        raise HTTPException(status_code=422, detail="测试参数必须是 JSON 对象。")
    original_suffix = Path(image.filename or "").suffix.lower()
    suffix = original_suffix if original_suffix in {".jpg", ".jpeg", ".png", ".bmp", ".webp"} else ".jpg"
    target_directory = PROJECT_ROOT / "uploads" / "scene_tests" / str(version.id)
    target_directory.mkdir(parents=True, exist_ok=True)
    target_path = target_directory / f"{datetime.utcnow():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}{suffix}"
    try:
        with target_path.open("wb") as destination:
            shutil.copyfileobj(image.file, destination)
    finally:
        await image.close()
    execution = await scenario_runtime.execute(
        database,
        version,
        image_path=str(target_path),
        source="SCENE_DESIGN_TEST",
        context=context,
        allow_draft=True,
    )
    database.commit()
    database.refresh(execution)
    payload = _execution_payload(execution)
    payload["test_image_path"] = str(target_path)
    return payload


@router.get("/executions/{execution_id}")
def get_execution(
    execution_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    execution = database.scalar(
        select(ScenarioExecution)
        .options(selectinload(ScenarioExecution.review))
        .where(ScenarioExecution.id == execution_id)
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="场景执行记录不存在。")
    return _execution_payload(execution)


@router.post("/executions/{execution_id}/review")
def enqueue_execution_review(
    execution_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    execution = database.get(ScenarioExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="场景执行记录不存在。")
    if is_collection_only(execution.scenario_version):
        raise HTTPException(status_code=400, detail="仅采集记录未执行检测，不支持 VLM 复核。")
    existing = database.scalar(
        select(AutomationJob).where(
            AutomationJob.job_type == "VLM_REVIEW",
            AutomationJob.status.in_(("QUEUED", "RUNNING", "WAITING_GPU")),
            AutomationJob.config_json["execution_id"].as_integer() == execution.id,
        )
    )
    if existing:
        return {"job_id": existing.id, "status": existing.status, "existing": True}
    job = AutomationJob(
        job_type="VLM_REVIEW",
        scenario_version_id=execution.scenario_version_id,
        config_json={"execution_id": execution.id},
        input_snapshot_json={"execution_id": execution.id},
    )
    database.add(job)
    database.commit()
    return {"job_id": job.id, "status": job.status, "existing": False}


@router.put("/executions/{execution_id}/manual-review")
def record_manual_review(
    execution_id: int,
    payload: ManualReviewRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    verdict = payload.verdict.upper()
    if verdict not in REVIEW_VERDICTS:
        raise HTTPException(status_code=400, detail="人工复判只能填写 OK、NG 或 UNCERTAIN。")
    execution = database.scalar(
        select(ScenarioExecution)
        .options(selectinload(ScenarioExecution.review))
        .where(ScenarioExecution.id == execution_id)
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="场景执行记录不存在。")
    if is_collection_only(execution.scenario_version):
        raise HTTPException(status_code=400, detail="仅采集记录不参与准确率统计，请在数据集中标注素材。")
    review = execution.review
    if review is None:
        review = ScenarioExecutionReview(execution_id=execution.id, status="SKIPPED")
        database.add(review)
    review.manual_verdict = verdict
    review.manual_note = payload.note
    review.manually_reviewed_at = datetime.utcnow()
    database.commit()
    return {"execution_id": execution.id, "manual_verdict": review.manual_verdict}
