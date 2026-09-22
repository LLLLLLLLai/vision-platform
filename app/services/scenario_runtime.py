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
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.intelligence import (
    InspectionScenarioVersion,
    ScenarioExecution,
    ScenarioExecutionReview,
    ScenarioNode,
    VlmModelConfig,
    VisionModel,
    VisionModelVersion,
)
from app.core.config import PROJECT_ROOT
from app.services.openai_compatible_vlm import (
    VlmConfigurationError,
    VlmRequestError,
    vlm_client,
)
from app.services.dataset_collection_service import try_collect_roi_for_matching_datasets
from app.services.vision_model_runtime import (
    VisionModelRuntimeError,
    build_published_model_spec,
    run_yolo_inference,
)


class ScenarioRuntimeError(RuntimeError):
    pass


_REFERENCE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_TRACE_SENSITIVE_KEYWORDS = ("authorization", "api_key", "apikey", "token", "password", "secret")


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
            "status": _normalize_result(output.get("result") if isinstance(output, dict) else None),
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
    if version is None or version.is_deleted or version.status != "PUBLISHED":
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

        started = time.perf_counter()
        runtime_context: dict[str, Any] = {"input": input_payload, "nodes": {}, "traces": []}
        try:
            output = await self._run_definition(
                database,
                scenario_version,
                image_path=image_path,
                runtime_context=runtime_context,
                execution_id=execution.id,
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
        return execution

    async def _run_definition(
        self,
        database: Session,
        scenario_version: InspectionScenarioVersion,
        *,
        image_path: str,
        runtime_context: dict[str, Any],
        execution_id: int | None = None,
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
                    node_parameters = _render(config, node_runtime_context)
                    trace_input = {
                        "image_path": node_image_path,
                        "model_version_id": model_spec.version_id,
                        "model_code": model_spec.model_code,
                        "task_type": model_spec.task_type,
                        "parameters": (
                            node_parameters if isinstance(node_parameters, dict) else {}
                        ),
                    }
                    result = await asyncio.to_thread(
                        run_yolo_inference,
                        model_spec,
                        node_image_path,
                        config=node_parameters if isinstance(node_parameters, dict) else {},
                    )
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
                    raw_padding = node_context.get("padding_ratio")
                    if raw_padding in (None, ""):
                        raw_padding = _render(
                            config.get("padding_ratio", 0.05),
                            node_runtime_context,
                        )
                    trace_input = {
                        "image_path": source_image_path,
                        "bbox": raw_bbox,
                        "padding_ratio": raw_padding,
                    }
                    result = await asyncio.to_thread(
                        self._execute_image_crop,
                        source_image_path,
                        raw_bbox,
                        raw_padding,
                        execution_id=execution_id,
                        node_key=node.node_key,
                    )
                elif node_type == "RULE":
                    trace_input = _node_input_mapping(node.config_json or {}, runtime_context)
                    result = self._execute_rule(node, runtime_context)
                elif node_type == "IF":
                    trace_input = _node_input_mapping(node.config_json or {}, runtime_context)
                    result = self._execute_if(node, runtime_context)
                elif node_type == "WEB_API":
                    trace_input = _node_input_mapping(node.config_json or {}, runtime_context)
                    result = await self._execute_web_api(node, runtime_context)
                elif node_type == "LOOP":
                    trace_input = _node_input_mapping(node.config_json or {}, runtime_context)
                    result = self._execute_loop(node, runtime_context)
                elif node_type == "END":
                    configured_output = (node.config_json or {}).get("output")
                    trace_input = {"output": configured_output}
                    resolved = _render(configured_output, runtime_context) if configured_output else None
                    if isinstance(resolved, dict):
                        result = resolved
                    else:
                        result = final_output or {"result": "UNCERTAIN"}
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

        return final_output or {"result": "UNCERTAIN", "reason": "工作流未产生输出。"}

    def _execute_image_crop(
        self,
        image_path: str,
        raw_bbox: Any,
        raw_padding: Any,
        *,
        execution_id: int | None,
        node_key: str,
    ) -> dict[str, Any]:
        """Crop an upstream detector bbox into a stable local image artifact."""

        source_path = Path(image_path)
        if not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path
        if not source_path.is_file():
            raise ScenarioRuntimeError("图片裁剪节点无法读取输入图片。")
        x1, y1, x2, y2 = _parse_crop_bbox(raw_bbox)
        try:
            padding_ratio = float(raw_padding if raw_padding not in (None, "") else 0.05)
        except (TypeError, ValueError) as exc:
            raise ScenarioRuntimeError("图片裁剪节点的边缘扩展比例必须是数字。") from exc
        if not math.isfinite(padding_ratio) or not 0 <= padding_ratio <= 1:
            raise ScenarioRuntimeError("图片裁剪节点的边缘扩展比例必须在 0 到 1 之间。")

        try:
            with Image.open(source_path) as original:
                image = ImageOps.exif_transpose(original)
                width, height = image.size
                left, right = sorted((x1, x2))
                top, bottom = sorted((y1, y2))
                padding_x = (right - left) * padding_ratio
                padding_y = (bottom - top) * padding_ratio
                crop_left = max(0, math.floor(left - padding_x))
                crop_top = max(0, math.floor(top - padding_y))
                crop_right = min(width, math.ceil(right + padding_x))
                crop_bottom = min(height, math.ceil(bottom + padding_y))
                if crop_right <= crop_left or crop_bottom <= crop_top:
                    raise ScenarioRuntimeError("图片裁剪节点的 bbox 超出图片有效区域。")
                cropped = image.crop((crop_left, crop_top, crop_right, crop_bottom))
                if cropped.mode not in {"RGB", "L"}:
                    cropped = cropped.convert("RGB")
                elif cropped.mode == "L":
                    cropped = cropped.convert("RGB")
        except ScenarioRuntimeError:
            raise
        except Exception as exc:
            raise ScenarioRuntimeError(f"图片裁剪失败：{exc}") from exc

        directory_name = str(execution_id) if execution_id is not None else "preview"
        output_directory = PROJECT_ROOT / "uploads" / "scene_crops" / directory_name
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / f"{node_key}_{uuid4().hex[:12]}.jpg"
        try:
            cropped.save(output_path, format="JPEG", quality=95, optimize=True)
        except Exception as exc:
            raise ScenarioRuntimeError(f"无法保存图片裁剪结果：{exc}") from exc

        return {
            "result": "OK",
            "image_path": str(output_path),
            "source_image_path": str(source_path),
            "requested_bbox": [round(value, 2) for value in (x1, y1, x2, y2)],
            "crop_bbox": [crop_left, crop_top, crop_right, crop_bottom],
            "width": crop_right - crop_left,
            "height": crop_bottom - crop_top,
            "padding_ratio": padding_ratio,
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

    def _execute_loop(
        self,
        node: ScenarioNode,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        config = node.config_json or {}
        node_context = _node_input_mapping(config, runtime_context)
        render_context = {**runtime_context, "params": node_context}
        raw_items = _render(config.get("items"), render_context)
        if isinstance(raw_items, str):
            try:
                raw_items = json.loads(raw_items)
            except json.JSONDecodeError:
                raw_items = [raw_items] if raw_items else []
        if not isinstance(raw_items, list):
            return {"result": "ERROR", "reason": "循环集合必须是数组或可解析为数组的 JSON 文本。"}
        max_iterations = max(1, min(int(config.get("max_iterations", 10)), 1000))
        items = raw_items[:max_iterations]
        return {
            "result": "OK",
            "items": items,
            "count": len(items),
            "item_name": str(config.get("item_name") or "item"),
            "max_iterations": max_iterations,
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
