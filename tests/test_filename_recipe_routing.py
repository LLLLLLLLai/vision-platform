import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.api.routes.inspection import (
    PublicDetectRequest,
    execute_filename_routed_inspection,
    filename_contains_signature,
    image_filename_key,
    match_published_recipe_by_parameters,
    match_published_recipe_by_filename,
    normalize_filename_part,
    parse_camera_picture_from_filename,
)
from app.models.recipe import Recipe
from app.models.system import Product, Station


class FilenameRecipeRoutingTest(unittest.TestCase):
    def test_normalizes_common_delimiters(self) -> None:
        self.assertEqual(
            normalize_filename_part("LINE-01_MAT.001 OP20"),
            "LINE_01_MAT_001_OP20",
        )

    def test_extracts_windows_filename(self) -> None:
        self.assertEqual(
            image_filename_key(r"D:\images\LINE01-MAT001-OP20-CAM01-P1_001.jpg"),
            "LINE01_MAT001_OP20_CAM01_P1_001",
        )

    def test_matches_signature_on_token_boundaries(self) -> None:
        filename = "20260729_LINE01_MAT001_OP20_CAM01_P1_001"
        self.assertTrue(
            filename_contains_signature(
                filename,
                "LINE01_MAT001_OP20_CAM01_P1",
            )
        )

    def test_capture_one_does_not_match_capture_ten(self) -> None:
        filename = "LINE01_MAT001_OP20_CAM01_P10_001"
        self.assertFalse(
            filename_contains_signature(
                filename,
                "LINE01_MAT001_OP20_CAM01_P1",
            )
        )

    def test_parses_camera_and_picture_from_compact_filename(self) -> None:
        self.assertEqual(
            parse_camera_picture_from_filename(
                r"C:\images\ASSY-CAMERA1PICTURE1.png"
            ),
            ("CAMERA1", 1),
        )

    def test_public_request_accepts_new_and_legacy_field_names(self) -> None:
        modern = PublicDetectRequest.model_validate(
            {
                "sn": "SN001",
                "image_paths": ["image.png"],
                "line": "L01",
                "materialcode": "MAT001",
                "operation": "OP20",
                "camera": "CAMERA1",
                "picture": 1,
            }
        )
        legacy = PublicDetectRequest.model_validate(
            {
                "sn": "SN001",
                "image_paths": ["image.png"],
                "line_code": "L01",
                "material_code": "MAT001",
                "process_code": "OP20",
                "camera_code": "CAMERA1",
                "capture_index": 1,
            }
        )
        self.assertEqual(modern.line_code, legacy.line_code)
        self.assertEqual(modern.material_code, legacy.material_code)
        self.assertEqual(modern.process_code, legacy.process_code)
        self.assertEqual(modern.camera_code, legacy.camera_code)
        self.assertEqual(modern.capture_index, legacy.capture_index)


class RecipeDatabaseRoutingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database_engine = create_engine("sqlite://")
        Base.metadata.create_all(self.database_engine)
        self.database = Session(self.database_engine)
        product = Product(code="MAT001", name="Material")
        station = Station(
            code="ST01",
            name="Station",
            line_code="LINE01",
            process_code="OP20",
        )
        self.database.add_all([product, station])
        self.database.flush()
        self.database.add_all(
            [
                Recipe(
                    code="LINE01-MAT001-OP20-CAM01-P1",
                    name="Photo 1",
                    status="PUBLISHED",
                    line_code="LINE01",
                    material_code="MAT001",
                    process_code="OP20",
                    product_id=product.id,
                    station_id=station.id,
                    camera_code="CAM01",
                    capture_index=1,
                ),
                Recipe(
                    code="LINE01-MAT001-OP20-CAM01-P2",
                    name="Photo 2",
                    status="PUBLISHED",
                    line_code="LINE01",
                    material_code="MAT001",
                    process_code="OP20",
                    product_id=product.id,
                    station_id=station.id,
                    camera_code="CAM01",
                    capture_index=2,
                ),
                Recipe(
                    code="LINE01-MAT001-OP20-CAM01-P3",
                    name="Draft",
                    status="DRAFT",
                    line_code="LINE01",
                    material_code="MAT001",
                    process_code="OP20",
                    product_id=product.id,
                    station_id=station.id,
                    camera_code="CAM01",
                    capture_index=3,
                ),
                Recipe(
                    code="LINE01-MAT001-OP20-CAMERA1-P1",
                    name="Compact filename photo 1",
                    status="PUBLISHED",
                    line_code="LINE01",
                    material_code="MAT001",
                    process_code="OP20",
                    product_id=product.id,
                    station_id=station.id,
                    camera_code="CAMERA1",
                    capture_index=1,
                ),
            ]
        )
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.database_engine.dispose()

    def test_matches_only_published_recipe(self) -> None:
        recipe = match_published_recipe_by_filename(
            self.database,
            r"D:\images\LINE01_MAT001_OP20_CAM01_P2_001.jpg",
        )
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.capture_index, 2)
        self.assertIsNone(
            match_published_recipe_by_filename(
                self.database,
                r"D:\images\LINE01_MAT001_OP20_CAM01_P3_001.jpg",
            )
        )

    def test_structured_parameters_match_business_recipe(self) -> None:
        recipe = match_published_recipe_by_parameters(
            self.database,
            PublicDetectRequest(
                sn="SN001",
                line_code="LINE01",
                material_code="MAT001",
                process_code="OP20",
                camera_code="CAM01",
                capture_index=1,
                image_paths=["current.jpg"],
            ),
        )
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.code, "LINE01-MAT001-OP20-CAM01-P1")

        draft = match_published_recipe_by_parameters(
            self.database,
            PublicDetectRequest(
                sn="SN001",
                line_code="LINE01",
                material_code="MAT001",
                process_code="OP20",
                camera_code="CAM01",
                capture_index=3,
                image_paths=["current.jpg"],
            ),
        )
        self.assertIsNone(draft)

    async def test_routes_and_aggregates_multiple_recipes(self) -> None:
        async def fake_execute(
            database: Session,
            recipe: Recipe,
            sn: str,
            image_paths: list[str],
            request_id: str | None = None,
        ) -> dict:
            result = "NG" if recipe.capture_index == 2 else "OK"
            return {
                "code": 0,
                "message": "success",
                "result": result,
                "image_paths": [f"result-{recipe.capture_index}.jpg"],
            }

        payload = PublicDetectRequest(
            sn="SN001",
            image_paths=[
                "LINE01-MAT001-OP20-CAM01-P1_a.jpg",
                "LINE01-MAT001-OP20-CAM01-P2_b.jpg",
                "LINE01-MAT001-OP20-CAM01-P1_c.jpg",
            ],
        )
        with patch(
            "app.api.routes.inspection.engine.execute",
            new=AsyncMock(side_effect=fake_execute),
        ) as execute_mock:
            response = await execute_filename_routed_inspection(
                payload,
                self.database,
            )

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["result"], "NG")
        self.assertEqual(
            response["image_paths"],
            ["result-1.jpg", "result-2.jpg"],
        )
        self.assertEqual(execute_mock.await_count, 2)
        first_group_paths = execute_mock.await_args_list[0].kwargs["image_paths"]
        self.assertEqual(len(first_group_paths), 2)

    async def test_routes_with_business_parameters_and_filename_camera(self) -> None:
        payload = PublicDetectRequest(
            sn="SN002",
            image_paths=["ASSY-CAMERA1PICTURE1.png"],
            line="LINE01",
            materialcode="MAT001",
            operation="OP20",
        )
        with patch(
            "app.api.routes.inspection.engine.execute",
            new=AsyncMock(
                return_value={
                    "code": 0,
                    "message": "success",
                    "result": "OK",
                    "image_paths": ["result.jpg"],
                }
            ),
        ) as execute_mock:
            response = await execute_filename_routed_inspection(
                payload,
                self.database,
            )

        self.assertEqual(response["code"], 0)
        matched_recipe = execute_mock.await_args.args[1]
        self.assertEqual(matched_recipe.camera_code, "CAMERA1")
        self.assertEqual(matched_recipe.capture_index, 1)


if __name__ == "__main__":
    unittest.main()
