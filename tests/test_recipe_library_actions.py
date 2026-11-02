import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.configuration import delete_recipe, recipe_detail
from app.db.base import Base
from app.models.recipe import Recipe
from app.models.system import Product, Station


class RecipeLibraryActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="MAT01", name="物料 MAT01")
        station = Station(code="L01_OP10", name="L01 · OP10")
        self.database.add_all((product, station))
        self.database.flush()
        self.draft = Recipe(
            code="L01_MAT01_OP10_CAM01_P01",
            name="草稿配方",
            product_id=product.id,
            station_id=station.id,
            status="DRAFT",
        )
        self.published = Recipe(
            code="L01_MAT01_OP10_CAM02_P01",
            name="已发布配方",
            product_id=product.id,
            station_id=station.id,
            status="PUBLISHED",
        )
        self.database.add_all((self.draft, self.published))
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_delete_recipe_is_soft_and_hides_details(self) -> None:
        result = delete_recipe(self.draft.id, database=self.database)

        self.database.refresh(self.draft)
        self.assertEqual(result, {"deleted": True, "was_published": False})
        self.assertTrue(self.draft.is_deleted)
        self.assertEqual(self.draft.status, "ARCHIVED")
        with self.assertRaises(HTTPException) as context:
            recipe_detail(self.draft.id, database=self.database)
        self.assertEqual(context.exception.status_code, 404)

    def test_delete_published_recipe_stops_it_from_being_active(self) -> None:
        result = delete_recipe(self.published.id, database=self.database)

        self.database.refresh(self.published)
        self.assertEqual(result, {"deleted": True, "was_published": True})
        self.assertTrue(self.published.is_deleted)
        self.assertEqual(self.published.status, "ARCHIVED")


if __name__ == "__main__":
    unittest.main()
