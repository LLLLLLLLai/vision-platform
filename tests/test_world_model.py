import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.inspection import InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services.world_model_service import sync_recipe_world_model


class ProductWorldModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="PDU001", name="PDU")
        station = Station(
            code="ST01",
            name="Assembly",
            line_code="L01",
            process_code="OP20",
        )
        self.database.add_all([product, station])
        self.database.flush()
        self.recipe = Recipe(
            code="L01_PDU001_OP20_CAM01_P01",
            name="Front",
            product_id=product.id,
            station_id=station.id,
            line_code="L01",
            material_code="PDU001",
            process_code="OP20",
            camera_code="CAM01",
            capture_index=1,
        )
        self.database.add(self.recipe)
        self.database.flush()
        self.roi = RegionOfInterest(
            recipe_id=self.recipe.id,
            code="FUSE_01",
            name="Fuse 01",
            object_type="FUSE",
            x_ratio=0.1,
            y_ratio=0.2,
            width_ratio=0.3,
            height_ratio=0.4,
        )
        self.database.add(self.roi)
        self.database.flush()
        self.database.add(
            InspectionItem(
                roi_id=self.roi.id,
                code="FUSE_01_EXISTENCE",
                name="Fuse existence",
                inspection_type="EXISTENCE",
                capability="REFERENCE_SIMILARITY",
                expected_json={"class_code": "FUSE_400A"},
                rule_json={"min_similarity": 0.9},
            )
        )
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_recipe_roi_maps_to_product_world_object(self) -> None:
        scene = sync_recipe_world_model(self.database, self.recipe)
        self.database.commit()
        self.database.refresh(self.roi)

        self.assertEqual(scene.code, "PDU001_WORLD")
        self.assertIsNotNone(self.roi.scene_object_id)
        world_object = self.roi.scene_object
        self.assertEqual(world_object.code, "FUSE_01")
        self.assertEqual(world_object.object_type, "FUSE")
        self.assertIn("CAM01:P01", world_object.geometry["views"])
        self.assertEqual(
            world_object.expected_state["rules"][0]["capability"],
            "REFERENCE_SIMILARITY",
        )


if __name__ == "__main__":
    unittest.main()
