from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from fastapi import UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import datasets as datasets_module
from app.api.routes.datasets import (
    DatasetCreate,
    create_dataset,
    get_dataset,
    import_offline_yolo_dataset,
)
from app.db.base import Base


class OfflineYoloDatasetImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.database = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.temporary_directory = TemporaryDirectory()
        self.temporary_root = Path(self.temporary_directory.name)
        self.original_project_root = datasets_module.PROJECT_ROOT
        datasets_module.PROJECT_ROOT = self.temporary_root

    def tearDown(self) -> None:
        datasets_module.PROJECT_ROOT = self.original_project_root
        self.database.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    @staticmethod
    def _png_bytes(color: tuple[int, int, int]) -> bytes:
        output = BytesIO()
        Image.new("RGB", (40, 30), color).save(output, format="PNG")
        return output.getvalue()

    def _archive(self) -> UploadFile:
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("classes.txt", "harness\nconnector\n")
            archive.writestr("images/train/harness.png", self._png_bytes((20, 100, 20)))
            archive.writestr("labels/train/harness.txt", "0 0.5 0.5 0.4 0.2\n")
            archive.writestr("images/train/empty.png", self._png_bytes((50, 50, 50)))
        payload.seek(0)
        return UploadFile(filename="offline-harness.zip", file=payload)

    def test_imports_standard_yolo_images_labels_and_negative_samples(self) -> None:
        dataset = create_dataset(
            DatasetCreate(
                code="OFFLINE_YOLO_HARNESS",
                name="离线线束训练集",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="DETECTION",
            ),
            database=self.database,
        )

        result = import_offline_yolo_dataset(
            dataset["id"],
            archive=self._archive(),
            database=self.database,
        )

        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["negative_count"], 1)
        self.assertEqual(result["labels"], ["harness", "connector"])

        imported = get_dataset(dataset["id"], database=self.database)
        items = {item["original_name"]: item for item in imported["items"]}
        self.assertEqual(items["harness.png"]["source"], "OFFLINE_YOLO")
        self.assertEqual(items["harness.png"]["annotation_status"], "LABELED")
        box = items["harness.png"]["annotation_json"]["boxes"][0]
        self.assertEqual(box["label"], "harness")
        self.assertAlmostEqual(box["x"], 0.3)
        self.assertAlmostEqual(box["y"], 0.4)
        self.assertAlmostEqual(box["width"], 0.4)
        self.assertAlmostEqual(box["height"], 0.2)
        self.assertEqual(items["empty.png"]["annotation_json"], {"boxes": []})

    def test_duplicate_archive_content_is_skipped(self) -> None:
        dataset = create_dataset(
            DatasetCreate(
                code="OFFLINE_YOLO_DUPLICATE",
                name="离线重复检查",
                purpose="TRAIN",
                media_type="IMAGE",
                annotation_type="DETECTION",
            ),
            database=self.database,
        )
        first = import_offline_yolo_dataset(dataset["id"], archive=self._archive(), database=self.database)
        second = import_offline_yolo_dataset(dataset["id"], archive=self._archive(), database=self.database)

        self.assertEqual(first["created_count"], 2)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["skipped_count"], 2)


if __name__ == "__main__":
    unittest.main()
