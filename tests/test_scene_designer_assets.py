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
            "workflowNodeNavigator",
            "workflowInspectorStatus",
            "workflowPaletteToggle",
            "workflowPaletteClose",
            "workflowInspectorToggle",
            "workflowInspectorClose",
            "designerValidate",
            "workflowPreflightModal",
            "workflowPreflightSummary",
            "workflowPreflightList",
            "designerApi",
            "sceneApiModal",
            "sceneApiEndpoint",
            "sceneApiRequestExample",
            "sceneApiResponseExample",
        ):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("scene_designer_dify.css", template)
        self.assertIn('data-node-type="IMAGE_ANNOTATE"', template)
        self.assertIn('data-node-type="S3_UPLOAD"', template)
        self.assertIn('id="workflowPalette"', template)

    def test_workflow_designer_has_search_and_layout_handlers(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")

        self.assertIn("function autoLayoutWorkflow()", script)
        self.assertIn("function focusWorkflowNode(nodeId)", script)
        self.assertIn("...state.pendingNodeKeys", script)
        self.assertIn('byId("workflowNodeNavigator").addEventListener', script)
        self.assertIn('" · 未连通"', script)
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
        self.assertIn("function ruleActualSourceHint(", script)
        self.assertIn('id="nodeActualSource"', script)
        self.assertIn('if (event.target.id === "nodeActual") updateRuleActualSourceHint()', script)
        self.assertIn("function applyVariablePickerToken(", script)
        self.assertIn("function updateWorkflowWorkbenchPanels(", script)
        self.assertIn("function runDesignerPreflight(", script)
        self.assertIn("function workflowNodeContract(", script)
        self.assertIn("workflow-data-type", script)
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
        self.assertIn("IMAGE_ANNOTATE", script)
        self.assertIn('id: "nodeS3ImagePath"', script)
        self.assertIn('id: "nodeS3Key"', script)
        self.assertIn("function annotationGeometryCompatibilityError(", script)
        self.assertIn("function runWorkflowTest()", script)
        self.assertIn("function startWorkflowTestRun()", script)
        self.assertIn("function renderWorkflowTestResult(", script)
        self.assertIn("function workflowTraceArtifactMarkup(", script)
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

    def test_workflow_inspector_stays_inside_viewport(self) -> None:
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn("height: min(760px, calc(100dvh - 230px));", stylesheet)
        self.assertIn(".scene-designer-page .workflow-inspector-content {\n  flex: 1 1 auto;", stylesheet)
        self.assertIn("overflow-y: auto;", stylesheet)

    def test_classification_prediction_is_visible_without_an_expected_label(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn('contractDescriptor("predicted_label", "TEXT", "预测类别")', script)
        self.assertIn('VISION_MODEL: profile?.supports_classification ?', script)
        self.assertIn("模型预测类别：", script)
        self.assertNotIn("nodeExpectedLabelOptions", script)
        self.assertIn("分类节点只输出", script)
        self.assertIn(".workflow-classification-summary", stylesheet)

    def test_detection_node_exposes_raw_predictions_and_debug_preview(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn('contractDescriptor("objects", "OBJECT_LIST", "目标列表")', script)
        self.assertIn('contractDescriptor("object_count", "NUMBER", "目标数量")', script)
        self.assertIn('output?.preview_image_url', script)
        self.assertIn('.workflow-trace-artifact.is-model-preview', stylesheet)
        self.assertNotIn('id="nodeExpectedMinCount"', script)
        self.assertNotIn('id="nodeExpectedClass"', script)

    def test_batch_crop_and_per_item_vlm_are_configurable_in_designer(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn('id: "nodeCropObjects"', script)
        self.assertIn('contractDescriptor("crops", "IMAGE_LIST"', script)
        self.assertIn('id="nodeLoopOperation"', script)
        self.assertIn('id="nodeLoopVlmModel"', script)
        self.assertIn('id: "nodeLoopPrompt"', script)
        self.assertIn('"{{ item.label }}"', script)
        self.assertIn('class="workflow-crop-gallery"', script)
        self.assertIn('.workflow-crop-gallery img', stylesheet)

    def test_rule_editor_has_plain_language_operators_and_no_duplicate_mapping(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")

        self.assertIn('["CONTAINS", "包含"]', script)
        self.assertIn('["NUMBER_GTE", "数值大于或等于"]', script)
        self.assertIn('function ruleInputValue(config, key)', script)
        self.assertIn('delete config.input_mapping;', script)
        self.assertIn('inputContent?.prepend(...settingsContent.children)', script)
        self.assertIn('"{{ input.original_image_path }}"', script)
        self.assertIn('"{{ input.source_image_path }}"', script)
        self.assertIn('"{{ input.roi_bbox }}"', script)

    def test_trace_cards_expand_and_edges_have_explicit_delete_action(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "scene_designer.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertIn('id="workflowDeleteEdge"', template)
        self.assertIn('class="workflow-edge-hit"', script)
        self.assertIn('state.selectedEdgeId = Number(edge.dataset.edgeId)', script)
        self.assertIn('byId("workflowDeleteEdge").addEventListener', script)
        self.assertIn('grid-auto-rows: max-content', stylesheet)
        self.assertIn('.workflow-node-layer { pointer-events: none; }', stylesheet)

    def test_loop_body_expands_inside_the_shared_canvas(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "scene_designer.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "scene_designer_dify.css").read_text(encoding="utf-8")

        self.assertLess(template.index('id="workflowCanvasSurface"'), template.index('id="workflowSubflowPanel"'))
        self.assertLess(template.index('id="workflowSubflowPanel"'), template.index('id="workflowCanvasEmpty"'))
        self.assertIn("function workflowDisplayLayout(nodes)", script)
        self.assertIn("const container = { x: parentPosition.x", script)
        self.assertIn("height: nodeHeight + subflowHeight + 12", script)
        self.assertIn("layout.container.width}px;height:${layout.container.height}px", script)
        self.assertIn("function dropNodeIntoLoop(event, nodeId)", script)
        self.assertIn('byId("workflowNodeLayer").addEventListener("drop"', script)
        self.assertIn('byId("workflowSubflowPanel").addEventListener("drop"', script)
        self.assertIn('event.target.closest("#workflowSubflowPanel")', script)
        self.assertIn("event.stopPropagation();", script)
        self.assertIn(".workflow-canvas-node.subflow-expanded", stylesheet)
        self.assertIn(".workflow-subflow-panel.drop-target", stylesheet)
        self.assertIn("从左侧节点菜单拖入此框任意位置", template)
        self.assertNotIn("inset: 70px 18px 18px", stylesheet)

    def test_model_output_aliases_show_their_source_type(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")

        self.assertIn('const sourceName = /^\\{\\{\\s*response\\.([\\w.]+)\\s*\\}\\}$/.exec', script)
        self.assertIn('source ? `${source.label}（${name}）`', script)
        self.assertIn('visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).display_name', script)


if __name__ == "__main__":
    unittest.main()
