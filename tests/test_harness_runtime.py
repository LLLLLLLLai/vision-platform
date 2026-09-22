import unittest

from app.harness.builtin import BUILTIN_PLUGINS
from app.harness.contracts import PluginManifest
from app.harness.runtime import PluginRuntime, get_harness


class PluginRuntimeTest(unittest.TestCase):
    def test_mount_and_resolve_capability(self) -> None:
        runtime = PluginRuntime("test")
        plugin = PluginManifest(
            code="test_ocr",
            name="Test OCR",
            category="test",
            capabilities=("text.ocr",),
        )

        runtime.mount(plugin)

        self.assertEqual(runtime.resolve("text.ocr").code, "test_ocr")
        self.assertEqual(runtime.describe()["capability_routes"]["text.ocr"], "test_ocr")

    def test_profile_can_prefer_an_alternate_plugin(self) -> None:
        runtime = PluginRuntime("test")
        primary = PluginManifest(
            code="primary",
            name="Primary",
            category="test",
            capabilities=("image.embedding",),
        )
        alternate = PluginManifest(
            code="alternate",
            name="Alternate",
            category="test",
            capabilities=("image.embedding",),
        )
        runtime.mount(primary)
        runtime.mount(alternate)
        runtime.prefer("image.embedding", "alternate")

        self.assertEqual(runtime.resolve("image.embedding").code, "alternate")

    def test_production_runtime_exposes_core_capabilities(self) -> None:
        runtime = get_harness()
        capability_routes = runtime.describe()["capability_routes"]

        self.assertIn("reference.similarity", capability_routes)
        self.assertIn("text.ocr", capability_routes)
        self.assertIn("image.color", capability_routes)
        self.assertTrue(any(plugin.code == "dinov2" for plugin in BUILTIN_PLUGINS))
