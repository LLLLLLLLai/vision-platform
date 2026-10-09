from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.api.routes.automation import PromptOptimizationRequest
from app.services.automation_worker import (
    AutomationWorker,
    _build_prompt_optimization_request,
    _optimization_target_reached,
    _optimizer_proposal_error,
    _prompt_optimizer_system_prompt,
)
from app.services.openai_compatible_vlm import INSPECTION_VLM_SYSTEM_PROMPT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PromptOptimizationPolicyTests(unittest.TestCase):
    def test_zero_target_accuracy_is_rejected_and_never_meets_target(self) -> None:
        with self.assertRaises(ValidationError):
            PromptOptimizationRequest(
                scenario_version_id=1,
                dataset_id=1,
                detection_vlm_model_id=1,
                optimizer_vlm_model_id=2,
                prompt_template="检查线束锁付状态。",
                optimization_requirements="优先降低漏判。",
                target_accuracy=0,
            )

        self.assertFalse(_optimization_target_reached({"accuracy": 0.0, "labeled": 2}, 0.0))
        self.assertFalse(_optimization_target_reached({"accuracy": 0.0, "labeled": 2}, 0.95))
        self.assertFalse(_optimization_target_reached({"accuracy": 1.0, "labeled": 0}, 0.95))
        self.assertTrue(_optimization_target_reached({"accuracy": 0.95, "labeled": 20}, 0.95))

    def test_result_only_requirement_is_bound_to_detection_prompt_not_optimizer_reply(self) -> None:
        request = _build_prompt_optimization_request(
            optimization_requirements="检测模型的 result 只能返回 OK 或 NG。",
            current_prompt="检查线束标签 {{ input.ocr_text }}。",
            metrics={"accuracy": 0.8},
            preserved_variables={"input.ocr_text"},
            input_values={"ocr_text": "FU5"},
        )

        self.assertIn("【优化器自身返回格式】", request)
        self.assertIn("【检测模型需要满足的用户优化要求】", request)
        self.assertIn("它不约束优化器自身的 JSON 输出", request)
        self.assertIn("【不可变的生产输出契约】", request)
        self.assertIn("USER_OPTIMIZATION_REQUIREMENTS", request)
        self.assertIn("检测模型的 result 只能返回 OK 或 NG", request)
        self.assertIn(
            "优化模型返回了检测结论",
            _optimizer_proposal_error(
                {"result": "OK"},
                preserved_variables={"input.ocr_text"},
            ),
        )

    def test_output_contract_cannot_be_replaced_by_optimization_requirements(self) -> None:
        optimizer_contract = _prompt_optimizer_system_prompt()

        self.assertIn("不可变的生产输出契约", optimizer_contract)
        self.assertIn("纯文本、Markdown、XML", optimizer_contract)
        self.assertIn("必须包含 result 字段", INSPECTION_VLM_SYSTEM_PROMPT)
        self.assertIn("只返回一个 JSON 对象", INSPECTION_VLM_SYSTEM_PROMPT)
        self.assertIsNone(
            _optimizer_proposal_error(
                {
                    "optimized_detection_prompt": (
                        "检查线束标签 {{ input.ocr_text }}，仅输出 result 为 OK 或 NG。"
                    )
                },
                preserved_variables={"input.ocr_text"},
            )
        )


class PromptOptimizationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_when_optimizer_returns_detection_verdict(self) -> None:
        worker = AutomationWorker()
        first_reply = ({"result": "OK"}, {"attempt_count": 1, "retry_count": 0})
        repaired_reply = (
            {
                "optimized_detection_prompt": (
                    "检查线束标签 {{ input.ocr_text }}。只输出 JSON，"
                    "其中 result 只能为 OK 或 NG。"
                ),
                "requirements_satisfied": True,
                "requirement_reason": "已把结果枚举限制写入检测提示词。",
            },
            {"attempt_count": 1, "retry_count": 0},
        )
        with patch.object(
            worker,
            "_judge_with_retry",
            new=AsyncMock(side_effect=[first_reply, repaired_reply]),
        ) as judge:
            proposal, attempts, error = await worker._generate_optimized_prompt(
                object(),
                optimization_prompt="生成检测提示词。",
                preserved_variables={"input.ocr_text"},
            )

        self.assertIsNone(error)
        self.assertEqual(judge.await_count, 2)
        self.assertTrue(attempts["format_repair_attempted"])
        self.assertEqual(
            proposal["optimized_detection_prompt"],
            repaired_reply[0]["optimized_detection_prompt"],
        )
        correction_request = judge.await_args_list[1].kwargs["prompt"]
        self.assertIn("上一轮输出格式错误", correction_request)


class OptimizationAndTrainingDetailAssetTests(unittest.TestCase):
    def test_optimization_detail_exposes_full_prompt_view_copy_and_measured_target_state(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "scenes.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scenes_index.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "management.css").read_text(encoding="utf-8")

        self.assertIn('id="promptViewerModal"', template)
        self.assertIn('id="copyPromptViewer"', template)
        self.assertIn('min="0.01"', template)
        self.assertIn("const optimizationOutcome", script)
        self.assertIn("metricTargetReached", script)
        self.assertIn("data-prompt-view", script)
        self.assertIn("data-prompt-copy", script)
        self.assertIn("prompt-round-list", script)
        self.assertIn("data-job-stop", script)
        self.assertIn("data-job-restart", script)
        self.assertIn("optimizationInputValues", template)
        self.assertIn("optimization-round-table", script)
        self.assertIn("#taskDetailModal .modal-dialog", stylesheet)
        self.assertIn(".prompt-copy-float", stylesheet)
        self.assertIn(".optimization-input-values", stylesheet)
        self.assertIn("taskProgress(job)", script)
        self.assertIn("task-progress", stylesheet)

    def test_training_detail_explains_metrics_and_uses_consistent_artifact_cards(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "models.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "management.css").read_text(encoding="utf-8")

        self.assertIn("const trainingMetricGuide", script)
        self.assertIn("如何判断是否合理", script)
        self.assertIn("training-artifact-image", script)
        self.assertIn("#trainingJobDetailModal .training-artifact-grid", stylesheet)
        self.assertIn("aspect-ratio: 4 / 3", stylesheet)
        self.assertIn("trainingProgress(job)", script)

    def test_direct_vlm_testing_supports_manual_inputs_and_published_history(self) -> None:
        template = (PROJECT_ROOT / "app" / "templates" / "scene_designer.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "app" / "static" / "js" / "scene_designer.js").read_text(encoding="utf-8")

        self.assertIn('id="directTestInputs"', template)
        self.assertIn('id="directPromptVariables"', template)
        self.assertIn('id="designerVersionHistory"', template)
        self.assertIn("renderDirectTestInputs(version)", script)
        self.assertIn("directTestContext()", script)
        self.assertIn("function insertDirectPromptVariable", script)
        self.assertIn("data-direct-prompt-variable", script)
        self.assertIn("{{ input.${escapeHtml(name)} }} · ${escapeHtml(label)}", script)
        self.assertIn("isDraft() && !await saveDirect", script)
        self.assertIn("function renderVersionHistory()", script)


if __name__ == "__main__":
    unittest.main()
