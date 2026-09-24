from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecipeDatasetUiAssetTests(unittest.TestCase):
    def test_recipe_history_uses_in_page_version_selector(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "workspace.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")

        self.assertIn('id="recipeHistoryModal"', template)
        self.assertIn('id="recipeHistoryList"', template)
        self.assertIn("function openRecipeHistory(", script)
        self.assertIn("data-recipe-history-rollback", script)
        self.assertNotIn("window.prompt", script)

    def test_detection_annotation_exposes_select_move_and_resize_workspace(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for element_id in (
            "annotationToolSelect",
            "annotationToolRectangle",
            "annotationCanvasWrap",
            "annotationLabelPalette",
            "annotationSelectedProperties",
            "annotationPreviousItem",
            "annotationNextItem",
        ):
            self.assertIn(f'id="{element_id}"', template)

        for implementation_marker in (
            "function resizeAnnotationBox(",
            "function moveAnnotationBox(",
            "function undoAnnotationChange(",
            "function changeAnnotationZoom(",
            "data-annotation-handle",
            "dragging empty image space pans the image",
        ):
            self.assertIn(implementation_marker, script)

    def test_dataset_supports_offline_yolo_label_package_import(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('id="offlineYoloImportActions"', template)
        self.assertIn('id="offlineYoloArchive"', template)
        self.assertIn("function importOfflineYoloArchive()", script)
        self.assertIn("/imports/yolo", script)


if __name__ == "__main__":
    unittest.main()
