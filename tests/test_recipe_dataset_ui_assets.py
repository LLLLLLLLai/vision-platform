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
            "annotationSelectedX",
            "annotationSelectedY",
            "annotationSelectedWidth",
            "annotationSelectedHeight",
            "annotationPreviousItem",
            "annotationNextItem",
        ):
            self.assertIn(f'id="{element_id}"', template)

        for implementation_marker in (
            "function resizeAnnotationBox(",
            "function moveAnnotationBox(",
            "function undoAnnotationChange(",
            "function changeAnnotationZoom(",
            "function updateSelectedAnnotationGeometry(",
            "function updateSelectedAnnotationLabel(",
            "data-annotation-handle",
            "dragging empty image space pans the image",
            "document.addEventListener(\"pointermove\", moveDetectionInteraction",
        ):
            self.assertIn(implementation_marker, script)

    def test_annotation_canvas_uses_color_frames_and_keeps_names_in_object_list(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('id="detectionAnnotationList"', template)
        self.assertIn("annotationColor(box.label)", script)
        self.assertIn("已标注对象", template)
        self.assertNotIn('class="annotation-svg-label"', script)

    def test_recipe_canvas_keeps_native_resolution_for_zoom(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")

        for implementation_marker in (
            "fitScale: 1",
            "const sourceWidth = baseImage.naturalWidth",
            "canvas.width = sourceWidth",
            "const renderedScale = Math.max(0.01, (view.fitScale || 1) * view.scale)",
            "function canvasVisualUnit()",
            "state.imageView.maxScale = Math.max(1, Math.min(6, 1 / fitScale));",
        ):
            self.assertIn(implementation_marker, script)

    def test_recipe_and_dataset_boxes_only_appear_after_a_real_drag(self) -> None:
        recipe_script = (PROJECT_ROOT / "app" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")
        dataset_script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for implementation_marker in (
            "drawHasMoved: false",
            "function hasPointerDragExceeded(",
            "function drawStartMarker(",
            "lastPointerPoint",
            "Do not show or save an implicit minimum-size rectangle",
        ):
            self.assertIn(implementation_marker, recipe_script)
        for implementation_marker in (
            "function detectionDrawOriginMarkup(",
            "function annotationDragExceeded(",
            "dragged: false",
            "state.annotation.draft = null;",
            "interaction.dragged",
        ):
            self.assertIn(implementation_marker, dataset_script)

    def test_dataset_supports_offline_yolo_label_package_import(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('id="offlineYoloImportActions"', template)
        self.assertIn('id="offlineYoloArchive"', template)
        self.assertIn("function importOfflineYoloArchive()", script)
        self.assertIn("/imports/yolo", script)

    def test_dataset_supports_separate_roi_and_camera_original_collection(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for element_id in (
            "datasetCollectionScope",
            "datasetCollectionScenarioField",
            "datasetCollectionRecipeField",
            "datasetCollectionRecipe",
        ):
            self.assertIn(f'id="{element_id}"', template)
        for marker in (
            "AUTO_ORIGINAL",
            "function loadPublishedRecipes()",
            "function syncDatasetCollectionMode()",
            "collection_recipe_id",
            "相机原图采集",
        ):
            self.assertIn(marker, script)

    def test_annotation_workspace_exposes_safe_ai_preannotation(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for element_id in (
            "annotationAiAssistant",
            "annotationPreModel",
            "annotationPreConfidence",
            "runAnnotationPrelabel",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("function runAnnotationPrelabel()", script)
        self.assertIn("/pre-annotations", script)
        self.assertIn("AI 预标注会替换当前页面中尚未保存的标注候选", script)

    def test_detection_record_detail_exposes_manual_review_entry(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")

        for marker in (
            "function renderManualReview(",
            "data-save-manual-review",
            "/manual-review",
            "人工确认已保存，并将用于场景统计。",
        ):
            self.assertIn(marker, script)

    def test_scene_report_exposes_quality_risk_metrics(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "workspace.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")

        self.assertIn("人工真值、漏判、误判、执行异常与耗时", template)
        for marker in (
            "人工确认样本准确率",
            "漏判 OK→NG",
            "误判 NG→OK",
            "P95 耗时",
            "function reportTrendChart(",
            "report-health",
        ):
            self.assertIn(marker, script)

    def test_model_center_exposes_trained_yolo_weight_import(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "models.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "models.js").read_text(encoding="utf-8")

        for marker in (
            'id="importVisionVersionModal"',
            'id="importVisionVersionForm"',
            'name="weights_file"',
            'name="existing_weights_path"',
            "导入已训练模型",
        ):
            self.assertIn(marker, template)
        for marker in (
            "function openImportVisionVersion(",
            "function importVisionVersion(",
            "/versions/import",
            "data-import-vision",
        ):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
