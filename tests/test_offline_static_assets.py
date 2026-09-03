from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATE = PROJECT_ROOT / "app" / "templates" / "base.html"
BOOTSTRAP_ROOT = (
    PROJECT_ROOT / "app" / "static" / "vendor" / "bootstrap" / "5.3.7"
)


class OfflineStaticAssetsTests(unittest.TestCase):
    def test_base_template_uses_local_bootstrap_assets(self) -> None:
        template = BASE_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("cdn.jsdelivr.net", template)
        self.assertIn(
            "/vendor/bootstrap/5.3.7/css/bootstrap.min.css",
            template,
        )
        self.assertIn(
            "/vendor/bootstrap/5.3.7/js/bootstrap.bundle.min.js",
            template,
        )

    def test_bootstrap_assets_are_bundled(self) -> None:
        expected_files = {
            "css/bootstrap.min.css": 200_000,
            "js/bootstrap.bundle.min.js": 70_000,
            "LICENSE": 1_000,
        }

        for relative_path, minimum_size in expected_files.items():
            asset = BOOTSTRAP_ROOT / relative_path
            self.assertTrue(asset.is_file(), f"missing offline asset: {asset}")
            self.assertGreater(asset.stat().st_size, minimum_size)


if __name__ == "__main__":
    unittest.main()
