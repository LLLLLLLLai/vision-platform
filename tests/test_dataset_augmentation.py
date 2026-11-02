from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from app.services.dataset_augmentation import (
    DatasetAugmentationError,
    augment_image_and_annotation,
    validate_augmentation_options,
)


class DatasetAugmentationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.image_path = Path(self.temporary_directory.name) / "source.png"
        image = Image.new("RGB", (100, 100), "white")
        image.paste((220, 20, 20), (10, 20, 30, 30))
        image.save(self.image_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_detection_box_tracks_horizontal_flip(self) -> None:
        image, annotation, applied = augment_image_and_annotation(
            self.image_path,
            {"boxes": [{"label": "wire", "x": 0.1, "y": 0.2, "width": 0.2, "height": 0.1}]},
            "DETECTION",
            {"horizontal_flip": True},
            seed=12,
        )

        self.assertEqual(image.size, (100, 100))
        self.assertAlmostEqual(annotation["boxes"][0]["x"], 0.7)
        self.assertAlmostEqual(annotation["boxes"][0]["y"], 0.2)
        self.assertEqual(applied["horizontal_flip"], True)
        self.assertEqual(image.getpixel((80, 25)), (220, 20, 20))

    def test_detection_box_tracks_ninety_degree_rotation(self) -> None:
        image, annotation, _applied = augment_image_and_annotation(
            self.image_path,
            {"boxes": [{"label": "wire", "x": 10, "y": 20, "width": 20, "height": 10}]},
            "DETECTION",
            {"rotation_min_degrees": 90, "rotation_max_degrees": 90},
            seed=21,
        )

        self.assertEqual(annotation["boxes"][0]["x"], 20)
        self.assertEqual(annotation["boxes"][0]["y"], 70)
        self.assertEqual(annotation["boxes"][0]["width"], 10)
        self.assertEqual(annotation["boxes"][0]["height"], 20)
        self.assertEqual(image.getpixel((25, 80)), (220, 20, 20))

    def test_segmentation_polygon_tracks_vertical_flip(self) -> None:
        _image, annotation, _applied = augment_image_and_annotation(
            self.image_path,
            {"segments": [{"label": "wire", "points": [[0.1, 0.2], [0.3, 0.2], [0.2, 0.4]]}]},
            "SEGMENTATION",
            {"vertical_flip": True},
            seed=34,
        )

        self.assertEqual(
            annotation["segments"][0]["points"],
            [[0.1, 0.8], [0.3, 0.8], [0.2, 0.6]],
        )

    def test_classification_keeps_label_and_photometric_seed_is_reproducible(self) -> None:
        arguments = (
            self.image_path,
            {"label": "wire"},
            "CLASSIFICATION",
            {"gaussian_noise_std_min": 5, "gaussian_noise_std_max": 5},
        )
        first_image, first_annotation, first_applied = augment_image_and_annotation(*arguments, seed=56)
        second_image, second_annotation, second_applied = augment_image_and_annotation(*arguments, seed=56)

        self.assertEqual(first_annotation, {"label": "wire"})
        self.assertEqual(first_annotation, second_annotation)
        self.assertEqual(first_applied, second_applied)
        self.assertEqual(first_image.tobytes(), second_image.tobytes())
        self.assertNotEqual(first_image.getpixel((0, 0)), (255, 255, 255))

    def test_rejects_noop_and_inverted_ranges(self) -> None:
        with self.assertRaises(DatasetAugmentationError):
            validate_augmentation_options({})
        with self.assertRaises(DatasetAugmentationError):
            validate_augmentation_options({"brightness_min": 1.2, "brightness_max": 0.8})


if __name__ == "__main__":
    unittest.main()
