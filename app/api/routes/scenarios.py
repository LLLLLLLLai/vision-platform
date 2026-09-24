from datetime import datetime
import json
from pathlib import Path
import re
from secrets import compare_digest
import shutil
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT, settings
from app.db.session import get_db
from app.models.intelligence import (
    AutomationJob,
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
from app.services.scenario_runtime import scenario_runtime
from app.services.vision_model_runtime import resolve_weights_path, task_output_profile


router = APIRouter()

SCENE_MODES = {"VLM_DIRECT", "WORKFLOW"}
SCENE_API_FAILURE_CODE = 5001
NODE_TYPES = {
    "START",
    "VLM",
    "VISION_MODEL",
    "IMAGE_CROP",
    "RULE",
    "WEB_API",
    "IF",
    "LOOP",
    "END",
}
REVIEW_VERDICTS = {"OK", "NG", "UNCERTAIN"}
IMAGE_INPUT_NAMES = {"image", "image_path", "image_paths", "image_file"}
_DYNAMIC_BBOX_REFERENCE = re.compile(
    r"^\s*\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects(?:\.\d+)?\.bbox\s*\}\}\s*$"
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
    if version is None or version.is_deleted or version.status != "PUBLISHED":
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


def _bbox_source_node_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DYNAMIC_BBOX_REFERENCE.fullmatch(value)
    return match.group(1) if match else None


def _validate_image_crop_node(
    database: Session,
    scenario_version: InspectionScenarioVersion,
    node: ScenarioNode,
) -> None:
    config = node.config_json or {}
    input_mapping = config.get("input_mapping") or config.get("context") or {}
    mapped_bbox = input_mapping.get("bbox") if isinstance(input_mapping, dict) else None
    configured_bbox = config.get("bbox")
    if configured_bbox in (None, "", []) and mapped_bbox in (None, "", []):
        raise HTTPException(
            status_code=400,
            detail=(
                f"图片裁剪节点“{node.name}”必须配置 bbox，"
                "例如引用上游训练模型的 {{ nodes.model_1.objects.0.bbox }}。"
            ),
        )

    for value in (mapped_bbox, configured_bbox):
        source_key = _bbox_source_node_key(value)
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
        if not profile["supports_bbox"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"图片裁剪节点“{node.name}”不能引用“{source_node.name}”的 bbox："
                    f"{profile['display_name']}不输出定位框。"
                    "请改用目标检测/当前 YOLO 分割模型，或直接填写固定 bbox。"
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
        raise HTTPException(status_code=400, detail="工作流不能存在闭环；请使用循环控制节点处理集合。")


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
        "primary_vlm_model_id": version.primary_vlm_model_id,
        "review_vlm_model_id": version.review_vlm_model_id,
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


def _scenario_payload(scene: InspectionScenario) -> dict[str, Any]:
    versions = sorted(scene.versions, key=lambda item: item.id, reverse=True)
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
        "output_json": execution.output_json,
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
    input_fields = _scene_input_fields(version)
    request_inputs = {
        field["name"]: f"<{field['label']}>" for field in input_fields
    }
    api_key_required = bool(settings.scene_api_key.strip())
    return {
        "scene": {
            "code": scene.code,
            "name": scene.name,
            "mode": scene.mode,
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
        "request_example": {
            "request_id": "scene-call-001",
            "image_path": "C:/vision-share/sample.jpg",
            "inputs": request_inputs,
            "enqueue_review": False,
        },
        "response_example": {
            "code": 0,
            "message": "success",
            "request_id": "scene-call-001",
            "scene": {
                "code": scene.code,
                "name": scene.name,
                "mode": scene.mode,
                "version": version.version,
            },
            "execution_id": 123,
            "result": "OK",
            "score": 0.98,
            "elapsed_ms": 135.2,
            "output": {"result": "OK"},
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
    return [_scenario_payload(scene) for scene in scenes]


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
            "scenario_version_id": version.id,
            "version": version.version,
            "input_fields": _scene_input_fields(version),
        }
        for scene, version in rows
    ]


@router.get("/invoke/{scene_code}")
def get_published_scene_api_contract(
    scene_code: str,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the stable public contract of a scene's active version."""

    scene, version = _published_scene_for_invoke(database, scene_code)
    return _published_scene_api_contract(scene, version)


@router.post("/invoke/{scene_code}")
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

    execution = await scenario_runtime.execute(
        database,
        version,
        image_path=payload.image_path,
        source="SCENE_API",
        context=payload.inputs,
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
    return {
        "code": 0 if succeeded else SCENE_API_FAILURE_CODE,
        "message": "success" if succeeded else (execution.error_message or "场景执行失败。"),
        "request_id": payload.request_id or f"scene-{execution.id}",
        "scene": {
            "code": scene.code,
            "name": scene.name,
            "mode": scene.mode,
            "version": version.version,
        },
        "execution_id": execution.id,
        "result": execution.result or "ERROR",
        "score": execution.score,
        "elapsed_ms": execution.elapsed_ms,
        "output": output,
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
    if scene.mode == "VLM_DIRECT":
        if not version.primary_vlm_model_id or not version.prompt_template:
            raise HTTPException(status_code=400, detail="直接 VLM 场景必须配置主 VLM 和提示词。")
    else:
        node_types = {node.node_type.upper() for node in version.nodes if node.enabled}
        if node_types.isdisjoint({"VLM", "VISION_MODEL"}):
            raise HTTPException(status_code=400, detail="工作流至少需要一个 VLM 检测或模型检测节点。")
        _validate_workflow_graph(version)
        for node in version.nodes:
            if node.enabled and node.node_type.upper() == "VLM":
                _validate_vlm_node(database, node)
            if node.enabled and node.node_type.upper() == "VISION_MODEL":
                _validate_published_vision_model_node(database, node)
            if node.enabled and node.node_type.upper() == "IMAGE_CROP":
                _validate_image_crop_node(database, version, node)
    database.query(InspectionScenarioVersion).filter(
        InspectionScenarioVersion.scenario_id == scene.id,
        InspectionScenarioVersion.id != version.id,
        InspectionScenarioVersion.status == "PUBLISHED",
    ).update({"status": "ARCHIVED"}, synchronize_session=False)
    version.status = "PUBLISHED"
    version.published_at = datetime.utcnow()
    scene.published_version_id = version.id
    database.commit()
    return {
        "id": version.id,
        "status": version.status,
        "published": True,
        "api_endpoint_path": _scene_api_endpoint_path(scene.code),
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
    if payload.enqueue_review:
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
    review = execution.review
    if review is None:
        review = ScenarioExecutionReview(execution_id=execution.id, status="SKIPPED")
        database.add(review)
    review.manual_verdict = verdict
    review.manual_note = payload.note
    review.manually_reviewed_at = datetime.utcnow()
    database.commit()
    return {"execution_id": execution.id, "manual_verdict": review.manual_verdict}
