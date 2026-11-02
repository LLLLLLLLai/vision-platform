from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from app.services.scene_result_images import SceneResultSnapshot, build_scene_result_image


class SceneResultImagesTests(unittest.TestCase):
    def test_last_annotation_node_image_is_used_as_result(self) -> None:
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            annotated_image = Path(directory) / "uploads" / "scene_annotations" / "987654320" / "boxes.jpg"
            annotated_image.parent.mkdir(parents=True)
            Image.new("RGB", (64, 48), "black").save(annotated_image)
            execution = SceneResultSnapshot(
                id=987654320,
                result="NG",
                output_json={"traces": [{
                    "node_type": "IMAGE_ANNOTATE",
                    "output": {"image_path": str(annotated_image)},
                }]},
            )
            with patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)):
                result_path = build_scene_result_image(execution, str(input_image))
                with Image.open(result_path) as rendered:
                    self.assertLess(max(rendered.getpixel((40, 40))), 20)

    def test_detection_boxes_are_drawn_when_model_used_input_image(self) -> None:
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            execution = SceneResultSnapshot(
                id=987654321,
                result="OK",
                output_json={"traces": [{
                    "node_type": "VISION_MODEL",
                    "input": {"image_path": str(input_image)},
                    "output": {"objects": [{"bbox": [12, 12, 40, 35], "label": "wire", "confidence": 0.9}]},
                }]},
            )
            with (
                patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)),
                patch("app.services.scenario_runtime.PROJECT_ROOT", Path(directory)),
            ):
                result_path = build_scene_result_image(execution, str(input_image))
                with Image.open(result_path) as rendered:
                    self.assertNotEqual(rendered.getpixel((12, 20)), (255, 255, 255))
                    self.assertEqual(rendered.size, (64, 48))

    def test_crop_local_boxes_are_not_drawn_on_full_image(self) -> None:
        with TemporaryDirectory() as directory:
            input_image = Path(directory) / "input.jpg"
            crop_image = Path(directory) / "crop.jpg"
            Image.new("RGB", (64, 48), "white").save(input_image)
            Image.new("RGB", (50, 48), "white").save(crop_image)
            execution = SceneResultSnapshot(
                id=987654322,
                result="NG",
                output_json={"traces": [{
                    "node_type": "VISION_MODEL",
                    "input": {"image_path": str(crop_image)},
                    "output": {"objects": [{"bbox": [12, 35, 25, 45], "label": "wire"}]},
                }]},
            )
            with patch("app.services.scene_result_images.PROJECT_ROOT", Path(directory)):
                result_path = build_scene_result_image(execution, str(input_image))
                with Image.open(result_path) as rendered:
                    red, green, blue = rendered.getpixel((12, 35))
                    self.assertGreater(min(red, green, blue), 230)
