import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.inspection import detection_call_record_detail
from app.db.base import Base
from app.models.inspection import DetectionApiCall, DetectionTask
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioExecution,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station


class DetectionRecordTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        product = Product(code="MAT01", name="物料 MAT01")
        station = Station(code="L01_OP10", name="工位")
        self.database.add_all((product, station))
        self.database.flush()
        self.recipe = Recipe(
            code="L01_MAT01_OP10_CAM01_P01",
            name="生产配方",
            product_id=product.id,
            station_id=station.id,
            status="PUBLISHED",
        )
        self.database.add(self.recipe)
        self.database.flush()
        self.roi = RegionOfInterest(
            recipe_id=self.recipe.id,
            code="ROI_01",
            name="线束端子",
            x_ratio=0.1,
            y_ratio=0.1,
            width_ratio=0.2,
            height_ratio=0.2,
        )
        self.database.add(self.roi)
        self.database.flush()
        self.task = DetectionTask(
            request_id="request-001",
            sn="SN-001",
            recipe_id=self.recipe.id,
            recipe_version="1.0",
            status="OK",
        )
        self.database.add(self.task)
        self.database.flush()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def _record_for(self, execution: ScenarioExecution) -> DetectionApiCall:
        record = DetectionApiCall(
            caller_ip="127.0.0.1",
            sn="SN-001",
            request_payload={"operation": "OP10"},
            response_payload={
                "inspection_results": [
                    {
                        "request_id": self.task.request_id,
                        "recipe_code": self.recipe.code,
                        "recipe_version": "1.0",
                        "result": "OK",
                        "image_results": [
                            {
                                "image_path": "input.jpg",
                                "result": "OK",
                                "inspection_items": [
                                    {
                                        "roi_code": self.roi.code,
                                        "roi_name": self.roi.name,
                                        "item_name": execution.scenario_version.scenario.name,
                                        "capability": execution.scenario_version.scenario.mode,
                                        "scenario_execution_id": execution.id,
                                        "scenario_version": execution.scenario_version.version,
                                        "status": "OK",
                                        "score": 0.96,
                                        "elapsed_ms": execution.elapsed_ms,
                                        "actual": execution.output_json,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            response_code=0,
            call_status="SUCCESS",
        )
        self.database.add(record)
        self.database.commit()
        return record

    def _scenario_execution(
        self,
        *,
        mode: str,
        output: dict,
        elapsed_ms: float,
    ) -> ScenarioExecution:
        scene = InspectionScenario(
            code=f"SCENE_{mode}",
            name=f"{mode} 场景",
            mode=mode,
        )
        self.database.add(scene)
        self.database.flush()
        version = InspectionScenarioVersion(
            scenario_id=scene.id,
            version="1.0",
            status="PUBLISHED",
        )
        self.database.add(version)
        self.database.flush()
        execution = ScenarioExecution(
            scenario_version_id=version.id,
            detection_task_id=self.task.id,
            roi_id=self.roi.id,
            source="PRODUCTION",
            status="COMPLETED",
            result="OK",
            output_json=output,
            elapsed_ms=elapsed_ms,
        )
        self.database.add(execution)
        self.database.commit()
        self.database.refresh(execution)
        return execution

    def test_direct_vlm_record_exposes_single_vlm_elapsed_time(self) -> None:
        execution = self._scenario_execution(
            mode="VLM_DIRECT",
            elapsed_ms=1234.5,
            output={
                "traces": [
                    {
                        "node_key": "direct_vlm",
                        "node_name": "VLM 检测",
                        "node_type": "VLM",
                        "status": "OK",
                        "elapsed_ms": 1220.25,
                    }
                ]
            },
        )
        record = self._record_for(execution)

        detail = detection_call_record_detail(record.id, database=self.database)
        timing = detail["recipes"][0]["images"][0]["rois"][0]["scenario_timing"]

        self.assertEqual(timing["display_type"], "VLM")
        self.assertEqual(timing["vlm_elapsed_ms"], 1220.25)
        self.assertEqual(timing["total_elapsed_ms"], 1234.5)
        self.assertNotIn("nodes", timing)

    def test_workflow_record_exposes_each_node_elapsed_time(self) -> None:
        execution = self._scenario_execution(
            mode="WORKFLOW",
            elapsed_ms=385.4,
            output={
                "traces": [
                    {
                        "node_key": "detector",
                        "node_name": "线束检测",
                        "node_type": "VISION_MODEL",
                        "status": "OK",
                        "elapsed_ms": 202.1,
                    },
                    {
                        "node_key": "judge",
                        "node_name": "规则判断",
                        "node_type": "RULE",
                        "status": "OK",
                        "elapsed_ms": 1.3,
                    },
                ]
            },
        )
        record = self._record_for(execution)

        detail = detection_call_record_detail(record.id, database=self.database)
        timing = detail["recipes"][0]["images"][0]["rois"][0]["scenario_timing"]

        self.assertEqual(timing["display_type"], "WORKFLOW")
        self.assertEqual(timing["total_elapsed_ms"], 385.4)
        self.assertEqual(
            [(node["node_key"], node["elapsed_ms"]) for node in timing["nodes"]],
            [("detector", 202.1), ("judge", 1.3)],
        )


if __name__ == "__main__":
    unittest.main()
