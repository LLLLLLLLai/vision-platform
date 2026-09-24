from pathlib import Path
import unittest

from pydantic import ValidationError

from app.api.routes.automation import PromptOptimizationRequest
from app.services.automation_worker import _optimization_target_reached


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

    def test_training_detail_explains_metrics_and_uses_consistent_artifact_cards(self) -> None:
        script = (PROJECT_ROOT / "app" / "static" / "js" / "models.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "app" / "static" / "css" / "management.css").read_text(encoding="utf-8")

        self.assertIn("const trainingMetricGuide", script)
        self.assertIn("如何判断是否合理", script)
        self.assertIn("training-artifact-image", script)
        self.assertIn("#trainingJobDetailModal .training-artifact-grid", stylesheet)
        self.assertIn("aspect-ratio: 4 / 3", stylesheet)


if __name__ == "__main__":
    unittest.main()
