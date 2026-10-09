from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import vision_models as vision_models_module
from app.api.routes.vision_models import (
    VisionModelCreate,
    create_vision_model,
    import_model_version,
)
from app.db.base import Base


class VisionModelImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.database = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.temporary_directory = TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.original_project_root = vision_models_module.PROJECT_ROOT
        vision_models_module.PROJECT_ROOT = self.project_root
        self.model = create_vision_model(
            VisionModelCreate(
                code="HARNESS_IMPORT",
                name="线束导入模型",
                task_type="YOLO_DETECTION",
                labels_json=["harness"],
            ),
            database=self.database,
        )

    async def asyncTearDown(self) -> None:
        vision_models_module.PROJECT_ROOT = self.original_project_root
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    @staticmethod
    def _validation_summary(path: Path, task_type: str) -> dict[str, object]:
        return {
            "detected_task": "detect",
            "task_type": task_type,
            "class_names": ["harness"],
        }

    async def test_imports_existing_or_uploaded_checkpoint_as_draft(self) -> None:
        existing = self.project_root / "vision-models" / "manual" / "best.pt"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"mock-yolo-weights")

        with patch.object(
            vision_models_module,
            "validate_yolo_weights_for_task",
            side_effect=self._validation_summary,
        ):
            from_server = await import_model_version(
                self.model["id"],
                version="manual-1.0",
                existing_weights_path="vision-models/manual/best.pt",
                weights_file=None,
                database=self.database,
            )
            uploaded = await import_model_version(
                self.model["id"],
                version="upload-1.0",
                existing_weights_path=None,
                weights_file=UploadFile(filename="best.pt", file=BytesIO(b"uploaded-weights")),
                database=self.database,
            )

        self.assertEqual(from_server["status"], "DRAFT")
        self.assertEqual(from_server["weights_path"], "vision-models/manual/best.pt")
        self.assertEqual(from_server["artifact_paths_json"]["import_source"], "server_path")
        self.assertEqual(uploaded["status"], "DRAFT")
        self.assertEqual(uploaded["artifact_paths_json"]["import_source"], "upload")
        imported_file = self.project_root / uploaded["weights_path"]
        self.assertTrue(imported_file.is_file())
        self.assertEqual(imported_file.read_bytes(), b"uploaded-weights")

    async def test_rejects_server_path_outside_weights_directory(self) -> None:
        outside = self.project_root / "outside.pt"
        outside.write_bytes(b"not-allowed")
        with self.assertRaises(HTTPException) as error:
            await import_model_version(
                self.model["id"],
                version="bad-path",
                existing_weights_path=str(outside),
                weights_file=None,
                database=self.database,
            )
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("vision-models/", str(error.exception.detail))


if __name__ == "__main__":
    unittest.main()
