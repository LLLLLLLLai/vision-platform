from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DesignerRecordUiAssetTests(unittest.TestCase):
    def test_workflow_panels_scroll_without_covering_open_test(self) -> None:
        template = (PROJECT_ROOT / "app/templates/scene_designer.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app/static/js/scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app/static/css/scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertLess(template.index('id="workflowPaletteEmpty"'), template.index('class="palette-note"'))
        self.assertLess(template.index('class="palette-note"'), template.index('<main class="workflow-stage-panel">'))
        self.assertIn('classList.add("workflow-test-open")', script)
        self.assertIn('classList.remove("workflow-test-open")', script)
        self.assertIn('.workflow-designer.workflow-test-open .workflow-inspector', stylesheet)
        self.assertIn('.workflow-palette-scroll {\n  min-height: 0;\n  overflow-x: hidden;\n  overflow-y: auto;', stylesheet)

    def test_record_images_and_processing_have_accessible_controls(self) -> None:
        template = (PROJECT_ROOT / "app/templates/workspace.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app/static/js/workspace.js").read_text(encoding="utf-8")

        self.assertIn('id="recordImageLightboxClose"', template)
        self.assertIn('data-record-image-open', script)
        self.assertIn('data-record-json-toggle', script)
        self.assertIn('recordImageLightboxImage', script)
        self.assertIn('renderRecordProcessing(roi)', script)
        self.assertIn('record.call_status === "SUCCESS"', script)

    def test_training_images_and_recipe_retest_are_available(self) -> None:
        models_template = (PROJECT_ROOT / "app/templates/models.html").read_text(encoding="utf-8")
        models_script = (PROJECT_ROOT / "app/static/js/models.js").read_text(encoding="utf-8")
        workspace_template = (PROJECT_ROOT / "app/templates/workspace.html").read_text(encoding="utf-8")
        workspace_script = (PROJECT_ROOT / "app/static/js/workspace.js").read_text(encoding="utf-8")

        self.assertIn('id="trainingImageViewerClose"', models_template)
        self.assertIn('data-training-image=', models_script)
        self.assertIn('function closeTrainingImage()', models_script)
        self.assertIn('id="chooseAnotherTestImage"', workspace_template)
        self.assertIn('byId("chooseAnotherTestImage").addEventListener', workspace_script)
        self.assertIn('state.testFile = file;', workspace_script)

    def test_list_pages_have_bounded_scroll_regions(self) -> None:
        stylesheet = (PROJECT_ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

        for selector in (
            ".management-page .task-table-wrap",
            ".management-page .model-card-list",
            ".management-page .entity-list-card",
            ".recipe-library-table-wrap",
            ".detection-records-table-wrap",
            ".report-table-wrap",
        ):
            self.assertIn(selector, stylesheet)
        self.assertIn("scrollbar-gutter: stable;", stylesheet)


if __name__ == "__main__":
    unittest.main()
