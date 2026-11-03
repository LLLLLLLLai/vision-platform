"""Execution runtime for published inspection scenarios.

V1 supports direct VLM scenes and a linear, data-mapped workflow.  The stored
node graph remains forward-compatible with a canvas executor: each node receives
the immutable input object and outputs from earlier nodes under ``nodes``.
"""

import asyncio
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.intelligence import (
    InspectionScenarioVersion,
    ScenarioExecution,
    ScenarioExecutionReview,
    ScenarioEdge,
    ScenarioNode,
    VlmModelConfig,
    VisionModel,
    VisionModelVersion,
)
from app.core.config import PROJECT_ROOT, settings
from app.services.openai_compatible_vlm import (
    INSPECTION_VLM_SYSTEM_PROMPT,
    VlmConfigurationError,
    VlmRequestError,
    vlm_client,
)
from app.services.dataset_collection_service import try_collect_roi_for_matching_datasets
from app.services.loop_body_graph import loop_body_connections
from app.services.remote_yolo_inference import run_remote_yolo_inference
from app.services.scene_s3_storage import SceneS3Storage, SceneS3StorageError
from app.services.vision_model_runtime import (
    VisionModelRuntimeError,
    build_published_model_spec,
    run_yolo_inference,
    task_output_profile,
)


class ScenarioRuntimeError(RuntimeError):
    pass


_REFERENCE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_TRACE_SENSITIVE_KEYWORDS = ("authorization", "api_key", "apikey", "token", "password", "secret")
_MAX_BATCH_CROPS = 30
_MAX_LOOP_VLM_PARALLELISM = 3
_MAX_LOOP_BODY_NODES = 20
_MAX_LOOP_BODY_ITERATIONS = 100
_MAX_CONDITION_LOOP_ROUNDS = 20


def _path_value(source: Any, path: str) -> Any:
    value = source
    for segment in path.split("."):
        if isinstance(value, dict):
            value = value.get(segment)
        elif isinstance(value, list) and segment.isdigit():
            index = int(segment)
            value = value[index] if 0 <= index < len(value) else None
        else:
            return None
    return value


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if not isinstance(value, str):
        return value

    whole_match = _REFERENCE.fullmatch(value)
    if whole_match:
        return _path_value(context, whole_match.group(1).strip())

    def replace(match: re.Match[str]) -> str:
        result = _path_value(context, match.group(1).strip())
        if result is None:
            return ""
        return str(result)

    return _REFERENCE.sub(replace, value)


def _node_input_mapping(
    config: dict[str, Any],
    runtime_context: dict[str, Any],
) -> dict[str, Any]:
    raw_mapping = config.get("input_mapping", config.get("context", {}))
    rendered = _render(raw_mapping or {}, runtime_context)
    return rendered if isinstance(rendered, dict) else {}


def _with_output_mapping(
    node: ScenarioNode,
    raw_result: dict[str, Any] | Any,
) -> dict[str, Any] | Any:
    mapping = (node.config_json or {}).get("output_mapping") or {}
    if not isinstance(mapping, dict) or not mapping:
        return raw_result

    result = dict(raw_result) if isinstance(raw_result, dict) else {"value": raw_result}
    output_context: dict[str, Any] = {"response": raw_result, "result": raw_result}
    if isinstance(raw_result, dict):
        output_context.update(raw_result)
    aliases = _render(mapping, output_context)
    if isinstance(aliases, dict):
        for key, value in aliases.items():
            normalized_key = str(key).strip()
            if normalized_key:
                result[normalized_key] = value
    return result


