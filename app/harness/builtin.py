from __future__ import annotations

from app.harness.contracts import PluginManifest


BUILTIN_PLUGINS: tuple[PluginManifest, ...] = (
    PluginManifest(
        code="opencv_rules",
        name="OpenCV Rules",
        category="本地规则",
        capabilities=("image.color", "image.alignment", "image.quality"),
        description="执行颜色、图像质量、几何和基准点对齐等确定性规则。",
    ),
)
