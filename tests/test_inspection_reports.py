import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.inspection import inspection_reports
from app.db.base import Base
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioExecution,
    ScenarioExecutionReview,
)


class InspectionReportsTests(unittest.TestCase):
    def test_groups_production_metrics_by_scene_name(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        scenario = InspectionScenario(
            code="HARNESS_LOCK",
            name="线束锁付",
            mode="VLM_DIRECT",
        )
        database.add(scenario)
        database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scenario.id,
            version="1.0",
            status="PUBLISHED",
        )
        database.add(version)
        database.flush()
        first_execution = ScenarioExecution(
            scenario_version_id=version.id,
            source="PRODUCTION",
            status="COMPLETED",
            result="NG",
        )
        second_execution = ScenarioExecution(
            scenario_version_id=version.id,
            source="PRODUCTION",
            status="COMPLETED",
            result="OK",
        )
        database.add_all((first_execution, second_execution))
        database.flush()
        database.add_all(
            (
                ScenarioExecutionReview(
                    execution_id=first_execution.id,
                    verdict="OK",
                ),
                ScenarioExecutionReview(
                    execution_id=second_execution.id,
                    manual_verdict="OK",
                ),
            )
        )
        database.commit()

        report = inspection_reports(days=30, database=database)

        self.assertEqual(report["overall"]["total"], 2)
        self.assertEqual(report["overall"]["ng_rate"], 0.5)
        self.assertEqual(report["overall"]["confirmed_count"], 1)
        self.assertEqual(report["overall"]["confirmed_accuracy"], 1.0)
        self.assertEqual(report["overall"]["review_agreement_rate"], 0.5)
        self.assertNotIn("line", report["dimensions"])
        self.assertNotIn("material", report["dimensions"])
        self.assertNotIn("operation", report["dimensions"])
        self.assertEqual(len(report["dimensions"]["scene"]), 1)
        self.assertEqual(report["dimensions"]["scene"][0]["key"], "线束锁付")
        self.assertEqual(report["dimensions"]["scene"][0]["scene_code"], "HARNESS_LOCK")
        self.assertNotIn("daily", report)
        self.assertGreaterEqual(len(report["monthly"]), 1)
        self.assertEqual(report["monthly"][-1]["ng"], 1)

        selected_day = datetime.utcnow().date().isoformat()
        one_day_report = inspection_reports(
            start_date=selected_day,
            end_date=selected_day,
            database=database,
        )
        self.assertEqual(one_day_report["start_date"], selected_day)
        self.assertEqual(one_day_report["end_date"], selected_day)
        self.assertEqual(len(one_day_report["monthly"]), 1)
        self.assertEqual(one_day_report["overall"]["total"], 2)
        database.close()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
