from typing import Any


EXECUTION_LABELS = {
    "COMPLETED": "已完成",
    "FAILED": "执行失败",
    "SKIPPED": "已跳过",
    "RUNNING": "执行中",
    "PENDING": "待执行",
    "CANCELLED": "已取消",
}
RESULT_LABELS = {"OK": "通过（OK）", "NG": "不通过（NG）", "UNCERTAIN": "待确认"}
RESULT_ALIASES = {"PASS": "OK", "TRUE": "OK", "FAIL": "NG", "FALSE": "NG", "UNKNOWN": "UNCERTAIN"}


def node_trace_state(trace: dict[str, Any]) -> dict[str, Any]:
    output = trace.get("output") if isinstance(trace.get("output"), dict) else {}
    node_type = str(trace.get("node_type") or "").upper()
    legacy_status = str(trace.get("status") or "").upper()
    output_result = str(output.get("result") or "").upper()
    execution_status = str(trace.get("execution_status") or "").upper()
    if execution_status not in EXECUTION_LABELS:
        if legacy_status in {"ERROR", "FAILED"} or (
            node_type != "START" and (output_result == "ERROR" or str(output.get("status") or "").upper() in {"ERROR", "FAILED"})
        ):
            execution_status = "FAILED"
        elif legacy_status == "SKIPPED" or (node_type != "START" and output_result == "SKIPPED"):
            execution_status = "SKIPPED"
        elif legacy_status in EXECUTION_LABELS:
            execution_status = legacy_status
        else:
            execution_status = "COMPLETED"

    detection_result = None
    branch = None
    if execution_status == "COMPLETED":
        quality_node = node_type in {"VLM", "RULE", "END", "LOOP", "ITERATION", "CONDITION_LOOP"}
        if node_type in {"LOOP", "ITERATION", "CONDITION_LOOP"}:
            quality_node = output.get("aggregate") != "COLLECT_ONLY" and bool(output.get("aggregate") or output.get("rounds"))
        raw_result = output_result or legacy_status
        if node_type == "WEB_API":
            response = output.get("response")
            quality_node = isinstance(response, dict) and response.get("result") is not None
            raw_result = str(response.get("result") or "").upper() if quality_node else ""
        if quality_node:
            raw_result = RESULT_ALIASES.get(raw_result, raw_result)
            detection_result = raw_result if raw_result in RESULT_LABELS else "UNCERTAIN"
        if node_type == "IF":
            branch = str(output.get("branch") or "").lower()
            if branch not in {"true", "false"}:
                branch = {"OK": "true", "NG": "false"}.get(output_result or legacy_status)

    result_label = RESULT_LABELS.get(detection_result, "—")
    if branch in {"true", "false"}:
        result_label = "条件成立" if branch == "true" else "条件不成立"
    return {
        "execution_status": execution_status,
        "execution_label": EXECUTION_LABELS[execution_status],
        "detection_result": detection_result,
        "result_label": result_label,
        "branch": branch,
    }


def with_node_trace_states(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    if isinstance(payload.get("traces"), list):
        normalized["traces"] = [
            {**trace, **node_trace_state(trace), "output": with_node_trace_states(trace.get("output"))}
            if isinstance(trace, dict) else trace
            for trace in payload["traces"]
        ]
    for key in ("items", "rounds"):
        if isinstance(payload.get(key), list):
            normalized[key] = [with_node_trace_states(item) for item in payload[key]]
    if isinstance(payload.get("output"), dict):
        normalized["output"] = with_node_trace_states(payload["output"])
    return normalized
