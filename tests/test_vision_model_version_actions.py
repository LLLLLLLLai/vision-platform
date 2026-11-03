from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import vision_models as vision_models_module
from app.api.routes.scenarios import _validate_published_vision_model_node
from app.api.routes.vision_models import (
    VisionModelCreate,
    create_vision_model,
    list_published_vision_model_versions,
    publish_model_version,
    rollback_model_version,
)
from app.db.base import Base
from app.db.session import get_db
from app.models.intelligence import VisionModelVersion
from app.services.scenario_runtime import _select_published_vision_model


class VisionModelVersionActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.temporary_directory = TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.project_patch = patch.object(vision_models_module, "PROJECT_ROOT", self.project_root)
        self.project_patch.start()
        self.model = create_vision_model(
            VisionModelCreate(
                code="HARNESS_MODEL",
                name="线束检测",
                task_type="YOLO_DETECTION",
                labels_json=["harness"],
            ),
            database=self.database,
        )
        self.weights = self.project_root / "vision-models" / "harness.pt"
        self.weights.parent.mkdir(parents=True)
        self.weights.write_bytes(b"test-weights")
        self.app = FastAPI()
        self.app.include_router(vision_models_module.router, prefix="/api/v1/vision-models")
        self.app.dependency_overrides[get_db] = lambda: self.database
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.project_patch.stop()
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_version(self, name: str, weights_path: Path | None = None) -> VisionModelVersion:
        version = VisionModelVersion(
            vision_model_id=self.model["id"],
            version=name,
            status="DRAFT",
            weights_path=str(weights_path or self.weights),
        )
        self.database.add(version)
        self.database.commit()
        return version

    def test_export_streams_selected_version_weights(self) -> None:
        version = self.add_version("1.0")
        response = self.client.get(
            f"/api/v1/vision-models/{self.model['id']}/versions/{version.id}/export"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"test-weights")
        self.assertIn("HARNESS_MODEL_1.0.pt", response.headers["content-disposition"])

    def test_export_accepts_configured_training_artifacts(self) -> None:
        training_root = self.project_root / "training_runs"
        trained_weights = training_root / "HARNESS_MODEL" / "weights" / "best.pt"
        trained_weights.parent.mkdir(parents=True)
        trained_weights.write_bytes(b"trained-weights")
        version = self.add_version("trained", trained_weights)

        with patch.object(vision_models_module.settings, "training_artifacts_root", str(training_root)):
            response = self.client.get(
                f"/api/v1/vision-models/{self.model['id']}/versions/{version.id}/export"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"trained-weights")

    def test_export_rejects_outside_or_missing_weights_and_wrong_model(self) -> None:
        outside = self.project_root / "private.pt"
        outside.write_bytes(b"not-exportable")
        outside_version = self.add_version("outside", outside)
        missing_version = self.add_version("missing", self.project_root / "vision-models" / "missing.pt")
        base = f"/api/v1/vision-models/{self.model['id']}/versions"

        self.assertEqual(self.client.get(f"{base}/{outside_version.id}/export").status_code, 400)
        self.assertEqual(self.client.get(f"{base}/{missing_version.id}/export").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/vision-models/999/versions/{outside_version.id}/export").status_code,
            404,
        )

    def test_rollback_reactivates_archived_version_without_breaking_pinned_flows(self) -> None:
        previous = self.add_version("1.0")
        current = self.add_version("2.0")
        publish_model_version(self.model["id"], previous.id, database=self.database)
        publish_model_version(self.model["id"], current.id, database=self.database)
        self.database.expire_all()

        response = self.client.post(
            f"/api/v1/vision-models/{self.model['id']}/versions/{previous.id}/rollback"
        )
        self.assertEqual(response.status_code, 200)
        restored = response.json()
        self.database.expire_all()
        self.assertEqual(restored["status"], "PUBLISHED")
        self.assertEqual(self.database.get(VisionModelVersion, current.id).status, "ARCHIVED")
        published = list_published_vision_model_versions(database=self.database)
        self.assertEqual([item["version_id"] for item in published], [previous.id])

        pinned_node = SimpleNamespace(name="旧版模型节点", config_json={"model_version_id": current.id})
        self.assertEqual(_select_published_vision_model(self.database, pinned_node).version_id, current.id)
        _validate_published_vision_model_node(self.database, pinned_node)

    def test_rollback_rejects_draft_and_missing_weights(self) -> None:
        draft = self.add_version("draft")
        with self.assertRaises(HTTPException) as draft_error:
            rollback_model_version(self.model["id"], draft.id, database=self.database)
        self.assertEqual(draft_error.exception.status_code, 409)

        publish_model_version(self.model["id"], draft.id, database=self.database)
        current = self.add_version("current")
        publish_model_version(self.model["id"], current.id, database=self.database)
        self.weights.unlink()
        with self.assertRaises(HTTPException) as missing_error:
            rollback_model_version(self.model["id"], draft.id, database=self.database)
        self.assertEqual(missing_error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
