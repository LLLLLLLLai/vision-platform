import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.recipe import Recipe, RecipeFeatureAnchor, RegionOfInterest
from app.models.system import Product, Station
from app.services.image_processing import (
    alignment_roi_offset_for_source,
    align_image_to_reference,
    align_image_with_anchor,
    aligned_roi_pixel_box,
    crop_roi,
)
from app.services.inspection_engine import InspectionEngine


class ImageAlignmentTests(unittest.TestCase):
    def test_phase_correlation_restores_small_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.jpg"
            source_path = root / "source.jpg"
            destination_path = root / "aligned.jpg"
            reference = np.full((240, 320, 3), 20, dtype=np.uint8)
            cv2.rectangle(reference, (70, 60), (230, 180), (180, 80, 30), -1)
            cv2.circle(reference, (150, 120), 22, (220, 220, 220), -1)
            transform = np.float32([[1, 0, 12], [0, 1, -8]])
            shifted = cv2.warpAffine(
                reference,
                transform,
                (320, 240),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), shifted)

            aligned_path, metadata = align_image_to_reference(
                str(source_path),
                str(reference_path),
                destination_path,
                max_shift_ratio=0.10,
                minimum_response=0.01,
                max_dimension=320,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertTrue(aligned_path and aligned_path.is_file())
            aligned = cv2.imread(str(aligned_path))
            self.assertLess(
                float(np.mean(np.abs(aligned.astype(np.int16) - reference.astype(np.int16)))),
                4.0,
            )

    def test_anchor_feature_alignment_restores_translation_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            source_path = root / "source.png"
            destination_path = root / "aligned.png"
            generator = np.random.default_rng(7)
            reference = generator.integers(0, 80, size=(320, 420, 3), dtype=np.uint8)
            cv2.rectangle(reference, (120, 95), (300, 235), (235, 235, 235), -1)
            for center in ((150, 125), (270, 125), (150, 205), (270, 205)):
                cv2.circle(reference, center, 16, (20, 20, 20), -1)
                cv2.line(reference, (center[0] - 10, center[1]), (center[0] + 10, center[1]), (255, 80, 40), 3)
            rotation = cv2.getRotationMatrix2D((210, 160), 3.0, 1.0)
            rotation[:, 2] += np.array([10.0, -7.0])
            shifted = cv2.warpAffine(
                reference,
                rotation,
                (420, 320),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), shifted)
            anchor = type(
                "Anchor",
                (),
                {
                    "code": "ANCHOR_01",
                    "x_ratio": 0.25,
                    "y_ratio": 0.20,
                    "width_ratio": 0.50,
                    "height_ratio": 0.55,
                    "padding": 0,
                },
            )()

            aligned_path, metadata = align_image_with_anchor(
                str(source_path),
                str(reference_path),
                anchor,
                destination_path,
                max_shift_ratio=0.15,
                search_margin_ratio=0.25,
                minimum_inliers=6,
                minimum_inlier_ratio=0.25,
                maximum_rotation_degrees=8.0,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertTrue(aligned_path and aligned_path.is_file())
            self.assertEqual(len(metadata["source_to_reference_transform"]), 2)
            aligned = cv2.imread(str(aligned_path))
            central = np.s_[35:285, 35:385]
            self.assertLess(
                float(np.mean(np.abs(aligned[central].astype(np.int16) - reference[central].astype(np.int16)))),
                18.0,
            )

    def test_anchor_refines_distant_roi_with_different_image_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            source_path = root / "source.png"
            generator = np.random.default_rng(91)
            reference = generator.integers(20, 210, size=(400, 600, 3), dtype=np.uint8)
            cv2.rectangle(reference, (70, 60), (210, 190), (20, 20, 20), 4)
            source_to_reference = np.float64(
                [[0.95, -0.012, 24.0], [0.01, 1.06, -21.0]]
            )
            source_resized = cv2.warpAffine(
                reference,
                cv2.invertAffineTransform(source_to_reference),
                (600, 400),
                borderMode=cv2.BORDER_REPLICATE,
            )
            source = cv2.resize(source_resized, (550, 450))
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), source)
            anchor = type(
                "Anchor",
                (),
                {
                    "code": "ANCHOR_LOCAL",
                    "x_ratio": 70 / 600,
                    "y_ratio": 60 / 400,
                    "width_ratio": 140 / 600,
                    "height_ratio": 130 / 400,
                    "padding": 0,
                },
            )()
            roi = type(
                "Roi",
                (),
                {
                    "x_ratio": 400 / 600,
                    "y_ratio": 250 / 400,
                    "width_ratio": 80 / 600,
                    "height_ratio": 80 / 400,
                    "padding": 0,
                },
            )()

            _, metadata = align_image_with_anchor(
                str(source_path),
                str(reference_path),
                anchor,
                root / "aligned.png",
                max_shift_ratio=0.15,
                search_margin_ratio=0.35,
                minimum_inliers=8,
                minimum_inlier_ratio=0.35,
                maximum_rotation_degrees=8.0,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertEqual(metadata["refinement"]["status"], "APPLIED")
            estimated = np.asarray(metadata["source_to_reference_transform"])
            self.assertLess(float(np.max(np.abs(estimated - source_to_reference))), 2.0)
            actual_box = aligned_roi_pixel_box(roi, 550, 450, metadata)
            reference_corners = np.array(
                [[[400, 250]], [[480, 250]], [[480, 330]], [[400, 330]]],
                dtype=np.float64,
            )
            expected_corners = cv2.transform(
                reference_corners,
                cv2.invertAffineTransform(source_to_reference),
            ).reshape(-1, 2)
            expected_corners[:, 0] *= 550 / 600
            expected_corners[:, 1] *= 450 / 400
            expected_box = (
                int(np.floor(expected_corners[:, 0].min())),
                int(np.floor(expected_corners[:, 1].min())),
                int(np.ceil(expected_corners[:, 0].max())),
                int(np.ceil(expected_corners[:, 1].max())),
            )
            self.assertTrue(
                all(abs(actual - expected) <= 3 for actual, expected in zip(actual_box, expected_box))
            )

    def test_global_alignment_fallback_requires_strong_anchor_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            source_path = root / "source.png"
            unrelated_path = root / "unrelated.png"
            generator = np.random.default_rng(109)
            reference = generator.integers(20, 230, size=(800, 1100, 3), dtype=np.uint8)
            source_to_reference = np.float64(
                [[0.97, -0.006, 24.0], [0.004, 1.05, -32.0]]
            )
            resized_source = cv2.warpAffine(
                reference,
                cv2.invertAffineTransform(source_to_reference),
                (1100, 800),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), cv2.resize(resized_source, (1050, 850)))
            cv2.imwrite(
                str(unrelated_path),
                generator.integers(20, 230, size=(850, 1050, 3), dtype=np.uint8),
            )
            anchor = type(
                "Anchor",
                (),
                {
                    "code": "ANCHOR_FALLBACK",
                    "x_ratio": 0.15,
                    "y_ratio": 0.20,
                    "width_ratio": 0.18,
                    "height_ratio": 0.18,
                    "padding": 0,
                },
            )()

            with patch(
                "app.services.image_processing.cv2.estimateAffinePartial2D",
                return_value=(None, None),
            ), patch(
                "app.services.image_processing._template_anchor_transform",
                return_value=(None, {"reason": "模板定位失败"}),
            ):
                aligned_path, metadata = align_image_with_anchor(
                    str(source_path),
                    str(reference_path),
                    anchor,
                    root / "aligned.png",
                    max_shift_ratio=0.10,
                    search_margin_ratio=0.35,
                    minimum_inliers=8,
                    minimum_inlier_ratio=0.35,
                    maximum_rotation_degrees=8.0,
                )
                unrelated_result, unrelated_metadata = align_image_with_anchor(
                    str(unrelated_path),
                    str(reference_path),
                    anchor,
                    root / "unrelated_aligned.png",
                    max_shift_ratio=0.10,
                    search_margin_ratio=0.35,
                    minimum_inliers=8,
                    minimum_inlier_ratio=0.35,
                    maximum_rotation_degrees=8.0,
                )

            self.assertEqual(metadata["status"], "APPLIED")
            self.assertEqual(metadata["method"], "FULL_IMAGE_ORB_AFFINE_FALLBACK")
            self.assertTrue(aligned_path and aligned_path.is_file())
            self.assertGreater(metadata["refinement"]["anchor_score"], 0.65)
            self.assertLess(
                float(np.max(np.abs(np.asarray(metadata["source_to_reference_transform"]) - source_to_reference))),
                3.0,
            )
            self.assertIsNone(unrelated_result)
            self.assertEqual(unrelated_metadata["status"], "SKIPPED")
            self.assertEqual(unrelated_metadata["global_fallback"]["status"], "SKIPPED")

    def test_affine_roi_crop_maps_reference_box_to_original_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.png"
            crop_path = root / "crop.png"
            source = np.zeros((600, 800, 3), dtype=np.uint8)
            cv2.imwrite(str(source_path), source)
            roi = type(
                "Roi",
                (),
                {
                    "x_ratio": 280 / 400,
                    "y_ratio": 180 / 300,
                    "width_ratio": 40 / 400,
                    "height_ratio": 50 / 300,
                    "padding": 0,
                },
            )()
            reference_to_resized_source = cv2.getRotationMatrix2D((200, 150), 5.0, 0.96)
            reference_to_resized_source[:, 2] += np.array([12.0, -8.0])
            alignment = {
                "status": "APPLIED",
                "reference_width": 400,
                "reference_height": 300,
                "source_width": 800,
                "source_height": 600,
                "source_to_reference_transform": cv2.invertAffineTransform(
                    reference_to_resized_source
                ).tolist(),
            }
            _, box = crop_roi(
                str(source_path),
                roi,
                crop_path,
                alignment=alignment,
            )
            reference_corners = np.array(
                [[[280, 180]], [[320, 180]], [[320, 230]], [[280, 230]]],
                dtype=np.float64,
            )
            expected_corners = cv2.transform(
                reference_corners, reference_to_resized_source
            ).reshape(-1, 2) * 2
            expected_box = (
                int(np.floor(expected_corners[:, 0].min())),
                int(np.floor(expected_corners[:, 1].min())),
                int(np.ceil(expected_corners[:, 0].max())),
                int(np.ceil(expected_corners[:, 1].max())),
            )
            self.assertEqual(box, expected_box)
            with Image.open(crop_path) as cropped:
                self.assertEqual(cropped.size, (box[2] - box[0], box[3] - box[1]))
            self.assertNotEqual(box, (560, 360, 640, 460))

    def test_invalid_affine_roi_does_not_fall_back_to_unaligned_crop(self) -> None:
        roi = type(
            "Roi",
            (),
            {
                "x_ratio": 0.5,
                "y_ratio": 0.5,
                "width_ratio": 0.1,
                "height_ratio": 0.1,
                "padding": 0,
            },
        )()
        alignment = {
            "reference_width": 400,
            "reference_height": 300,
            "source_width": 800,
            "source_height": 600,
            "source_to_reference_transform": [[0, 0, 0], [0, 0, 0]],
        }
        with self.assertRaisesRegex(ValueError, "仿射变换无效"):
            aligned_roi_pixel_box(roi, 800, 600, alignment)

    def test_anchor_offset_moves_roi_crop_on_original_source(self) -> None:
        """Production ROI pixels must move with the located anchor offset."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            source_path = root / "source.png"
            aligned_path = root / "aligned.png"
            reference_roi_path = root / "reference_roi.png"
            source_roi_path = root / "source_roi.png"
            generator = np.random.default_rng(23)
            reference = generator.integers(20, 90, size=(260, 360, 3), dtype=np.uint8)
            cv2.rectangle(reference, (72, 62), (172, 162), (228, 228, 228), -1)
            cv2.circle(reference, (112, 102), 24, (35, 35, 35), -1)
            cv2.line(reference, (88, 136), (156, 82), (0, 120, 255), 5)
            cv2.rectangle(reference, (240, 150), (305, 215), (40, 180, 60), -1)
            source = cv2.warpAffine(
                reference,
                np.float32([[1, 0, 14], [0, 1, -9]]),
                (360, 260),
                borderMode=cv2.BORDER_REPLICATE,
            )
            cv2.imwrite(str(reference_path), reference)
            cv2.imwrite(str(source_path), source)
            anchor = type(
                "Anchor",
                (),
                {
                    "code": "ANCHOR_TRANSLATION",
                    "x_ratio": 72 / 360,
                    "y_ratio": 62 / 260,
                    "width_ratio": 100 / 360,
                    "height_ratio": 100 / 260,
                    "padding": 0,
                },
            )()
            roi = type(
                "Roi",
                (),
                {
                    "x_ratio": 240 / 360,
                    "y_ratio": 150 / 260,
                    "width_ratio": 65 / 360,
                    "height_ratio": 65 / 260,
                    "padding": 0,
                },
            )()

            _, metadata = align_image_with_anchor(
                str(source_path),
                str(reference_path),
                anchor,
                aligned_path,
                max_shift_ratio=0.12,
                search_margin_ratio=0.20,
                minimum_inliers=6,
                minimum_inlier_ratio=0.25,
                maximum_rotation_degrees=8.0,
            )

            self.assertEqual(metadata["status"], "APPLIED")
            offset_x, offset_y = alignment_roi_offset_for_source(metadata)
            self.assertAlmostEqual(offset_x, 14, delta=2.5)
            self.assertAlmostEqual(offset_y, -9, delta=2.5)
            crop_roi(str(reference_path), roi, reference_roi_path)
            crop_roi(
                str(source_path),
                roi,
                source_roi_path,
                offset_x=offset_x,
                offset_y=offset_y,
            )
            reference_roi = cv2.imread(str(reference_roi_path))
            source_roi = cv2.imread(str(source_roi_path))
            self.assertEqual(reference_roi.shape, source_roi.shape)
            self.assertLess(
                float(np.mean(np.abs(reference_roi.astype(np.int16) - source_roi.astype(np.int16)))),
                8.0,
            )


class InspectionAlignmentSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_roi_uses_affine_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.png"
            reference_path = root / "reference.png"
            Image.new("RGB", (800, 600), "white").save(source_path)
            Image.new("RGB", (400, 300), "white").save(reference_path)
            recipe = Recipe(
                code="AFFINE-RECIPE",
                name="Affine Recipe",
                status="DRAFT",
                base_image_path=str(reference_path),
            )
            recipe.feature_anchor = RecipeFeatureAnchor(
                x_ratio=0.1,
                y_ratio=0.1,
                width_ratio=0.2,
                height_ratio=0.2,
                enabled=True,
            )
            roi = RegionOfInterest(
                code="ROI_AFFINE",
                name="Affine ROI",
                x_ratio=0.7,
                y_ratio=0.6,
                width_ratio=0.1,
                height_ratio=1 / 6,
                padding=0,
            )
            reference_to_resized_source = cv2.getRotationMatrix2D(
                (200, 150), 5.0, 0.96
            )
            reference_to_resized_source[:, 2] += np.array([12.0, -8.0])
            alignment = {
                "status": "APPLIED",
                "reference_width": 400,
                "reference_height": 300,
                "source_width": 800,
                "source_height": 600,
                "source_to_reference_transform": cv2.invertAffineTransform(
                    reference_to_resized_source
                ).tolist(),
            }
            database = Session(create_engine("sqlite://"))
            try:
                with (
                    patch("app.services.inspection_engine.PROJECT_ROOT", root),
                    patch(
                        "app.services.inspection_engine.align_image_with_anchor",
                        return_value=(root / "aligned.png", alignment),
                    ),
                ):
                    result = await InspectionEngine().test_draft_roi(
                        database,
                        recipe,
                        roi,
                        str(source_path),
                        rules=[],
                        review={},
                    )
                image_result = result["image_results"][0]
                self.assertEqual(
                    image_result["alignment"]["roi_correction"]["mode"],
                    "SOURCE_ROI_AFFINE",
                )
                expected_box = aligned_roi_pixel_box(roi, 800, 600, alignment)
                with Image.open(image_result["roi_image_path"]) as cropped:
                    self.assertEqual(
                        cropped.size,
                        (expected_box[2] - expected_box[0], expected_box[3] - expected_box[1]),
                    )
            finally:
                database.close()

    async def test_anchor_failure_blocks_roi_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.jpg"
            reference_path = root / "reference.jpg"
            Image.new("RGB", (320, 240), "white").save(source_path)
            Image.new("RGB", (320, 240), "white").save(reference_path)
            database_engine = create_engine("sqlite://")
            database = Session(database_engine)
            try:
                Base.metadata.create_all(database_engine)
                product = Product(code="MAT-ANCHOR", name="Alignment Material")
                station = Station(code="ST-ANCHOR", name="Alignment Station")
                database.add_all([product, station])
                database.flush()
                recipe = Recipe(
                    code="ANCHOR-RECIPE",
                    name="Anchor Recipe",
                    status="PUBLISHED",
                    product_id=product.id,
                    station_id=station.id,
                    base_image_path=str(reference_path),
                )
                database.add(recipe)
                database.flush()
                database.add_all(
                    [
                        RecipeFeatureAnchor(
                            recipe_id=recipe.id,
                            x_ratio=0.10,
                            y_ratio=0.10,
                            width_ratio=0.20,
                            height_ratio=0.20,
                        ),
                        RegionOfInterest(
                            recipe_id=recipe.id,
                            code="ROI_BLOCKED",
                            name="Blocked ROI",
                            x_ratio=0.50,
                            y_ratio=0.50,
                            width_ratio=0.20,
                            height_ratio=0.20,
                        ),
                    ]
                )
                database.commit()

                with patch(
                    "app.services.inspection_engine.align_image_with_anchor",
                    return_value=(
                        None,
                        {"status": "SKIPPED", "reason": "定位特征点模板匹配置信度不足"},
                    ),
                ):
                    result = await InspectionEngine().execute(
                        database,
                        recipe,
                        sn="SN-ANCHOR",
                        image_paths=[str(source_path)],
                        artifact_root=root / "artifacts",
                    )

                self.assertEqual(result["result"], "ERROR")
                image_result = result["image_results"][0]
                self.assertEqual(image_result["alignment"]["crop_mode"], "BLOCKED")
                self.assertEqual(image_result["inspection_items"][0]["roi_code"], "IMAGE_ALIGNMENT")
                self.assertFalse((root / "artifacts" / "image_1" / "ROI_BLOCKED.jpg").exists())
            finally:
                database.close()
                database_engine.dispose()


if __name__ == "__main__":
    unittest.main()
