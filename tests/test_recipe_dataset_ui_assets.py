from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecipeDatasetUiAssetTests(unittest.TestCase):
    def test_recipe_details_follow_displayed_version_not_production_fallback(self) -> None:
        script = (PROJECT_ROOT / "app/static/js/workspace.js").read_text(encoding="utf-8")
        action = script.split('if (event.target.closest(".detail-recipe")) {', 1)[1].split("} else if", 1)[0]
        self.assertIn("recipeDetailUrl(recipeId)", action)
        self.assertNotIn("sourceRecipeId", action)
        self.assertIn("href=\"${recipeDetailUrl(version.id)}\"", script)

    def test_recipe_detail_versions_keep_readonly_navigation(self) -> None:
        template = (PROJECT_ROOT / "app/templates/workspace.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app/static/js/workspace.js").read_text(encoding="utf-8")
        self.assertIn('id="recipeDetailVersion"', template)
        self.assertIn("&mode=view", script.split("function recipeDetailUrl(", 1)[1].split("\n}", 1)[0])
        self.assertIn("renderRecipeDetailVersions();", script)
        fields = script.split("function recipeFields()", 1)[1].split("\n}", 1)[0]
        self.assertIn('byId("recipeForm").elements', fields)
        self.assertNotIn("formValues(", fields)

    def test_recipe_image_failures_are_visible_and_do_not_mix_versions(self) -> None:
        template = (PROJECT_ROOT / "app/templates/workspace.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app/static/js/workspace.js").read_text(encoding="utf-8")
        self.assertIn('id="recipeImageNotice"', template)
        self.assertIn('baseImage.addEventListener("error"', script)
        self.assertIn("当前查看版本未配置基准图片", script)
        self.assertIn("不会自动替换为其他版本的图片", script)

    def test_dataset_items_can_be_filtered_by_annotation_status(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for status in ("ALL", "PENDING", "AI_LABELED", "LABELED"):
            self.assertIn(f'data-item-filter="{status}"', template)
        for element_id in (
            "datasetItemSearch",
            "datasetItemSourceFilter",
            "datasetItemSplitFilter",
            "datasetItemSort",
            "datasetItemFilterSummary",
            "datasetItemPagination",
            "datasetItemPageSize",
            "datasetPagePrevious",
            "datasetPageNumber",
            "datasetPageGo",
            "datasetPageNext",
            "datasetLoadMore",
        ):
            self.assertIn(f'id="{element_id}"', template)
        for marker in (
            "function filteredDatasetItems(",
            "function renderItemFilters(",
            "function resetItemFilters(",
            "items.slice(0, state.visibleItemCount)",
            "items.slice(start, start + state.itemPageSize)",
            "return filteredDatasetItems(dataset).filter(",
        ):
            self.assertIn(marker, script)

    def test_dataset_batch_annotation_and_collection_controls_are_exposed(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")
        for element_id in (
            "openDatasetBatchAnnotation",
            "datasetBatchExisting",
            "datasetBatchProgress",
            "datasetAutoCollectToggle",
            "collectionSettingsScenario",
            "collectionSettingsRecipe",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("function startBatchAnnotation(", script)
        self.assertIn("function toggleDatasetCollection(", script)
        self.assertIn("function loadBatchJob(", script)

    def test_ai_annotations_are_saved_without_manual_review(self) -> None:
        template = (PROJECT_ROOT / "app/templates/datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app/static/js/datasets.js").read_text(encoding="utf-8")
        self.assertNotIn("待复核", template + script)
        self.assertNotIn("请人工确认后保存", template + script)
        self.assertIn("AI 已标注", template)
        handler = script.split("async function runAnnotationPrelabel()", 1)[1].split("function openAnnotation(", 1)[0]
        self.assertIn("setAnnotationDirty(false)", handler)
        self.assertIn("Object.assign(storedItem, response.item)", handler)
        self.assertIn("renderDetail()", handler)

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

    def test_dataset_upload_reports_skipped_images_beside_upload_button(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('id="datasetUploadFeedback"', template)
        self.assertIn('role="status" aria-live="polite"', template)
        self.assertIn('function showUploadFeedback(', script)
        self.assertIn('未新增素材', script)
        self.assertIn('图片内容重复', script)

    def test_dataset_detail_exposes_safe_dataset_delete_action(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('id="deleteDataset"', template)
        self.assertIn("function deleteDataset()", script)
        self.assertIn("method: \"DELETE\"", script)
        self.assertIn("历史训练、评测与检测记录会保留", script)

    def test_classification_annotation_uses_configured_label_select(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        self.assertIn('<select id="classificationAnnotationLabel"', template)
        self.assertNotIn('<input id="classificationAnnotationLabel"', template)
        self.assertIn("function renderClassificationLabelOptions(", script)
        self.assertIn("请选择数据集已维护的图片类别", script)

    def test_training_dataset_exposes_configurable_safe_augmentation(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "datasets.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "datasets.js").read_text(encoding="utf-8")

        for element_id in (
            "openDatasetAugmentation",
            "datasetAugmentationModal",
            "augmentationCopiesPerItem",
            "augmentationHorizontalFlip",
            "augmentationRotationEnabled",
            "augmentationBrightnessEnabled",
            "augmentationNoiseEnabled",
            "runDatasetAugmentation",
        ):
            self.assertIn(f'id="{element_id}"', template)
        for marker in (
            "function supportsDatasetAugmentation(",
            "function buildAugmentationPayload(",
            "function runDatasetAugmentation(",
            "/augmentations",
            "AUGMENTED: \"数据增强\"",
        ):
            self.assertIn(marker, script)

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
        self.assertIn("AI 自动标注会替换并保存当前图片的标注", script)

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
        self.assertIn('id="inspectionReportDimensions"', template)
        self.assertNotIn('id="inspectionReportSummary"', template)
        self.assertNotIn('id="inspectionReportCharts"', template)
        for marker in (
            "场景健康统计",
            "样本准确率",
            "漏判 OK→NG",
            "误判 NG→OK",
            "P95 耗时",
            "report-health",
        ):
            self.assertIn(marker, script)
        self.assertNotIn("function reportTrendChart(", script)

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

    def test_model_versions_use_chinese_table_instead_of_raw_json(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "models.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "models.js").read_text(encoding="utf-8")

        for marker in (
            'id="visionVersionsModal"',
            'id="visionVersionsList"',
            "训练数据集",
            "关键指标",
            "权重文件",
        ):
            self.assertIn(marker, template)
        for marker in (
            "function openVisionVersions(",
            "function versionMetricSummary(",
            'YOLO_DETECTION: ["metrics/mAP50-95(B)", "metrics/mAP50(B)", "metrics/precision(B)", "metrics/recall(B)"]',
            'YOLO_SEGMENTATION: ["metrics/mAP50-95(M)", "metrics/mAP50(M)", "metrics/precision(M)", "metrics/recall(M)"]',
            "查看其他 ${other.length} 项指标",
            'PUBLISHED: ["当前发布"',
            'ARCHIVED: ["已归档"',
        ):
            self.assertIn(marker, script)
        self.assertNotIn("JSON.stringify(versions, null, 2)", script)


if __name__ == "__main__":
    unittest.main()
