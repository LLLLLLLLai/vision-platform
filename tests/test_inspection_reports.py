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

    def test_uncertain_manual_review_is_not_counted_as_confirmed_truth(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        scenario = InspectionScenario(code="OCR", name="铭牌识别", mode="VLM_DIRECT")
        database.add(scenario)
        database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scenario.id,
            version="1.0",
            status="PUBLISHED",
        )
        database.add(version)
        database.flush()
        execution = ScenarioExecution(
            scenario_version_id=version.id,
            source="PRODUCTION",
            status="COMPLETED",
            result="OK",
        )
        database.add(execution)
        database.flush()
        database.add(
            ScenarioExecutionReview(
                execution_id=execution.id,
                manual_verdict="UNCERTAIN",
            )
        )
        database.commit()

        report = inspection_reports(days=30, database=database)

        self.assertEqual(report["overall"]["confirmed_count"], 0)
        self.assertIsNone(report["overall"]["confirmed_accuracy"])
        self.assertEqual(report["overall"]["manual_confirmation_coverage"], 0.0)
        scene = report["dimensions"]["scene"][0]
        self.assertEqual(scene["human_reviewed_count"], 1)
        self.assertEqual(scene["confirmed_count"], 0)
        database.close()
        engine.dispose()

    def test_exposes_scene_quality_risk_and_latency_metrics(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        database = Session(engine)
        scenario = InspectionScenario(code="LOCK", name="线束锁付", mode="VLM_DIRECT")
        database.add(scenario)
        database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scenario.id,
            version="1.0",
            status="PUBLISHED",
        )
        database.add(version)
        database.flush()
        executions = [
            ScenarioExecution(scenario_version_id=version.id, source="PRODUCTION", status="COMPLETED", result="OK", elapsed_ms=10),
            ScenarioExecution(scenario_version_id=version.id, source="PRODUCTION", status="COMPLETED", result="NG", elapsed_ms=50),
            ScenarioExecution(scenario_version_id=version.id, source="PRODUCTION", status="FAILED", result="ERROR", elapsed_ms=100),
        ]
        database.add_all(executions)
        database.flush()
        database.add_all(
            (
                ScenarioExecutionReview(execution_id=executions[0].id, manual_verdict="NG"),
                ScenarioExecutionReview(execution_id=executions[1].id, manual_verdict="OK"),
            )
        )
        database.commit()

        report = inspection_reports(days=30, database=database)
        scene = report["dimensions"]["scene"][0]

        self.assertEqual(scene["false_accept_count"], 1)
        self.assertEqual(scene["false_reject_count"], 1)
        self.assertEqual(scene["confirmed_count"], 2)
        self.assertEqual(scene["raw"]["error"], 1)
        self.assertEqual(scene["p95_elapsed_ms"], 100.0)
        self.assertEqual(scene["health"]["level"], "CRITICAL")
        self.assertEqual(report["overall"]["false_accept_count"], 1)
        self.assertEqual(report["monthly"][-1]["false_reject_count"], 1)
        database.close()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
