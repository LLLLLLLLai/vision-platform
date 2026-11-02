from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.harness.builtin import BUILTIN_PLUGINS
from app.harness.contracts import PluginManifest


class PluginRuntime:
    """Small, dependency-light plugin kernel inspired by Harness composition."""

    def __init__(self, profile: str) -> None:
        self.profile = profile
        self._plugins: dict[str, PluginManifest] = {}
        self._capabilities: dict[str, str] = {}

    def mount(self, plugin: PluginManifest) -> None:
        if plugin.code in self._plugins:
            raise ValueError(f"插件已挂载：{plugin.code}")
        self._plugins[plugin.code] = plugin
        for capability in plugin.capabilities:
            self._capabilities.setdefault(capability, plugin.code)

    def prefer(self, capability: str, plugin_code: str) -> None:
        """Choose the plugin that supplies a capability in this runtime profile."""
        plugin = self.plugin(plugin_code)
        if capability not in plugin.capabilities:
            raise ValueError(
                f"插件 {plugin_code} 不提供能力：{capability}"
            )
        self._capabilities[capability] = plugin_code

    def unmount(self, code: str) -> None:
        plugin = self._plugins.pop(code, None)
        if plugin is None:
            return
        for capability in plugin.capabilities:
            if self._capabilities.get(capability) == code:
                self._capabilities.pop(capability, None)

    def plugin(self, code: str) -> PluginManifest:
        try:
            return self._plugins[code]
        except KeyError as exc:
            raise ValueError(f"未启用插件：{code}") from exc

    def resolve(self, capability: str) -> PluginManifest:
        try:
            return self.plugin(self._capabilities[capability])
        except KeyError as exc:
            raise ValueError(f"当前运行档案未提供能力：{capability}") from exc

    def service_url(self, code: str) -> str:
        plugin = self.plugin(code)
        if not plugin.url_setting:
            raise ValueError(f"插件不是 HTTP 服务：{code}")
        return str(getattr(settings, plugin.url_setting)).rstrip("/")

    def service_plugins(self) -> tuple[PluginManifest, ...]:
        return tuple(plugin for plugin in self._plugins.values() if plugin.is_service)

    def describe(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "plugins": [
                {
                    "code": plugin.code,
                    "name": plugin.name,
                    "category": plugin.category,
                    "capabilities": list(plugin.capabilities),
                    "service_url": self.service_url(plugin.code) if plugin.is_service else None,
                    "launch_script": plugin.launch_script,
                    "description": plugin.description,
                }
                for plugin in self._plugins.values()
            ],
            "capability_routes": dict(self._capabilities),
        }


def _profile_configuration(profile: str) -> dict[str, Any]:
    config_path = Path(settings.harness_config_path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.is_file():
        return {"enabled_plugins": [plugin.code for plugin in BUILTIN_PLUGINS]}

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        configured = payload.get("profiles", {}).get(profile)
        if isinstance(configured, dict):
            enabled = configured.get("enabled_plugins")
            if isinstance(enabled, list) and enabled:
                return configured
    except (OSError, json.JSONDecodeError):
        pass
    return {"enabled_plugins": [plugin.code for plugin in BUILTIN_PLUGINS]}


@lru_cache(maxsize=1)
def get_harness() -> PluginRuntime:
    runtime = PluginRuntime(settings.harness_profile)
    profile_config = _profile_configuration(settings.harness_profile)
    enabled = {str(code) for code in profile_config["enabled_plugins"]}
    available = {plugin.code for plugin in BUILTIN_PLUGINS}
    unknown = enabled - available
    if unknown:
        raise RuntimeError(
            "运行档案引用了未注册插件：" + ", ".join(sorted(unknown))
        )
    disabled = {
        code.strip()
        for code in settings.harness_disabled_plugins.split(",")
        if code.strip()
    }
    for plugin in BUILTIN_PLUGINS:
        if plugin.code in enabled and plugin.code not in disabled:
            runtime.mount(plugin)
    routes = profile_config.get("capability_routes", {})
    if not isinstance(routes, dict):
        raise RuntimeError("运行档案 capability_routes 必须是对象")
    for capability, plugin_code in routes.items():
        if str(plugin_code) in disabled:
            continue
        runtime.prefer(str(capability), str(plugin_code))
    return runtime


def clear_harness_cache() -> None:
    get_harness.cache_clear()
