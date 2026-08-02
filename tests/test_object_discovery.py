import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from app.services.discovery_service import (
    build_localization_groups,
    enrich_harness_connection_candidates,
    normalize_discovery_result,
    normalize_grounding_result,
    normalize_inventory_result,
)


class ObjectDiscoveryNormalizationTest(unittest.TestCase):
    def test_normalizes_qwen_boxes_to_ratios(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "objects": [
                        {
                            "label": "保险丝 1",
                            "object_type": "FUSE",
                            "prompt_en": "high voltage fuse",
                            "bbox": [100, 200, 300, 600],
                            "confidence": 0.92,
                        }
                    ]
                }
            }
        }

        candidates = normalize_discovery_result(response)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["object_type"], "FUSE")
        self.assertEqual(candidates[0]["x_ratio"], 0.1)
        self.assertEqual(candidates[0]["width_ratio"], 0.2)
        self.assertEqual(candidates[0]["height_ratio"], 0.4)

    def test_deduplicates_nearly_identical_boxes(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "objects": [
                        {
                            "label": "螺丝",
                            "object_type": "SCREW",
                            "bbox": [100, 100, 200, 200],
                            "confidence": 0.6,
                        },
                        {
                            "label": "固定螺丝",
                            "object_type": "SCREW",
                            "bbox": [102, 102, 198, 198],
                            "confidence": 0.9,
                        },
                    ]
                }
            }
        }

        candidates = normalize_discovery_result(response)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["label"], "螺丝 01")
        self.assertEqual(candidates[0]["source_label"], "固定螺丝")
        self.assertEqual(candidates[0]["candidate_id"], "CAND_1")

    def test_ignores_invalid_boxes_and_unknown_types_are_safe(self) -> None:
        response = {
            "result": {
                "parsed": {
                    "objects": [
                        {"label": "无效", "object_type": "FUSE", "bbox": [1, 1, 1, 1]},
                        {
                            "label": "其他零件",
                            "object_type": "CUSTOM_PART",
                            "bbox": [0.2, 0.3, 0.4, 0.5],
                        },
                    ]
                }
            }
        }

        candidates = normalize_discovery_result(response)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["object_type"], "OBJECT")

    def test_inventory_drives_grounding_labels_and_count_filter(self) -> None:
        inventory_response = {
            "result": {
                "parsed": {
                    "objects": [
                        {
                            "label": "保险丝",
                            "object_type": "FUSE",
                            "prompt_en": "high voltage fuse",
                            "expected_count": 2,
                        }
                    ]
                }
            }
        }
        inventory = normalize_inventory_result(inventory_response)
        grounding_response = {
            "image_width": 1000,
            "image_height": 500,
            "objects": [
                {"label": "high voltage fuse", "score": 0.93, "bbox": [100, 100, 200, 300]},
                {"label": "high voltage fuse", "score": 0.91, "bbox": [300, 100, 400, 300]},
                {"label": "high voltage fuse", "score": 0.40, "bbox": [305, 105, 395, 295]},
            ],
        }

        candidates = normalize_grounding_result(grounding_response, inventory)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["label"], "保险丝 01")
        self.assertEqual(candidates[1]["label"], "保险丝 02")
        self.assertEqual(candidates[0]["engine"], "GROUNDING_DINO")

    def test_builds_harness_and_fastener_keypoint_groups(self) -> None:
        inventory = [
            {
                "label": "PCBA",
                "object_type": "PCBA",
                "prompt_en": "green pcb",
                "expected_count": 1,
            }
        ]

        groups = build_localization_groups(
            inventory,
            ["wiring harness connection", "screw"],
        )

        self.assertEqual(
            [group["name"] for group in groups],
            [
                "GENERAL_OBJECTS",
                "HARNESS_SEGMENTATION_SEEDS",
                "HARNESS_KEYPOINTS",
                "FASTENER_KEYPOINTS",
            ],
        )
        harness_prompts = {
            item["prompt_en"] for item in groups[2]["inventory"]
        }
        self.assertIn("cable lug attached to bolt", harness_prompts)
        self.assertIn("wire harness connector plugged into socket", harness_prompts)
        segmentation_prompts = {
            item["prompt_en"] for item in groups[1]["inventory"]
        }
        self.assertIn("black wiring harness cable", segmentation_prompts)

    def test_specialized_targets_reject_oversized_harness_boxes(self) -> None:
        groups = build_localization_groups([], ["wiring harness", "screw"])
        specialized_inventory = [
            item
            for group in groups
            for item in group["inventory"]
        ]
        response = {
            "image_width": 1000,
            "image_height": 500,
            "objects": [
                {
                    "label": "cable lug attached to bolt",
                    "score": 0.31,
                    "bbox": [100, 100, 220, 210],
                },
                {
                    "label": "cable lug attached to bolt",
                    "score": 0.41,
                    "bbox": [0, 0, 900, 450],
                },
                {
                    "label": "silver hex bolt head",
                    "score": 0.26,
                    "bbox": [500, 100, 550, 150],
                },
            ],
        }

        candidates = normalize_grounding_result(
            response,
            specialized_inventory,
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["target_kind"], "HARNESS_BOLTED_LUG")
        self.assertEqual(candidates[1]["target_kind"], "HEX_BOLT_HEAD")
        self.assertTrue(all(item["review_status"] == "RECOMMENDED" for item in candidates))

    def test_derives_orange_harness_connection_near_screw(self) -> None:
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "assembly.png"
            image = Image.new("RGB", (1000, 500), "#20242a")
            draw = ImageDraw.Draw(image)
            draw.rectangle((430, 210, 600, 250), fill="#f97316")
            image.save(image_path)
            screw_candidate = {
                "candidate_id": "CAND_1",
                "label": "六角螺栓头 01",
                "object_type": "SCREW",
                "target_kind": "HEX_BOLT_HEAD",
                "confidence": 0.24,
                "bbox": [0.48, 0.40, 0.53, 0.50],
                "x_ratio": 0.48,
                "y_ratio": 0.40,
                "width_ratio": 0.05,
                "height_ratio": 0.10,
            }

            candidates = enrich_harness_connection_candidates(
                str(image_path),
                [screw_candidate],
            )

        harness = [item for item in candidates if item["object_type"] == "HARNESS"]
        self.assertEqual(len(harness), 1)
        self.assertEqual(harness[0]["target_kind"], "HARNESS_BOLTED_LUG")
        self.assertGreater(harness[0]["orange_ratio"], 0.015)


if __name__ == "__main__":
    unittest.main()
