import copy
import time
import unittest

from app.services.node_trace_status import node_trace_state, with_node_trace_states
from app.services.scenario_runtime import _append_trace


class NodeTraceStatusTests(unittest.TestCase):
    def trace(self, node_type, output, **kwargs):
        context = {}
        _append_trace(
            context, node_key="node", node_name="节点", node_type=node_type,
            started_at=time.perf_counter(), node_input={}, output=output, **kwargs,
        )
        return context["traces"][0]

    def test_start_passes_user_values_without_becoming_a_verdict(self):
        for value in (None, "NG", "UNCERTAIN", "ERROR", "SKIPPED"):
            with self.subTest(value=value):
                output = {"image_path": "sample.jpg", "result": value}
                trace = self.trace("START", output)
                self.assertEqual(trace["status"], "COMPLETED")
                self.assertEqual(trace["execution_label"], "已完成")
                self.assertIsNone(trace["detection_result"])
                self.assertEqual(trace["output"], output)

    def test_inference_and_image_operations_do_not_make_quality_decisions(self):
        for node_type, output in (
            ("VISION_MODEL", {"predicted_label": "NG", "confidence": 0.9}),
            ("IMAGE_CROP", {"result": "UNCERTAIN", "crops": []}),
            ("IMAGE_ANNOTATE", {"result": "OK", "annotation_count": 0}),
            ("S3_UPLOAD", {"status": "COMPLETED", "s3_uri": "s3://bucket/result.jpg"}),
            ("LOOP", {"result": "UNCERTAIN", "aggregate": "COLLECT_ONLY"}),
        ):
            with self.subTest(node_type=node_type):
                trace = self.trace(node_type, output)
                self.assertEqual(trace["execution_status"], "COMPLETED")
                self.assertIsNone(trace["detection_result"])

    def test_quality_decisions_are_separate_from_execution_success(self):
        for node_type, result in (("RULE", "NG"), ("VLM", "UNCERTAIN"), ("END", "OK")):
            with self.subTest(node_type=node_type):
                trace = self.trace(node_type, {"result": result})
                self.assertEqual(trace["execution_status"], "COMPLETED")
                self.assertEqual(trace["detection_result"], result)

    def test_false_branch_is_not_a_failed_node_or_ng_decision(self):
        trace = self.trace("IF", {"result": "NG", "branch": "false"})
        self.assertEqual(trace["execution_status"], "COMPLETED")
        self.assertIsNone(trace["detection_result"])
        self.assertEqual(trace["result_label"], "条件不成立")
        self.assertEqual(trace["output"]["result"], "NG")

    def test_errors_and_skips_are_preserved(self):
        for node_type in ("VLM", "VISION_MODEL", "RULE", "IMAGE_CROP", "S3_UPLOAD", "END"):
            with self.subTest(node_type=node_type):
                trace = self.trace(node_type, {"result": "ERROR", "reason": "timeout"})
                self.assertEqual(trace["execution_status"], "FAILED")
                self.assertEqual(trace["execution_label"], "执行失败")
        failed_start = self.trace("START", {}, execution_status="FAILED")
        self.assertEqual(failed_start["execution_status"], "FAILED")
        skipped = self.trace("VLM", {"result": "SKIPPED"}, execution_status="SKIPPED")
        self.assertEqual(skipped["status"], "SKIPPED")
        self.assertIsNone(skipped["detection_result"])

    def test_legacy_and_nested_traces_normalize_without_modifying_history(self):
        start = {"node_type": "START", "status": "UNCERTAIN", "output": {"image_path": "sample.jpg"}}
        skipped = {"node_type": "VLM", "status": "UNCERTAIN", "output": {"result": "SKIPPED"}}
        payload = {"traces": [start, {"node_type": "ITERATION", "output": {
            "result": "NG", "aggregate": "ALL_OK", "items": [{"traces": [start, skipped]}],
        }}]}
        original = copy.deepcopy(payload)
        normalized = with_node_trace_states(payload)
        self.assertEqual(normalized["traces"][0]["execution_status"], "COMPLETED")
        children = normalized["traces"][1]["output"]["items"][0]["traces"]
        self.assertEqual(children[0]["execution_status"], "COMPLETED")
        self.assertEqual(children[1]["execution_status"], "SKIPPED")
        self.assertEqual(payload, original)

    def test_web_transport_success_and_response_verdict_are_distinct(self):
        state = node_trace_state({"node_type": "WEB_API", "output": {"result": "OK", "response": {"data": []}}})
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertIsNone(state["detection_result"])
        state = node_trace_state({"node_type": "WEB_API", "output": {"result": "NG", "response": {"result": "NG"}}})
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertEqual(state["detection_result"], "NG")


if __name__ == "__main__":
    unittest.main()
