from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PluginManifest:
    """Declarative definition of one replaceable platform capability."""

    code: str
    name: str
    category: str
    capabilities: tuple[str, ...]
    url_setting: str | None = None
    launch_script: str | None = None
    python_environments: tuple[str, ...] = ()
    health_path: str = "/health"
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_service(self) -> bool:
        return self.url_setting is not None
