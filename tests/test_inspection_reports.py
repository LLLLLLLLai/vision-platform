import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.inspection import inspection_reports
from app.db.base import Base
from app.models.inspection import DetectionItemResult, DetectionTask, InspectionItem
from app.models.recipe import Recipe, RegionOfInterest


class InspectionReportsTests(unittest.TestCase):
    def test_groups_process_metrics_by_business_dimensions(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        recipe = Recipe(
            code="LINE01_MAT01_OP10_CAM01_P01",
            name="Recipe",
            product_id=1,
            station_id=1,
            line_code="LINE01",
            material_code="MAT01",
            process_code="OP10",
            camera_code="CAM01",
        )
        database.add(recipe)
        database.flush()
        roi = RegionOfInterest(
            recipe_id=recipe.id,
            code="FUSE_01",
            name="保险丝",
            object_type="FUSE",
            x_ratio=0.1,
            y_ratio=0.1,
            width_ratio=0.2,
            height_ratio=0.2,
        )
        database.add(roi)
        database.flush()
        item = InspectionItem(
            roi_id=roi.id,
            code="FUSE_01_EXISTENCE",
            name="存在校验",
            inspection_type="EXISTENCE",
            capability="REFERENCE_SIMILARITY",
        )
        database.add(item)
        database.flush()
        task = DetectionTask(
            request_id="report-test",
            sn="SN001",
            recipe_id=recipe.id,
            recipe_version="1.0",
            status="NG",
        )
        database.add(task)
        database.flush()
        database.add(
            DetectionItemResult(
                task_id=task.id,
                image_path="image.jpg",
                roi_id=roi.id,
                inspection_item_id=item.id,
                status="NG",
            )
        )
        database.commit()

        report = inspection_reports(days=30, database=database)

        self.assertEqual(report["overall"]["total"], 1)
        self.assertEqual(report["overall"]["ng_rate"], 1.0)
        self.assertEqual(report["dimensions"]["line"][0]["key"], "LINE01")
        self.assertNotIn("item", report["dimensions"])
        self.assertEqual(len(report["daily"]), 30)
        self.assertEqual(report["daily"][-1]["ng"], 1)
        self.assertIsNone(report["overall"]["confirmed_accuracy"])

        selected_day = datetime.utcnow().date().isoformat()
        one_day_report = inspection_reports(
            start_date=selected_day,
            end_date=selected_day,
            database=database,
        )
        self.assertEqual(one_day_report["start_date"], selected_day)
        self.assertEqual(one_day_report["end_date"], selected_day)
        self.assertEqual(len(one_day_report["daily"]), 1)
        self.assertEqual(one_day_report["overall"]["total"], 1)
        database.close()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
