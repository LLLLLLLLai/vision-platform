import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.api.routes.configuration import delete_roi, create_recipe_draft
from app.db.base import Base
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.models.intelligence import InspectionScenario, InspectionScenarioVersion, RoiScenarioBinding, ScenarioExecution


class DraftRoiDeleteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="MAT", name="MAT")
        station = Station(code="STA", name="STA")
        scene = InspectionScenario(code="SCENE", name="SCENE", mode="VLM")
        self.database.add_all([product, station, scene])
        self.database.flush()
        self.recipe = Recipe(code="TEST", name="TEST", status="DRAFT", product_id=product.id, station_id=station.id)
        version = InspectionScenarioVersion(scenario_id=scene.id, version="1", status="PUBLISHED")
        self.database.add_all([self.recipe, version])
        self.database.flush()
        self.roi = RegionOfInterest(recipe_id=self.recipe.id, code="ROI1", name="ROI1", x_ratio=0.1, y_ratio=0.1, width_ratio=0.2, height_ratio=0.2)
        self.database.add(self.roi)
        self.database.flush()
        self.binding = RoiScenarioBinding(roi_id=self.roi.id, scenario_version_id=version.id, enabled=True)
        self.history = ScenarioExecution(roi_id=self.roi.id, scenario_version_id=version.id, status="COMPLETED", result="NG", output_json={"history": "retained"})
        self.database.add_all([self.binding, self.history])
        self.database.commit()

    def tearDown(self):
        self.database.close()
        self.engine.dispose()

    def test_delete_bound_draft_roi_keeps_history_and_hides_roi(self):
        self.assertEqual(len(self.recipe.rois), 1)
        self.assertTrue(delete_roi(self.roi.id, self.database)["deleted"])
        self.assertEqual(self.recipe.rois, [])
        self.assertTrue(self.roi.is_deleted)
        self.assertFalse(self.binding.enabled)
        self.database.refresh(self.history)
        self.assertEqual(self.history.output_json, {"history": "retained"})
        self.assertEqual(self.history.roi_id, self.roi.id)
        with self.assertRaises(HTTPException) as error:
            delete_roi(self.roi.id, self.database)
        self.assertEqual(error.exception.status_code, 404)

    def test_published_cannot_delete_draft_does_not_change_published(self):
        self.recipe.status = "PUBLISHED"
        self.database.commit()
        with self.assertRaises(HTTPException):
            delete_roi(self.roi.id, self.database)
        draft = create_recipe_draft(self.recipe.id, database=self.database)
        draft_recipe = self.database.get(Recipe, draft["id"])
        delete_roi(draft_recipe.rois[0].id, self.database)
        self.assertEqual(len(self.recipe.rois), 1)
        self.assertEqual(len(draft_recipe.rois), 0)
