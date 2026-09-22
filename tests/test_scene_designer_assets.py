from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SceneDesignerAssetTests(unittest.TestCase):
    def test_workflow_designer_exposes_workbench_controls(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "scene_designer.html").read_text(encoding="utf-8")

        for element_id in (
            "directInputParameters",
            "directClearResult",
            "workflowNodeSearch",
            "workflowAutoLayout",
            "workflowFitCanvas",
            "workflowTestImage",
            "workflowRunTest",
            "workflowTestPanel",
            "workflowTestTraceList",
            "workflowTestClear",
            "workflowGraphSummary",
            "workflowInspectorStatus",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("scene_designer_dify.css", template)

    def test_workflow_designer_has_search_and_layout_handlers(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")

        self.assertIn("function autoLayoutWorkflow()", script)
        self.assertIn("function filterWorkflowPalette(query)", script)
        self.assertIn('byId("workflowAutoLayout").addEventListener', script)
        self.assertIn('byId("workflowNodeSearch").addEventListener', script)
        self.assertIn("function parameterSectionMarkup(", script)
        self.assertIn("function readParameterRows(", script)
        self.assertIn("function readSchemaParameterRows(", script)
        self.assertIn("function clearDirectConversation()", script)
        self.assertIn("function directResultSummary(", script)
        self.assertIn("function fitDirectDesignerToViewport()", script)
        self.assertIn('byId("directClearResult").addEventListener', script)
        self.assertIn("function inputMappingMarkup(", script)
        self.assertIn("function outputMappingMarkup(", script)
        self.assertIn("function upstreamNodeKeys(", script)
        self.assertIn('id: "nodeEndOutputParameters"', script)
        self.assertIn("IMAGE_CROP", script)
        self.assertIn("function runWorkflowTest()", script)
        self.assertIn("function renderWorkflowTestResult(", script)
        self.assertIn('byId("workflowRunTest").addEventListener', script)


if __name__ == "__main__":
    unittest.main()