def _complete_workflow_end_output(
    configured_output: dict[str, Any],
    previous_output: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep custom end fields without losing the workflow's overall verdict."""

    result = dict(configured_output)
    previous = previous_output or {}
    if result.get("result") in (None, ""):
        for key in ("result", "confidence", "score", "reason"):
            if result.get(key) in (None, "") and key in previous:
                result[key] = previous[key]
    classification = previous.get("classification")
    if isinstance(classification, dict):
        result.setdefault("classification", classification)
        result.setdefault("predicted_label", classification.get("top1_label"))
    elif previous.get("predicted_label"):
        result.setdefault("predicted_label", previous["predicted_label"])
    if result.get("result") in (None, ""):
        result["result"] = "UNCERTAIN"
    return result


def _trace_safe(value: Any) -> Any:
    """Preserve useful execution facts without exposing configured credentials."""

    if isinstance(value, dict):
        return {
            str(key): (
                "***"
                if any(keyword in str(key).lower() for keyword in _TRACE_SENSITIVE_KEYWORDS)
                else _trace_safe(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_trace_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_trace_safe(item) for item in value]
    return value


def _append_trace(
    runtime_context: dict[str, Any],
    *,
    node_key: str,
    node_name: str,
    node_type: str,
    started_at: float,
    node_input: Any,
    output: Any,
) -> None:
    runtime_context.setdefault("traces", []).append(
        {
            "node_key": node_key,
            "node_name": node_name,
            "node_type": node_type,
            "status": (
                "COMPLETED"
                if isinstance(output, dict) and output.get("result") is None and output.get("status") == "COMPLETED"
                else _normalize_result(output.get("result") if isinstance(output, dict) else None)
            ),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "input": _trace_safe(node_input),
            "output": _trace_safe(output),
        }
    )


def _resolve_node_image_path(
    *,
    node_context: dict[str, Any],
    config: dict[str, Any],
    render_context: dict[str, Any],
    fallback_image_path: str,
) -> str:
    mapped = node_context.get("image_path") or node_context.get("image")
    configured = _render(config.get("image_path"), render_context)
    candidate = mapped or configured or fallback_image_path
    if not isinstance(candidate, (str, Path)) or not str(candidate).strip():
        raise ScenarioRuntimeError("节点未获得可读取的图片路径。")
    return str(candidate)


def _parse_crop_bbox(raw_bbox: Any) -> tuple[float, float, float, float]:
    value = raw_bbox
    if isinstance(value, dict):
        value = value.get("bbox", value.get("xyxy", value.get("coordinates")))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ScenarioRuntimeError("图片裁剪节点尚未配置 bbox。")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = [item.strip() for item in text.strip("[]() ").split(",")]
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        raise ScenarioRuntimeError("图片裁剪节点的 bbox 必须是 [x1, y1, x2, y2]。")
    try:
        coordinates = tuple(float(item) for item in value[:4])
    except (TypeError, ValueError) as exc:
        raise ScenarioRuntimeError("图片裁剪节点的 bbox 坐标必须是数字。") from exc
    if not all(math.isfinite(item) for item in coordinates):
        raise ScenarioRuntimeError("图片裁剪节点的 bbox 坐标必须是有限数字。")
    return coordinates  # type: ignore[return-value]


def _as_annotation_objects(raw_objects: Any, raw_bbox: Any) -> list[dict[str, Any]]:
    """Normalize detector objects or a single bbox for the image-annotation node."""

    objects_value = raw_objects
    if isinstance(objects_value, dict) and isinstance(objects_value.get("objects"), list):
        objects_value = objects_value["objects"]
    if isinstance(objects_value, dict) and objects_value.get("bbox") is not None:
        objects_value = [objects_value]
    if isinstance(objects_value, (tuple, list)) and len(objects_value) == 4 and all(
        isinstance(value, (int, float, str)) for value in objects_value
    ):
        objects_value = [{"bbox": objects_value}]

    normalized: list[dict[str, Any]] = []
    if isinstance(objects_value, (list, tuple)):
        for index, item in enumerate(objects_value):
            if isinstance(item, dict):
                bbox_value = item.get("bbox", item.get("xyxy", item.get("coordinates")))
                if bbox_value in (None, "", []):
                    continue
                try:
                    bbox = _parse_crop_bbox(bbox_value)
                except ScenarioRuntimeError:
                    continue
                normalized.append(
                    {
                        "index": item.get("index", index),
                        "bbox": bbox,
                        "label": item.get("label", item.get("class_name", "")),
                        "confidence": item.get("confidence", item.get("score")),
                        "mask": item.get("mask", item.get("polygon")),
                        "mask_format": item.get("mask_format", item.get("polygon_format", "")),
                    }
                )
            else:
                try:
                    bbox = _parse_crop_bbox(item)
                except ScenarioRuntimeError:
                    continue
                normalized.append({"index": index, "bbox": bbox, "label": "", "confidence": None})

    if not normalized and raw_bbox not in (None, "", []):
        bbox = _parse_crop_bbox(raw_bbox)
        normalized.append({"index": len(normalized), "bbox": bbox, "label": "", "confidence": None})
    return normalized


def _uploads_asset_url(path: Path) -> str | None:
    try:
        relative = path.resolve().relative_to((PROJECT_ROOT / "uploads").resolve())
    except ValueError:
        return None
    return f"/files/{relative.as_posix()}"


def _normalize_result(value: Any) -> str:
    raw = str(value or "UNCERTAIN").strip().upper()
    aliases = {
        "PASS": "OK",
        "TRUE": "OK",
        "FAIL": "NG",
        "FALSE": "NG",
        "UNKNOWN": "UNCERTAIN",
    }
    return aliases.get(raw, raw if raw in {"OK", "NG", "UNCERTAIN", "ERROR"} else "UNCERTAIN")


def _node_prompt(node: ScenarioNode, version: InspectionScenarioVersion, context: dict[str, Any]) -> str:
    config = node.config_json or {}
    template = config.get("prompt") or version.prompt_template
    if not template:
        raise ScenarioRuntimeError(f"VLM 节点 {node.name} 尚未配置提示词。")
    rendered = _render(template, context)
    return str(rendered)


def _select_vlm(
    database: Session,
    version: InspectionScenarioVersion,
    node: ScenarioNode | None = None,
) -> VlmModelConfig:
    config = node.config_json if node else {}
    model_id = (config or {}).get("vlm_model_id") or version.primary_vlm_model_id
    if not model_id:
        raise ScenarioRuntimeError("场景未配置 VLM 模型。")
    model = database.get(VlmModelConfig, int(model_id))
    if model is None or not model.enabled or model.is_deleted:
        raise ScenarioRuntimeError("所选 VLM 模型不存在或未启用。")
    return model


def _select_published_vision_model(
    database: Session,
    node: ScenarioNode,
):
    raw_version_id = (node.config_json or {}).get("model_version_id")
    try:
        version_id = int(raw_version_id)
    except (TypeError, ValueError) as exc:
        raise ScenarioRuntimeError(
            f"模型节点 {node.name} 尚未选择已发布模型版本。"
        ) from exc
    version = database.scalar(
        select(VisionModelVersion)
        .options(selectinload(VisionModelVersion.vision_model))
        .where(VisionModelVersion.id == version_id)
    )
    if version is None or version.is_deleted or version.status not in {"PUBLISHED", "ARCHIVED"}:
        raise ScenarioRuntimeError(
            f"模型节点 {node.name} 引用的模型版本不存在或尚未发布。"
        )
    model: VisionModel | None = version.vision_model
    if model is None or model.is_deleted or not model.enabled:
        raise ScenarioRuntimeError(f"模型节点 {node.name} 的训练模型不存在或已停用。")
    return build_published_model_spec(version, model)


def _workflow_order(scenario_version: InspectionScenarioVersion) -> list[ScenarioNode]:
    """Return a stable topological order for the canvas graph.

    Old linear flows did not persist meaningful edges.  The fallback sort keeps
    those historical versions executable while newly designed flows follow their
    explicit canvas connections.
    """

    nodes = [node for node in scenario_version.nodes if node.enabled]
    if not nodes:
        return []
    node_by_key = {node.node_key: node for node in nodes}
    indegree = {node.node_key: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node.node_key: [] for node in nodes}
    for edge in scenario_version.edges:
        if edge.source_node_key in node_by_key and edge.target_node_key in node_by_key:
            outgoing[edge.source_node_key].append(edge.target_node_key)
            indegree[edge.target_node_key] += 1
    ready = sorted(
        (node for node in nodes if indegree[node.node_key] == 0),
        key=lambda item: (item.sort_order, item.id),
    )
    ordered: list[ScenarioNode] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for target_key in outgoing[node.node_key]:
            indegree[target_key] -= 1
            if indegree[target_key] == 0:
                ready.append(node_by_key[target_key])
                ready.sort(key=lambda item: (item.sort_order, item.id))
    if len(ordered) != len(nodes):
        raise ScenarioRuntimeError("工作流存在闭环或无法排序的节点连线。")
    return ordered


def _edge_is_active(
    mapping: dict[str, Any],
    source_result: dict[str, Any] | None,
) -> bool:
    if isinstance(source_result, dict) and source_result.get("result") == "SKIPPED":
        return False
    expected_branch = (mapping or {}).get("branch")
    if expected_branch in {None, ""}:
        return True
    actual_branch = str((source_result or {}).get("branch", "")).lower()
    return actual_branch == str(expected_branch).lower()


def _evaluate_condition(
    *,
    actual: Any,
    expected: Any,
    operator: str,
) -> tuple[bool | None, str | None]:
    operator = str(operator or "EQUALS").upper()
    if operator == "EQUALS":
        return actual == expected, None
    if operator == "CONTAINS":
        return str(expected) in str(actual), None
    if operator == "EXISTS":
        return bool(actual) is bool(expected), None
    if operator in {"NUMBER_EQUALS", "NUMBER_GT", "NUMBER_GTE", "NUMBER_LT", "NUMBER_LTE"}:
        try:
            actual_number = float(actual)
            expected_number = float(expected)
        except (TypeError, ValueError):
            return None, "数值规则需要可转换为数字的实际值与期望值。"
        comparisons = {
            "NUMBER_EQUALS": actual_number == expected_number,
            "NUMBER_GT": actual_number > expected_number,
            "NUMBER_GTE": actual_number >= expected_number,
            "NUMBER_LT": actual_number < expected_number,
            "NUMBER_LTE": actual_number <= expected_number,
        }
        return comparisons[operator], None
    return None, f"不支持的规则运算符：{operator}"


class ScenarioRuntime:
    async def execute(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        *,
        image_path: str,
        source: str,
        context: dict[str, Any] | None = None,
        detection_task_id: int | None = None,
        roi_id: int | None = None,
        dataset_item_id: int | None = None,
        allow_draft: bool = False,
    ) -> ScenarioExecution:
        if scenario_version.status != "PUBLISHED" and not allow_draft:
            raise ScenarioRuntimeError("只能执行已发布的检测场景版本。")

        input_payload = {
            **(context or {}),
            "original_image_path": (context or {}).get("original_image_path") or image_path,
            "source_image_path": (context or {}).get("source_image_path") or image_path,
            "image_path": image_path,
        }
        execution = ScenarioExecution(
            scenario_version_id=scenario_version.id,
            detection_task_id=detection_task_id,
            roi_id=roi_id,
            dataset_item_id=dataset_item_id,
            source=source,
            status="RUNNING",
            input_json=input_payload,
        )
        database.add(execution)
        database.flush()
        if source == "PRODUCTION":
            database.commit()

        started = time.perf_counter()
        runtime_context: dict[str, Any] = {"input": input_payload, "nodes": {}, "traces": []}
        try:
            output = await self._run_definition(
                database,
                scenario_version,
                image_path=image_path,
                runtime_context=runtime_context,
                execution_id=execution.id,
                debug_preview=source == "SCENE_DESIGN_TEST",
            )
            execution.output_json = {
                "result": output,
                "nodes": runtime_context["nodes"],
                "traces": runtime_context["traces"],
            }
            execution.result = _normalize_result(output.get("result"))
            confidence = output.get("confidence", output.get("score"))
            execution.score = float(confidence) if confidence is not None else None
            execution.status = "COMPLETED"
            if execution.result in {"OK", "NG", "UNCERTAIN"}:
                collected_item_ids = try_collect_roi_for_matching_datasets(
                    database=database,
                    scenario_version_id=scenario_version.id,
                    execution_id=execution.id,
                    roi_id=roi_id,
                    source=source,
                    roi_image_path=image_path,
                )
                if collected_item_ids:
                    execution.output_json["collected_dataset_item_ids"] = collected_item_ids
        except (
            ScenarioRuntimeError,
            VisionModelRuntimeError,
            VlmConfigurationError,
            VlmRequestError,
            SceneS3StorageError,
        ) as exc:
            execution.status = "FAILED"
            execution.result = "ERROR"
            execution.error_message = str(exc)
            execution.output_json = {
                "nodes": runtime_context["nodes"],
                "traces": runtime_context["traces"],
            }
        except Exception as exc:  # Keep production detection fact visible without crashing the task log.
            execution.status = "FAILED"
            execution.result = "ERROR"
            execution.error_message = f"场景运行异常：{exc}"
            execution.output_json = {
                "nodes": runtime_context["nodes"],
                "traces": runtime_context["traces"],
            }
        finally:
            execution.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            execution.completed_at = datetime.utcnow()
            database.flush()
            if source == "PRODUCTION":
                database.commit()
        return execution

    async def _run_definition(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        *,
        image_path: str,
        runtime_context: dict[str, Any],
        execution_id: int | None = None,
        debug_preview: bool = False,
    ) -> dict[str, Any]:
        nodes = _workflow_order(scenario_version)
        if scenario_version.scenario.mode == "VLM_DIRECT" or not nodes:
            node_started = time.perf_counter()
            model = _select_vlm(database, scenario_version)
            prompt = _render(scenario_version.prompt_template or "", runtime_context)
            if not prompt:
                raise ScenarioRuntimeError("直接 VLM 场景尚未配置提示词。")
            request_overrides = _render(
                (scenario_version.definition_json or {}).get("vlm_parameters", {}),
                runtime_context,
            )
            trace_input = {
                "image_path": image_path,
                "prompt": str(prompt),
                "context": runtime_context["input"],
                "parameters": request_overrides if isinstance(request_overrides, dict) else {},
            }
            try:
                output = await vlm_client.judge(
                    model,
                    prompt=str(prompt),
                    image_path=image_path,
                    context=runtime_context["input"],
                    system_prompt=INSPECTION_VLM_SYSTEM_PROMPT,
                    request_overrides=(
                        request_overrides if isinstance(request_overrides, dict) else {}
                    ),
                )
            except Exception as exc:
                _append_trace(
                    runtime_context,
                    node_key="direct_vlm",
                    node_name="VLM 检测",
                    node_type="VLM",
                    started_at=node_started,
                    node_input=trace_input,
                    output={"result": "ERROR", "reason": str(exc)},
                )
                raise
            runtime_context["nodes"]["direct_vlm"] = output
            _append_trace(
                runtime_context,
                node_key="direct_vlm",
                node_name="VLM 检测",
                node_type="VLM",
                started_at=node_started,
                node_input=trace_input,
                output=output,
            )
            return output

        final_output: dict[str, Any] | None = None
        quality_output: dict[str, Any] | None = None
        last_node_type: str | None = None
        incoming_edges: dict[str, list[Any]] = {node.node_key: [] for node in nodes}
        for edge in scenario_version.edges:
            if edge.target_node_key in incoming_edges:
                incoming_edges[edge.target_node_key].append(edge)
        for node in nodes:
            node_type = node.node_type.upper()
            node_started = time.perf_counter()
            trace_input: Any = {}
            incoming = incoming_edges.get(node.node_key, [])
            if node_type != "START" and incoming:
                active_incoming = [
                    edge
                    for edge in incoming
                    if _edge_is_active(
                        edge.mapping_json or {},
                        runtime_context["nodes"].get(edge.source_node_key),
                    )
                ]
                if not active_incoming:
                    skipped = {
                        "result": "SKIPPED",
                        "reason": "未命中进入该节点的流程分支。",
                    }
                    runtime_context["nodes"][node.node_key] = skipped
                    _append_trace(
                        runtime_context,
                        node_key=node.node_key,
                        node_name=node.name,
                        node_type=node_type,
                        started_at=node_started,
                        node_input={},
                        output=skipped,
                    )
                    continue
            try:
                if node_type == "START":
                    trace_input = dict(runtime_context["input"])
                    result: dict[str, Any] = dict(runtime_context["input"])
                elif node_type == "VLM":
                    config = node.config_json or {}
                    node_context = _node_input_mapping(config, runtime_context)
                    node_runtime_context = {**runtime_context, "params": node_context}
                    node_image_path = _resolve_node_image_path(
                        node_context=node_context,
                        config=config,
                        render_context=node_runtime_context,
                        fallback_image_path=image_path,
                    )
                    referenced_version_id = config.get("referenced_scenario_version_id")
                    referenced_version: InspectionScenarioVersion | None = None
                    if referenced_version_id:
                        referenced_version = database.scalar(
                            select(InspectionScenarioVersion)
                            .options(selectinload(InspectionScenarioVersion.scenario))
                            .where(InspectionScenarioVersion.id == int(referenced_version_id))
                        )
                        if (
                            referenced_version is None
                            or referenced_version.is_deleted
                            or referenced_version.status != "PUBLISHED"
                            or referenced_version.scenario.mode != "VLM_DIRECT"
                        ):
                            raise ScenarioRuntimeError(
                                f"VLM 节点 {node.name} 引用的已发布 VLM 场景不可用。"
                            )
                    model = _select_vlm(
                        database,
                        referenced_version or scenario_version,
                        None if referenced_version else node,
                    )
                    prompt = (
                        _render(referenced_version.prompt_template or "", node_runtime_context)
                        if referenced_version
                        else _node_prompt(node, scenario_version, node_runtime_context)
                    )
                    node_vlm_parameters = _render(
                        (
                            (referenced_version.definition_json or {}).get("vlm_parameters", {})
                            if referenced_version
                            else config.get("vlm_parameters", {})
                        ),
                        node_runtime_context,
                    )
                    trace_input = {
                        "image_path": node_image_path,
                        "prompt": prompt,
                        "context": node_context,
                        "parameters": (
                            node_vlm_parameters
                            if isinstance(node_vlm_parameters, dict)
                            else {}
                        ),
                    }
                    result = await vlm_client.judge(
                        model,
                        prompt=prompt,
                        image_path=node_image_path,
                        context=node_context,
                        system_prompt=INSPECTION_VLM_SYSTEM_PROMPT,
                        request_overrides=(
                            node_vlm_parameters
                            if isinstance(node_vlm_parameters, dict)
                            else {}
                        ),
                    )
                elif node_type == "VISION_MODEL":
                    model_spec = _select_published_vision_model(database, node)
                    config = node.config_json or {}
                    node_context = _node_input_mapping(config, runtime_context)
                    node_runtime_context = {**runtime_context, "params": node_context}
                    node_image_path = _resolve_node_image_path(
                        node_context=node_context,
                        config=config,
                        render_context=node_runtime_context,
                        fallback_image_path=image_path,
                    )
                    node_parameters = _render(
                        {
                            key: value
                            for key, value in config.items()
                            if key not in {
                                "context", "input_mapping", "output_mapping",
                                "expected_label", "expected_class", "expected_count",
                                "expected_min_count", "expected_max_count",
                            }
                        },
                        node_runtime_context,
                    )
                    trace_input = {
                        "image_path": node_image_path,
                        "model_version_id": model_spec.version_id,
                        "model_code": model_spec.model_code,
                        "task_type": model_spec.task_type,
                        "mapped_inputs": node_context,
                        "parameters": (
                            node_parameters if isinstance(node_parameters, dict) else {}
                        ),
                    }
                    if str(settings.yolo_inference_mode or "LOCAL").upper() == "REMOTE":
                        result = await run_remote_yolo_inference(
                            model_spec,
                            node_image_path,
                            config=(
                                node_parameters
                                if isinstance(node_parameters, dict)
                                else {}
                            ),
                        )
                    else:
                        result = await asyncio.to_thread(
                            run_yolo_inference,
                            model_spec,
                            node_image_path,
                            config=(
                                node_parameters
                                if isinstance(node_parameters, dict)
                                else {}
                            ),
                        )
                    result.pop("result", None)
                    result.pop("reason", None)
                    result.setdefault("status", "COMPLETED")
                    result["output_profile"] = task_output_profile(model_spec.task_type)
                    if debug_preview and model_spec.task_type in {"YOLO_DETECTION", "YOLO_SEGMENTATION"}:
                        try:
                            preview = await asyncio.to_thread(
                                self._execute_image_annotate,
                                node_image_path,
                                result.get("objects", []),
                                None,
                                {},
                                execution_id=execution_id,
                                node_key=f"{node.node_key}_preview",
                            )
                            result["preview_image_path"] = preview["image_path"]
                            result["preview_image_url"] = preview["image_url"]
                        except ScenarioRuntimeError as exc:
                            result["preview_error"] = str(exc)
                elif node_type == "IMAGE_CROP":
                    config = node.config_json or {}
                    node_context = _node_input_mapping(config, runtime_context)
                    node_runtime_context = {**runtime_context, "params": node_context}
                    source_image_path = _resolve_node_image_path(
                        node_context=node_context,
                        config=config,
                        render_context=node_runtime_context,
                        fallback_image_path=image_path,
                    )
                    raw_bbox = node_context.get("bbox")
                    if raw_bbox in (None, "", []):
                        raw_bbox = _render(config.get("bbox"), node_runtime_context)
                    batch_mode = bool(config.get("objects")) or "objects" in node_context
                    raw_objects = node_context.get("objects") if "objects" in node_context else _render(
                        config.get("objects"), node_runtime_context
                    )
                    raw_padding = node_context.get("padding_ratio")
                    if raw_padding in (None, ""):
                        raw_padding = _render(
                            config.get("padding_ratio", 0.05),
                            node_runtime_context,
                        )
                    trace_input = {
                        "image_path": source_image_path,
                        "bbox": raw_bbox,
                        "object_count": len(raw_objects) if batch_mode and isinstance(raw_objects, list) else None,
                        "padding_ratio": raw_padding,
                    }
                    result = await asyncio.to_thread(
                        self._execute_image_crop,
                        source_image_path,
                        raw_bbox,
                        raw_padding,
                        execution_id=execution_id,
                        node_key=node.node_key,
                        raw_objects=raw_objects,
                        batch_mode=batch_mode,
                    )
                elif node_type == "IMAGE_ANNOTATE":
                    config = node.config_json or {}
                    node_context = _node_input_mapping(config, runtime_context)
                    node_runtime_context = {**runtime_context, "params": node_context}
                    source_image_path = _resolve_node_image_path(
                        node_context=node_context,
                        config=config,
                        render_context=node_runtime_context,
                        fallback_image_path=image_path,
                    )
                    raw_objects = node_context.get("objects")
                    if raw_objects in (None, "", []):
                        raw_objects = _render(config.get("objects"), node_runtime_context)
                    raw_bbox = node_context.get("bbox")
                    if raw_bbox in (None, "", []):
                        raw_bbox = _render(config.get("bbox"), node_runtime_context)
                    trace_input = {
                        "image_path": source_image_path,
                        "objects": raw_objects,
                        "bbox": raw_bbox,
                        "draw_masks": bool(config.get("draw_masks", True)),
                        "show_label": bool(config.get("show_label", True)),
                        "show_confidence": bool(config.get("show_confidence", True)),
                    }
                    result = await asyncio.to_thread(
                        self._execute_image_annotate,
                        source_image_path,
                        raw_objects,
                        raw_bbox,
                        config,
                        execution_id=execution_id,
                        node_key=node.node_key,
                    )
                elif node_type == "RULE":
                    result = self._execute_rule(node, runtime_context)
                    trace_input = {
                        "actual": result.get("actual"),
                        "expected": result.get("expected"),
                        "operator": result.get("operator"),
                    }
                elif node_type == "IF":
                    result = self._execute_if(node, runtime_context)
                    trace_input = {
                        "actual": result.get("actual"),
                        "expected": result.get("expected"),
                        "operator": result.get("operator"),
                    }
                elif node_type == "WEB_API":
                    trace_input = _node_input_mapping(node.config_json or {}, runtime_context)
                    result = await self._execute_web_api(node, runtime_context)
                elif node_type == "S3_UPLOAD":
                    config = node.config_json or {}
                    node_context = _node_input_mapping(config, runtime_context)
                    node_runtime_context = {**runtime_context, "params": node_context}
                    source_image_path = node_context.get("image_path") or _render(
                        config.get("image_path"), node_runtime_context
                    )
                    if not isinstance(source_image_path, str) or not source_image_path.strip():
                        raise ScenarioRuntimeError("S3 上传节点未获得可读取的图片路径。")
                    requested_key = runtime_context["input"].get("result_s3_key") or _render(
                        config.get("key"), node_runtime_context
                    )
                    requested_key = str(requested_key).strip() if requested_key not in (None, "") else None
                    scene_code = scenario_version.scenario.code
                    loop_index = runtime_context.get("loop_item_index")
                    if loop_index is not None:
                        if requested_key:
                            stem, extension = requested_key.rsplit(".", 1)
                            requested_key = f"{stem}_item_{loop_index}.{extension}"
                        else:
                            safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", scene_code)
                            requested_key = f"{safe_code}/{datetime.utcnow():%Y/%m/%d}/{execution_id or uuid4().hex}_item_{loop_index}_result.jpg"
                    trace_input = {"image_path": source_image_path, "key": requested_key}
                    s3_uri = await asyncio.to_thread(
                        SceneS3Storage().publish_result,
                        Path(source_image_path),
                        scene_code=scene_code,
                        execution_id=execution_id,
                        requested_key=requested_key,
                    )
                    result = {"status": "COMPLETED", "s3_uri": s3_uri, "result_image_path": s3_uri}
                elif node_type in {"LOOP", "ITERATION", "CONDITION_LOOP"}:
                    trace_input = {
                        "items_reference": (node.config_json or {}).get("items"),
                        "operation": (
                            "CONDITION_LOOP" if node_type == "CONDITION_LOOP"
                            else (node.config_json or {}).get("operation", "PASS_THROUGH")
                        ),
                    }
                    result = (
                        await self._execute_condition_loop(database, scenario_version, node, runtime_context)
                        if node_type == "CONDITION_LOOP"
                        else await self._execute_loop(database, scenario_version, node, runtime_context)
                    )
                elif node_type == "END":
                    configured_output = (node.config_json or {}).get("output")
                    trace_input = {"output": configured_output}
                    resolved = _render(configured_output, runtime_context) if configured_output else None
                    if isinstance(resolved, dict):
                        result = _complete_workflow_end_output(
                            resolved,
                            quality_output,
                        )
                    else:
                        previous = dict(final_output or {})
                        if last_node_type in {"VISION_MODEL", "IMAGE_CROP", "IMAGE_ANNOTATE"}:
                            previous.pop("result", None)
                            previous.pop("reason", None)
                        result = _complete_workflow_end_output(previous, quality_output)
                    final_output = result
                else:
                    result = {
                        "result": "ERROR",
                        "reason": f"不支持的节点类型：{node.node_type}",
                    }
                result = _with_output_mapping(node, result)
            except Exception as exc:
                _append_trace(
                    runtime_context,
                    node_key=node.node_key,
                    node_name=node.name,
                    node_type=node_type,
                    started_at=node_started,
                    node_input=trace_input,
                    output={"result": "ERROR", "reason": str(exc)},
                )
                raise
            runtime_context["nodes"][node.node_key] = result
            _append_trace(
                runtime_context,
                node_key=node.node_key,
                node_name=node.name,
                node_type=node_type,
                started_at=node_started,
                node_input=trace_input,
                output=result,
            )
            if isinstance(result, dict):
                final_output = result
                last_node_type = node_type
                if (
                    node_type in {"VLM", "RULE", "IF", "WEB_API"}
                    or (
                        node_type in {"LOOP", "ITERATION", "CONDITION_LOOP"}
                        and (
                            node_type in {"ITERATION", "CONDITION_LOOP"}
                            or (node.config_json or {}).get("operation") in {"VLM", "SUBFLOW"}
                        )
                        and (
                            (node.config_json or {}).get("aggregate", "ALL_OK") != "COLLECT_ONLY"
                            or result.get("result") == "ERROR"
                        )
                    )
                ) and result.get("result") is not None:
                    quality_output = result

        return final_output or {"result": "UNCERTAIN", "reason": "工作流未产生输出。"}

    def _execute_image_crop(
        self,
        image_path: str,
        raw_bbox: Any,
        raw_padding: Any,
        *,
        execution_id: int | None,
        node_key: str,
        raw_objects: Any = None,
        batch_mode: bool = False,
    ) -> dict[str, Any]:
        """Crop one bbox or each detector object into stable local image artifacts."""

        source_path = Path(image_path)
        if not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path
        if not source_path.is_file():
            raise ScenarioRuntimeError("图片裁剪节点无法读取输入图片。")
        if batch_mode:
            objects = raw_objects.get("objects") if isinstance(raw_objects, dict) else raw_objects
            if not isinstance(objects, list):
                raise ScenarioRuntimeError("批量裁剪的目标列表必须是数组。")
            if len(objects) > _MAX_BATCH_CROPS:
                raise ScenarioRuntimeError(f"批量裁剪最多支持 {_MAX_BATCH_CROPS} 个目标，请先筛选目标。")
        else:
            objects = [{"bbox": raw_bbox}]
        try:
            padding_ratio = float(raw_padding if raw_padding not in (None, "") else 0.05)
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("图片裁剪节点的边缘扩展比例必须是数字。") from exc
        if not math.isfinite(padding_ratio) or not 0 <= padding_ratio <= 1:
            raise ScenarioRuntimeError("图片裁剪节点的边缘扩展比例必须在 0 到 1 之间。")

        directory_name = str(execution_id) if execution_id is not None else "preview"
        output_directory = PROJECT_ROOT / "uploads" / "scene_crops" / directory_name
        output_directory.mkdir(parents=True, exist_ok=True)
        crops: list[dict[str, Any]] = []
        try:
            with Image.open(source_path) as original:
                image = ImageOps.exif_transpose(original)
                width, height = image.size
                for index, item in enumerate(objects):
                    bbox_value = item.get("bbox", item.get("xyxy")) if isinstance(item, dict) else item
                    x1, y1, x2, y2 = _parse_crop_bbox(bbox_value)
                    if x1 == x2 or y1 == y2:
                        raise ScenarioRuntimeError(f"第 {index + 1} 个裁剪框没有有效面积。")
                    left, right = sorted((x1, x2))
                    top, bottom = sorted((y1, y2))
                    padding_x = (right - left) * padding_ratio
                    padding_y = (bottom - top) * padding_ratio
                    crop_left = max(0, math.floor(left - padding_x))
                    crop_top = max(0, math.floor(top - padding_y))
                    crop_right = min(width, math.ceil(right + padding_x))
                    crop_bottom = min(height, math.ceil(bottom + padding_y))
                    if crop_right <= crop_left or crop_bottom <= crop_top:
                        raise ScenarioRuntimeError(f"第 {index + 1} 个裁剪框超出图片有效区域。")
                    cropped = image.crop((crop_left, crop_top, crop_right, crop_bottom))
                    if cropped.mode != "RGB":
                        cropped = cropped.convert("RGB")
                    output_path = output_directory / f"{node_key}_{index}_{uuid4().hex[:12]}.jpg"
                    cropped.save(output_path, format="JPEG", quality=95, optimize=True)
                    crops.append({
                        "index": index,
                        "label": item.get("label", item.get("class_name", "")) if isinstance(item, dict) else "",
                        "confidence": item.get("confidence", item.get("score")) if isinstance(item, dict) else None,
                        "bbox": [round(value, 2) for value in (x1, y1, x2, y2)],
                        "image_path": str(output_path),
                        "image_url": _uploads_asset_url(output_path),
                        "source_image_path": str(source_path),
                        "crop_bbox": [crop_left, crop_top, crop_right, crop_bottom],
                        "width": crop_right - crop_left,
                        "height": crop_bottom - crop_top,
                    })
        except ScenarioRuntimeError:
            raise
        except Exception as exc:
            raise ScenarioRuntimeError(f"图片裁剪失败：{exc}") from exc

        if batch_mode:
            return {
                "result": "OK" if crops else "UNCERTAIN",
                "crops": crops,
                "count": len(crops),
                "source_image_path": str(source_path),
                "padding_ratio": padding_ratio,
            }
        crop = crops[0]
        return {
            "result": "OK",
            "image_path": crop["image_path"],
            "image_url": crop["image_url"],
            "source_image_path": crop["source_image_path"],
            "requested_bbox": crop["bbox"],
            "crop_bbox": crop["crop_bbox"],
            "width": crop["width"],
            "height": crop["height"],
            "padding_ratio": padding_ratio,
        }

    def _execute_image_annotate(
        self,
        image_path: str,
        raw_objects: Any,
        raw_bbox: Any,
        config: dict[str, Any],
        *,
        execution_id: int | None,
        node_key: str,
    ) -> dict[str, Any]:
        """Draw detector boxes (and segmentation polygons when available) onto an image."""

        source_path = Path(image_path)
        if not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path
        if not source_path.is_file():
            raise ScenarioRuntimeError("图片画框节点无法读取输入图片。")
        annotations = _as_annotation_objects(raw_objects, raw_bbox)
        try:
            line_width = int(config.get("line_width", 3))
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("图片画框节点的线宽必须是整数。") from exc
        line_width = max(1, min(line_width, 20))
        draw_masks = bool(config.get("draw_masks", True))
        show_label = bool(config.get("show_label", True))
        show_confidence = bool(config.get("show_confidence", True))
        label_prefix = str(config.get("label_prefix") or "").strip()
        colors = ((41, 128, 255), (23, 164, 104), (240, 129, 43), (168, 85, 247), (225, 59, 77))

        try:
            with Image.open(source_path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                width, height = image.size
                draw = ImageDraw.Draw(image, "RGBA")
                try:
                    font = ImageFont.load_default()
                except Exception:
                    font = None
                rendered: list[dict[str, Any]] = []
                for position, annotation in enumerate(annotations):
                    x1, y1, x2, y2 = annotation["bbox"]
                    left, right = sorted((max(0, x1), min(width, x2)))
                    top, bottom = sorted((max(0, y1), min(height, y2)))
                    if right <= left or bottom <= top:
                        continue
                    color = colors[position % len(colors)]
                    polygon = annotation.get("mask")
                    if draw_masks and isinstance(polygon, (list, tuple)):
                        points: list[tuple[float, float]] = []
                        for point in polygon:
                            if not isinstance(point, (list, tuple)) or len(point) < 2:
                                continue
                            try:
                                point_x, point_y = float(point[0]), float(point[1])
                            except (TypeError, ValueError):
                                continue
                            if str(annotation.get("mask_format") or "").upper() == "POLYGON_NORMALIZED":
                                point_x *= width
                                point_y *= height
                            points.append((point_x, point_y))
                        if len(points) >= 3:
                            draw.polygon(points, fill=(*color, 52), outline=(*color, 230), width=line_width)
                    draw.rectangle((left, top, right, bottom), outline=(*color, 255), width=line_width)
                    text_parts: list[str] = []
                    if label_prefix:
                        text_parts.append(label_prefix)
                    if show_label and annotation.get("label") not in (None, ""):
                        text_parts.append(str(annotation["label"]))
                    if show_confidence and annotation.get("confidence") not in (None, ""):
                        try:
                            text_parts.append(f"{float(annotation['confidence']):.2f}")
                        except (TypeError, ValueError):
                            text_parts.append(str(annotation["confidence"]))
                    label = " ".join(text_parts)
                    if label:
                        text_y = max(0, top - 18)
                        bbox = draw.textbbox((left + 3, text_y + 2), label, font=font)
                        draw.rectangle((left, text_y, bbox[2] + 4, bbox[3] + 4), fill=(*color, 235))
                        draw.text((left + 3, text_y + 2), label, fill=(255, 255, 255, 255), font=font)
                    rendered.append(
                        {
                            "index": annotation.get("index", position),
                            "label": annotation.get("label") or None,
                            "confidence": annotation.get("confidence"),
                            "bbox": [round(value, 2) for value in (left, top, right, bottom)],
                            "has_mask": bool(annotation.get("mask")),
                        }
                    )
        except ScenarioRuntimeError:
            raise
        except Exception as exc:
            raise ScenarioRuntimeError(f"图片画框失败：{exc}") from exc

        directory_name = str(execution_id) if execution_id is not None else "preview"
        output_directory = PROJECT_ROOT / "uploads" / "scene_annotations" / directory_name
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / f"{node_key}_{uuid4().hex[:12]}.jpg"
        try:
            image.save(output_path, format="JPEG", quality=95, optimize=True)
        except Exception as exc:
            raise ScenarioRuntimeError(f"无法保存图片画框结果：{exc}") from exc
        return {
            "result": "OK",
            "image_path": str(output_path),
            "image_url": _uploads_asset_url(output_path),
            "source_image_path": str(source_path),
            "annotation_count": len(rendered),
            "annotations": rendered,
            "width": width,
            "height": height,
        }

    def _execute_rule(
        self,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        node_context = _node_input_mapping(config, runtime_context)
        render_context = {**runtime_context, "params": node_context}
        actual = _render(config.get("actual"), render_context)
        expected = _render(config.get("expected"), render_context)
        operator = str(config.get("operator", "EQUALS")).upper()
        passed, error = _evaluate_condition(
            actual=actual,
            expected=expected,
            operator=operator,
        )
        if error:
            return {
                "result": "ERROR",
                "reason": error,
                "actual": actual,
                "expected": expected,
                "operator": operator,
            }
        return {
            "result": "OK" if passed else "NG",
            "actual": actual,
            "expected": expected,
            "operator": operator,
        }

    def _execute_if(
        self,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        node_context = _node_input_mapping(config, runtime_context)
        render_context = {**runtime_context, "params": node_context}
        actual = _render(config.get("actual"), render_context)
        expected = _render(config.get("expected"), render_context)
        operator = str(config.get("operator", "EQUALS")).upper()
        passed, error = _evaluate_condition(
            actual=actual,
            expected=expected,
            operator=operator,
        )
        if error:
            return {
                "result": "ERROR",
                "branch": "false",
                "reason": error,
                "actual": actual,
                "expected": expected,
                "operator": operator,
            }
        return {
            "result": "OK" if passed else "NG",
            "branch": "true" if passed else "false",
            "actual": actual,
            "expected": expected,
            "operator": operator,
        }

    async def _execute_web_api(
        self,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        node_context = _node_input_mapping(config, runtime_context)
        render_context = {**runtime_context, "params": node_context}
        url = str(_render(config.get("url", ""), render_context) or "").strip()
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            return {"result": "ERROR", "reason": "Web 接口节点必须配置有效的 http 或 https 地址。"}
        method = str(config.get("method", "POST")).upper()
        headers = _render(config.get("headers", {}), render_context)
        body = _render(config.get("body", {}), render_context)
        request_format = str(config.get("request_format", "JSON")).upper()
        timeout = float(config.get("timeout_seconds", 15))
        request_kwargs: dict[str, Any] = {
            "headers": headers if isinstance(headers, dict) else {},
        }
        if method not in {"GET", "DELETE"}:
            if request_format == "TEXT":
                request_kwargs["content"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                request_kwargs["headers"].setdefault("Content-Type", "text/plain; charset=utf-8")
            else:
                request_kwargs["json"] = body
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, **request_kwargs)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"result": "ERROR", "reason": f"Web 接口调用失败：{exc}"}
        response_format = str(config.get("response_format", "JSON")).upper()
        if response_format == "TEXT":
            payload: Any = response.text
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
        if isinstance(payload, dict):
            normalized = _normalize_result(payload.get("result")) if payload.get("result") is not None else "OK"
            confidence = payload.get("confidence", payload.get("score"))
            return {
                "result": normalized,
                "confidence": confidence,
                "status_code": response.status_code,
                "response": payload,
            }
        return {"result": "OK", "status_code": response.status_code, "response": payload}

    async def _execute_loop(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        node_context = _node_input_mapping(config, runtime_context)
        render_context = {**runtime_context, "params": node_context}
        raw_items = node_context.get("items") if "items" in node_context else _render(config.get("items"), render_context)
        if isinstance(raw_items, str):
            try:
                raw_items = json.loads(raw_items)
            except json.JSONDecodeError:
                raw_items = [raw_items] if raw_items else []
        if not isinstance(raw_items, list):
            raise ScenarioRuntimeError("循环集合必须是数组或可解析为数组的 JSON 文本。")
        try:
            max_iterations = int(config.get("max_iterations", 10))
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("循环最大次数必须是整数。") from exc
        operation = (
            "SUBFLOW" if str(node.node_type or "").upper() == "ITERATION"
            else str(config.get("operation") or "PASS_THROUGH").upper()
        )
        if operation == "SUBFLOW":
            return await self._execute_loop_body(
                database, scenario_version, node, runtime_context,
                raw_items=raw_items, max_iterations=max_iterations,
            )
        if operation == "PASS_THROUGH":
            if not 1 <= max_iterations <= 1000:
                raise ScenarioRuntimeError("循环最大次数必须在 1 到 1000 之间。")
            items = raw_items[:max_iterations]
            return {
                "result": "OK",
                "items": items,
                "count": len(items),
                "item_name": str(config.get("item_name") or "item"),
                "max_iterations": max_iterations,
            }
        if operation != "VLM":
            raise ScenarioRuntimeError(f"不支持的循环执行方式：{operation}")
        if not 1 <= max_iterations <= _MAX_BATCH_CROPS or len(raw_items) > max_iterations:
            raise ScenarioRuntimeError(
                f"循环待处理 {len(raw_items)} 项，超过设置的最大次数 {max_iterations}；"
                "请先筛选目标或调高上限，不能遗漏目标后判 OK。"
            )
        try:
            parallelism = int(config.get("max_parallelism", 3))
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("循环并发数必须是整数。") from exc
        if not 1 <= parallelism <= _MAX_LOOP_VLM_PARALLELISM:
            raise ScenarioRuntimeError("循环并发数必须在 1 到 3 之间。")
        aggregate = str(config.get("aggregate") or "ALL_OK").upper()
        if aggregate not in {"ALL_OK", "ANY_OK", "COLLECT_ONLY"}:
            raise ScenarioRuntimeError("循环汇总方式不受支持。")
        model = _select_vlm(database, scenario_version, node)
        prompt_template = str(config.get("prompt") or "").strip()
        if not prompt_template:
            raise ScenarioRuntimeError("逐项 VLM 循环尚未配置检测提示词。")
        item_name = str(config.get("item_name") or "item").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item_name) or item_name in {"input", "nodes", "params", "traces"}:
            raise ScenarioRuntimeError("循环当前项变量名不合法。")
        semaphore = asyncio.Semaphore(parallelism)

        async def judge_item(index: int, item: Any) -> dict[str, Any]:
            started_at = time.perf_counter()
            if not isinstance(item, dict):
                return {"index": index, "result": "ERROR", "reason": "循环项必须包含 image_path 的对象。"}
            item_context = {**render_context, "item": item, item_name: item}
            image_path = str(item.get("image_path") or "").strip()
            if not image_path:
                return {"index": index, "result": "ERROR", "reason": "循环项缺少裁剪图片路径。"}
            prompt = str(_render(prompt_template, item_context) or "").strip()
            overrides = _render(config.get("vlm_parameters") or {}, item_context)
            if not isinstance(overrides, dict):
                overrides = {}
            try:
                async with semaphore:
                    output = await vlm_client.judge(
                        model,
                        prompt=prompt,
                        image_path=image_path,
                        context={
                            **{key: value for key, value in node_context.items() if key != "items"},
                            "item": {
                                "index": index,
                                "label": item.get("label"),
                                "confidence": item.get("confidence"),
                                "bbox": item.get("bbox"),
                            },
                        },
                        system_prompt=INSPECTION_VLM_SYSTEM_PROMPT,
                        request_overrides=overrides,
                    )
                verdict = _normalize_result(output.get("result")) if isinstance(output, dict) else "UNCERTAIN"
                return {
                    "index": index,
                    "label": item.get("label", ""),
                    "bbox": item.get("bbox"),
                    "image_path": image_path,
                    "image_url": item.get("image_url"),
                    "result": verdict,
                    "confidence": output.get("confidence") if isinstance(output, dict) else None,
                    "reason": output.get("reason") if isinstance(output, dict) else "VLM 未返回结构化结果。",
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "output": output,
                }
            except Exception as exc:
                return {
                    "index": index,
                    "label": item.get("label", ""),
                    "bbox": item.get("bbox"),
                    "image_path": image_path,
                    "image_url": item.get("image_url"),
                    "result": "ERROR",
                    "reason": str(exc),
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                }

        items = await asyncio.gather(*(judge_item(index, item) for index, item in enumerate(raw_items)))
        verdicts = [item["result"] for item in items]
        if not verdicts:
            result = "UNCERTAIN"
        elif "ERROR" in verdicts:
            result = "ERROR"
        elif aggregate == "COLLECT_ONLY":
            result = "UNCERTAIN"
        elif aggregate == "ALL_OK":
            result = "NG" if "NG" in verdicts else "UNCERTAIN" if "UNCERTAIN" in verdicts else "OK"
        else:
            result = "OK" if "OK" in verdicts else "UNCERTAIN" if "UNCERTAIN" in verdicts else "NG"
        return {
            "result": result,
            "reason": f"逐项 VLM 完成 {len(items)} 项：{verdicts.count('OK')} OK、{verdicts.count('NG')} NG、{verdicts.count('UNCERTAIN')} 待确认、{verdicts.count('ERROR')} 错误。",
            "items": items,
            "count": len(items),
            "aggregate": aggregate,
            "max_parallelism": parallelism,
        }

    async def _execute_loop_body(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
        *,
        raw_items: list[Any],
        max_iterations: int,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = node.config_json or {}
        body = config.get("body_nodes")
        if not isinstance(body, list) or not 1 <= len(body) <= _MAX_LOOP_BODY_NODES:
            raise ScenarioRuntimeError("循环体需要添加 1 到 20 个处理节点。")
        if not 1 <= max_iterations <= _MAX_LOOP_BODY_ITERATIONS or len(raw_items) > max_iterations:
            raise ScenarioRuntimeError(
                f"循环待处理 {len(raw_items)} 项，超过设置的最大次数 {max_iterations}；"
                "请先筛选目标或调高上限，不能遗漏目标后判 OK。"
            )
        item_name = str(config.get("item_name") or "item").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item_name) or item_name in {"input", "nodes", "params", "traces", "parent", "state"}:
            raise ScenarioRuntimeError("循环当前项变量名不合法。")
        aggregate = str(config.get("aggregate") or "ALL_OK").upper()
        if aggregate not in {"ALL_OK", "ANY_OK", "COLLECT_ONLY"}:
            raise ScenarioRuntimeError("循环汇总方式不受支持。")
        allowed_types = {"VLM", "VISION_MODEL", "IMAGE_CROP", "IMAGE_ANNOTATE", "RULE", "IF", "WEB_API", "S3_UPLOAD"}
        keys = {"loop_start", "loop_end"}
        body_nodes = [ScenarioNode(id=0, node_key="loop_start", name="循环开始", node_type="START", sort_order=0, config_json={}, enabled=True)]
        for position, item in enumerate(body, start=1):
            if not isinstance(item, dict) or item.get("node_type") not in allowed_types:
                raise ScenarioRuntimeError(f"循环体第 {position} 个节点类型不受支持。")
            key = str(item.get("node_key") or "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in keys:
                raise ScenarioRuntimeError(f"循环体第 {position} 个节点键无效或重复。")
            keys.add(key)
            body_nodes.append(ScenarioNode(
                id=position, node_key=key, name=str(item.get("name") or key),
                node_type=item["node_type"], sort_order=position,
                config_json=item.get("config_json") if isinstance(item.get("config_json"), dict) else {},
                enabled=True,
            ))
        body_nodes.append(ScenarioNode(
            id=len(body) + 1, node_key="loop_end", name="循环结束", node_type="END",
            sort_order=len(body) + 1, config_json={"output": config.get("body_output") or {}}, enabled=True,
        ))
        body_edges = []
        for connection in loop_body_connections(body, config.get("body_edges")):
            if not isinstance(connection, dict):
                raise ScenarioRuntimeError("循环体连线格式无效。")
            source_key = str(connection.get("source_node_key") or "")
            target_key = str(connection.get("target_node_key") or "")
            if source_key not in keys or target_key not in keys:
                raise ScenarioRuntimeError(f"循环体连线 {source_key} → {target_key} 指向不存在的节点。")
            branch = str(connection.get("branch") or "").lower()
            body_edges.append(ScenarioEdge(
                source_node_key=source_key, target_node_key=target_key,
                mapping_json={"branch": branch} if branch else {},
            ))
        body_version = SimpleNamespace(
            nodes=body_nodes, edges=body_edges, scenario=scenario_version.scenario,
            primary_vlm_model_id=scenario_version.primary_vlm_model_id,
            prompt_template=scenario_version.prompt_template,
            definition_json=scenario_version.definition_json,
        )
        items = []
        for index, item in enumerate(raw_items):
            started = time.perf_counter()
            current = {**item, "index": index} if isinstance(item, dict) else {"value": item, "index": index}
            item_image_path = str(current.get("image_path") or runtime_context["input"].get("image_path") or "").strip()
            child_input = {**runtime_context["input"], **current, "image_path": item_image_path, "item": current}
            if state is not None:
                child_input["state"] = state
            child_context = {
                "input": child_input, "item": current, item_name: current,
                "parent": {"input": runtime_context["input"], "nodes": runtime_context["nodes"]},
                "nodes": {}, "traces": [], "loop_item_index": index,
            }
            if state is not None:
                child_context["state"] = state
            try:
                output = await self._run_definition(
                    database, body_version, image_path=item_image_path,
                    runtime_context=child_context,
                )
                verdict = _normalize_result(output.get("result"))
                reason = output.get("reason")
            except Exception as exc:
                output = {"result": "ERROR", "reason": str(exc)}
                verdict = "ERROR"
                reason = str(exc)
            items.append({
                "index": index, "item": current, "result": verdict, "reason": reason,
                "output": output, "nodes": child_context["nodes"], "traces": child_context["traces"],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        verdicts = [item["result"] for item in items]
        if not verdicts:
            result = "UNCERTAIN"
        elif "ERROR" in verdicts:
            result = "ERROR"
        elif aggregate == "COLLECT_ONLY":
            result = "UNCERTAIN"
        elif aggregate == "ALL_OK":
            result = "NG" if "NG" in verdicts else "UNCERTAIN" if "UNCERTAIN" in verdicts else "OK"
        else:
            result = "OK" if "OK" in verdicts else "UNCERTAIN" if "UNCERTAIN" in verdicts else "NG"
        return {
            "result": result, "items": items, "count": len(items), "aggregate": aggregate,
            "reason": f"循环体完成 {len(items)} 项：{verdicts.count('OK')} OK、{verdicts.count('NG')} NG、{verdicts.count('UNCERTAIN')} 待确认、{verdicts.count('ERROR')} 错误。",
        }

    async def _execute_condition_loop(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        try:
            max_rounds = int(config.get("max_iterations", 3))
            timeout_seconds = float(config.get("timeout_seconds", 30))
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("条件循环的轮数和超时必须是数字。") from exc
        if not 1 <= max_rounds <= _MAX_CONDITION_LOOP_ROUNDS or not 0 < timeout_seconds <= 300:
            raise ScenarioRuntimeError("条件循环最多 20 轮，超时必须在 0 到 300 秒之间。")
        state = _render(config.get("initial_state") or {}, runtime_context)
        updates = config.get("state_updates") or {}
        stop = config.get("stop_condition") or {}
        if not isinstance(state, dict) or not isinstance(updates, dict):
            raise ScenarioRuntimeError("条件循环的初始变量和变量更新必须是键值映射。")
        if not isinstance(stop, dict) or not stop.get("actual"):
            raise ScenarioRuntimeError("条件循环必须配置退出条件。")

        started = time.perf_counter()
        rounds: list[dict[str, Any]] = []
        body_node = ScenarioNode(
            node_type="ITERATION",
            config_json={**config, "aggregate": "COLLECT_ONLY", "item_name": "item"},
        )
        for round_index in range(max_rounds):
            if time.perf_counter() - started >= timeout_seconds:
                break
            current_state = {**state, "index": round_index, "image_path": state.get("image_path") or runtime_context["input"].get("image_path")}
            try:
                body_result = await asyncio.wait_for(
                    self._execute_loop_body(
                        database, scenario_version, body_node, runtime_context,
                        raw_items=[current_state], max_iterations=1, state=state,
                    ),
                    timeout=max(0.001, timeout_seconds - (time.perf_counter() - started)),
                )
            except asyncio.TimeoutError:
                return {
                    "result": "ERROR", "reason": "条件循环执行超时。",
                    "state": state, "rounds": rounds, "count": len(rounds), "exit_reason": "TIMEOUT",
                }
            item_result = body_result["items"][0]
            if item_result["result"] == "ERROR":
                rounds.append(item_result)
                return {
                    "result": "ERROR", "reason": item_result.get("reason") or "循环体执行失败。",
                    "state": state, "rounds": rounds, "count": len(rounds), "exit_reason": "CHILD_ERROR",
                }
            render_context = {
                "input": runtime_context["input"], "parent": runtime_context,
                "state": state, "nodes": item_result["nodes"],
                "output": item_result["output"], "round": {"index": round_index, "number": round_index + 1},
            }
            resolved_updates: dict[str, Any] = {}
            for key, raw_update in updates.items():
                if not isinstance(key, str) or not key:
                    raise ScenarioRuntimeError("循环变量名必须是非空字符串。")
                if isinstance(raw_update, dict) and raw_update.get("operation") == "INCREMENT":
                    amount = _render(raw_update.get("value", 1), render_context)
                    try:
                        resolved_updates[key] = float(state.get(key, 0)) + float(amount)
                    except (TypeError, ValueError) as exc:
                        raise ScenarioRuntimeError(f"循环变量“{key}”无法累加非数值内容。") from exc
                elif isinstance(raw_update, dict) and raw_update.get("operation") == "APPEND":
                    previous = state.get(key, [])
                    if not isinstance(previous, list):
                        raise ScenarioRuntimeError(f"循环变量“{key}”不是数组，无法追加。")
                    resolved_updates[key] = [*previous, _render(raw_update.get("value"), render_context)]
                else:
                    resolved_updates[key] = _render(raw_update, render_context)
            state = {**state, **resolved_updates}
            render_context["state"] = state
            actual = _render(stop["actual"], render_context)
            expected = _render(stop.get("expected"), render_context)
            passed, error = _evaluate_condition(
                actual=actual, expected=expected,
                operator=str(stop.get("operator") or "EQUALS"),
            )
            item_result["stop_condition"] = {"actual": actual, "expected": expected, "met": passed}
            rounds.append(item_result)
            if time.perf_counter() - started >= timeout_seconds:
                break
            if error:
                return {
                    "result": "ERROR", "reason": error, "state": state,
                    "rounds": rounds, "count": len(rounds), "exit_reason": "INVALID_CONDITION",
                }
            if passed:
                return {
                    "result": item_result["result"], "reason": "循环退出条件已满足。",
                    "state": state, "rounds": rounds, "count": len(rounds),
                    "exit_reason": "CONDITION_MET", "output": item_result["output"],
                }
        exit_reason = "TIMEOUT" if time.perf_counter() - started >= timeout_seconds else "MAX_ROUNDS"
        return {
            "result": "ERROR", "reason": "条件循环未在限定轮数或时间内满足退出条件。",
            "state": state, "rounds": rounds, "count": len(rounds), "exit_reason": exit_reason,
        }

    async def review(
        self,
        database: Session,
        execution: ScenarioExecution,
        *,
        force: bool = False,
    ) -> ScenarioExecutionReview:
        existing = execution.review
        if existing and existing.status in {"COMPLETED", "RUNNING"} and not force:
            return existing
        version = execution.scenario_version
        review = existing or ScenarioExecutionReview(execution_id=execution.id)
        if existing is None:
            database.add(review)
            database.flush()

        review_model_id = version.review_vlm_model_id
        if not review_model_id:
            review.status = "SKIPPED"
            review.reason = "场景未配置独立 VLM 复核模型。"
            database.flush()
            return review
        if review_model_id == version.primary_vlm_model_id:
            review.status = "SKIPPED"
            review.reason = "复核模型与主 VLM 相同，不作为独立复核执行。"
            database.flush()
            return review
        model = database.get(VlmModelConfig, review_model_id)
        if model is None or not model.enabled or model.is_deleted:
            review.status = "FAILED"
            review.reason = "复核 VLM 模型不存在或未启用。"
            database.flush()
            return review

        review.status = "RUNNING"
        review.review_vlm_model_id = model.id
        database.flush()
        prompt = (
            "请独立复核以下工业视觉检测结论。只依据实测 ROI 图片、规则和原始结果，"
            "不要猜测。输出 JSON：result(OK/NG/UNCERTAIN)、confidence、reason。\n"
            f"原始结果：{execution.result}\n"
            f"原始输出：{execution.output_json}"
        )
        try:
            output = await vlm_client.judge(
                model,
                prompt=prompt,
                image_path=str(execution.input_json.get("image_path", "")),
                context={"scene_version_id": version.id, "raw_result": execution.result},
            )
            review.status = "COMPLETED"
            review.verdict = _normalize_result(output.get("result"))
            confidence = output.get("confidence")
            review.confidence = float(confidence) if confidence is not None else None
            review.reason = str(output.get("reason") or "") or None
            review.raw_output_json = output
        except (VlmConfigurationError, VlmRequestError) as exc:
            review.status = "FAILED"
            review.reason = str(exc)
        review.reviewed_at = datetime.utcnow()
        database.flush()
        return review


scenario_runtime = ScenarioRuntime()
