import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import configuration
from app.db.base import Base
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceImage


class ReferenceCaptureTest(unittest.IsolatedAsyncioTestCase):
    async def test_saves_reference_when_embedding_service_is_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "recipe.jpg"
            Image.new("RGB", (320, 240), (80, 90, 100)).save(image_path)
            database_engine = create_engine("sqlite://")
            Base.metadata.create_all(database_engine)
            database = Session(database_engine)
            recipe = Recipe(
                code="LINE_MAT_OP_CAM_P01",
                name="Recipe",
                product_id=1,
                station_id=1,
                base_image_path=str(image_path),
            )
            database.add(recipe)
            database.flush()
            roi = RegionOfInterest(
                recipe_id=recipe.id,
                code="HARNESS_01",
                name="HARNESS_01",
                object_type="HARNESS",
                x_ratio=0.1,
                y_ratio=0.1,
                width_ratio=0.4,
                height_ratio=0.4,
            )
            database.add(roi)
            database.commit()

            class FailingAlgorithms:
                async def embedding(self, _image_path: str):
                    raise RuntimeError("DINOv2 unavailable")

            with (
                patch.object(configuration, "PROJECT_ROOT", root),
                patch.object(configuration, "algorithm_client", FailingAlgorithms()),
            ):
                result = await configuration.capture_roi_reference(roi.id, database)
                preview = configuration._latest_auto_reference(database, recipe, roi)

            reference = database.scalar(select(ReferenceImage))
            self.assertEqual(result["embedding_status"], "PENDING_RETRY")
            self.assertIn("DINOv2 unavailable", result["embedding_warning"])
            self.assertEqual(reference.quality_status, "PENDING_RETRY")
            self.assertTrue(Path(reference.image_path).is_file())
            self.assertIsNotNone(preview)
            self.assertEqual(preview["image_url"], result["image_url"])
            self.assertFalse(preview["detection_ready"])
            database.close()
            database_engine.dispose()


if __name__ == "__main__":
    unittest.main()
