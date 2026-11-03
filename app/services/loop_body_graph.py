"""Convert legacy sequential loop bodies and explicit canvas connections."""

from __future__ import annotations

from typing import Any


def loop_body_connections(body: list[dict[str, Any]], configured: Any) -> list[dict[str, Any]]:
    if configured is not None:
        return configured if isinstance(configured, list) else []

    if not body:
        return []
    connections = [{"source_node_key": "loop_start", "target_node_key": str(body[0].get("node_key") or "")}]
    for index, entry in enumerate(body):
        source = str(entry.get("node_key") or "")
        next_key = str(body[index + 1].get("node_key") or "") if index + 1 < len(body) else "loop_end"
        if entry.get("node_type") == "IF":
            connections.extend([
                {"source_node_key": source, "target_node_key": str(entry.get("true_next_node_key") or next_key), "branch": "true"},
                {"source_node_key": source, "target_node_key": str(entry.get("false_next_node_key") or "loop_end"), "branch": "false"},
            ])
        else:
            connections.append({"source_node_key": source, "target_node_key": str(entry.get("next_node_key") or next_key)})
    return connections
