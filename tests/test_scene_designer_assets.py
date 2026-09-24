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
            "workflowTestRunPanel",
            "workflowTestPanel",
            "workflowTestResultBadge",
            "workflowTestElapsed",
            "workflowTestTraceList",
            "workflowTestTraceCount",
            "workflowTestFinalOutput",
            "workflowTestClear",
            "workflowGraphSummary",
            "workflowInspectorStatus",
            "workflowPaletteToggle",
            "workflowPaletteClose",
            "workflowInspectorToggle",
            "workflowInspectorClose",
            "designerApi",
            "sceneApiModal",
            "sceneApiEndpoint",
            "sceneApiRequestExample",
            "sceneApiResponseExample",
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
        self.assertIn("function promptParameterMappingMarkup(", script)
        self.assertIn("function promptMappedParameterKeys(", script)
        self.assertIn("function outputMappingMarkup(", script)
        self.assertIn("function upstreamNodeKeys(", script)
        self.assertIn("function readVlmInspectorConfig(", script)
        self.assertIn("function insertVariableToken(", script)
        self.assertIn("function promptTemplateEditorMarkup(", script)
        self.assertIn("function insertPromptVariableChip(", script)
        self.assertIn("function syncPromptTemplateEditor(", script)
        self.assertIn("data-prompt-template-editor", script)
        self.assertIn("data-prompt-variable-remove", script)
        self.assertIn("prompt-variable-chip", script)
        self.assertIn("function openVariablePicker(", script)
        self.assertIn("function variableGroups(", script)
        self.assertIn("function applyVariablePickerToken(", script)
        self.assertIn("function updateWorkflowWorkbenchPanels(", script)
        self.assertIn("function showSceneApiContract()", script)
        self.assertIn("function copySceneApiValue(kind)", script)
        self.assertIn('byId("designerApi").addEventListener', script)
        self.assertIn("function setInspectorTab(", script)
        self.assertIn("function setupInspectorTabs(", script)
        self.assertIn("node-config-tabs", script)
        self.assertIn("data-inspector-tab", script)
        self.assertIn("data-variable-target", script)
        self.assertIn("data-variable-picker", script)
        self.assertIn("workflowVariablePicker", script)
        self.assertIn("保存当前节点", script)
        self.assertIn('id: "nodeEndOutputParameters"', script)
        self.assertIn("IMAGE_CROP", script)
        self.assertIn("function runWorkflowTest()", script)
        self.assertIn("function startWorkflowTestRun()", script)
        self.assertIn("function renderWorkflowTestResult(", script)
        self.assertIn('byId("workflowRunTest").addEventListener', script)
        self.assertIn('byId("workflowTestRunPanel").addEventListener', script)
        self.assertIn("提示词参数映射", script)
        self.assertIn("{{ params.", script)
        self.assertIn("function nextDraftVersionName(", script)
        self.assertNotIn('prompt("新草稿版本号"', script)
        self.assertIn('const systemNode = ["START", "END"].includes(node.node_type);', script)
        self.assertNotIn('element.classList.contains("locked")', script)
        self.assertNotIn("workflow-debug-open", script)

    def test_workflow_debug_layout_keeps_trace_and_inspector_scrollable(self) -> None:
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn("workflow-stage-panel.has-test .workflow-test-panel", stylesheet)
        self.assertIn("workflow-trace-detail pre", stylesheet)
        self.assertIn("overflow: auto", stylesheet)


if __name__ == "__main__":
    unittest.main()
