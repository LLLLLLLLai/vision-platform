from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.scenarios import _validate_image_crop_node
from app.db.base import Base
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioNode,
    VisionModel,
    VisionModelVersion,
)


class SceneModelOutputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_crop_node_cannot_consume_bbox_from_classification_model(self) -> None:
        model = VisionModel(
            code="AI_HARNESS_LOCK",
            name="AI 检测线束锁付",
            task_type="YOLO_CLASSIFICATION",
        )
        self.database.add(model)
        self.database.flush()
        model_version = VisionModelVersion(
            vision_model_id=model.id,
            version="V1",
            status="PUBLISHED",
            weights_path="weights/harness-classification.pt",
        )
        scene = InspectionScenario(
            code="SCENE_HARNESS_FLOW",
            name="线束流程",
            mode="WORKFLOW",
        )
        self.database.add_all((model_version, scene))
        self.database.flush()
        scenario_version = InspectionScenarioVersion(
            scenario_id=scene.id,
            version="1.0",
            status="DRAFT",
        )
        self.database.add(scenario_version)
        self.database.flush()
        classifier = ScenarioNode(
            scenario_version_id=scenario_version.id,
            node_key="classifier",
            name="锁付分类",
            node_type="VISION_MODEL",
            config_json={"model_version_id": model_version.id},
        )
        crop = ScenarioNode(
            scenario_version_id=scenario_version.id,
            node_key="crop",
            name="裁剪锁付区域",
            node_type="IMAGE_CROP",
            config_json={
                "input_mapping": {
                    "bbox": "{{ nodes.classifier.objects.0.bbox }}",
                }
            },
        )
        self.database.add_all((classifier, crop))
        self.database.commit()
        self.database.refresh(scenario_version)

        with self.assertRaises(HTTPException) as context:
            _validate_image_crop_node(self.database, scenario_version, crop)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("不输出定位框", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()
