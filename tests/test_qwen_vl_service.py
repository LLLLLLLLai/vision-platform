import unittest

from qwen_vl_service.utils import parse_json_object, parse_partial_object_list


class QwenVlJsonParsingTest(unittest.TestCase):
    def test_parses_plain_json(self) -> None:
        parsed = parse_json_object('{"result":"OK","confidence":0.9}')
        self.assertEqual(parsed["result"], "OK")

    def test_parses_fenced_json(self) -> None:
        parsed = parse_json_object(
            '```json\n{"result":"UNCERTAIN","confidence":0.2}\n```'
        )
        self.assertEqual(parsed["result"], "UNCERTAIN")

    def test_returns_none_for_invalid_output(self) -> None:
        self.assertIsNone(parse_json_object("not-json"))

    def test_repairs_missing_colon_in_short_reason_field(self) -> None:
        parsed = parse_json_object(
            '{"result":"PASS","confidence":1.0,"reason ""}'
        )
        self.assertEqual(parsed["result"], "PASS")
        self.assertEqual(parsed["reason"], "")

    def test_recovers_complete_candidates_from_truncated_json(self) -> None:
        raw = (
            '{"objects":['
            '{"label":"保险丝","bbox":[10,20,30,40]},'
            '{"label":"螺丝","bbox":[50,60,70,80]},'
            '{"label":"未完成"'
        )
        objects = parse_partial_object_list(raw)
        self.assertEqual([item["label"] for item in objects], ["保险丝", "螺丝"])


if __name__ == "__main__":
    unittest.main()
