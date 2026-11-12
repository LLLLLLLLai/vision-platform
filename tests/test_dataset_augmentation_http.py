from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import datasets as datasets_module
from app.db.base import Base
from app.db.session import get_db
from app.models.intelligence import Dataset, DatasetItem


class DatasetAugmentationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image_path = self.root / "source.png"
        Image.new("RGB", (24, 24), (128, 100, 70)).save(self.image_path)
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.dataset = Dataset(code="AUGMENT_HTTP", name="增强接口测试", purpose="TRAIN", media_type="IMAGE", annotation_type="DETECTION", label_schema_json=["wire"])
        self.database.add(self.dataset)
        self.database.commit()
        self.url = f"/api/v1/datasets/{self.dataset.id}/augmentations"
        application = FastAPI()
        application.include_router(datasets_module.router, prefix="/api/v1/datasets")
        application.dependency_overrides[get_db] = lambda: self.database
        self.client = TestClient(application)
        self.root_patch = patch.object(datasets_module, "PROJECT_ROOT", self.root)
        self.root_patch.start()

    def tearDown(self) -> None:
        self.client.close()
        self.root_patch.stop()
        self.database.close()
        self.engine.dispose()
        self.directory.cleanup()

    def add_sources(self, count: int, split: str = "TRAIN") -> list[int]:
        items = [DatasetItem(
            dataset_id=self.dataset.id,
            media_path=str(self.image_path),
            original_name=f"source_{index}.png",
            media_type="IMAGE",
            annotation_status="LABELED",
            annotation_json={"boxes": [{"label": "wire", "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}]},
            source="UPLOAD",
            split=split,
        ) for index in range(count)]
        self.database.add_all(items)
        self.database.commit()
        return [item.id for item in items]

    def test_2634_sources_complete_in_14_http_batches(self) -> None:
        source_ids = self.add_sources(2634)
        created = 0
        for offset in range(0, len(source_ids), 200):
            batch = source_ids[offset:offset + 200]
            response = self.client.post(self.url, json={
                "source_item_ids": batch, "copies_per_item": 1, "seed": 20261112,
                "horizontal_flip": True, "gaussian_noise_std_min": 10, "gaussian_noise_std_max": 10,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["created_count"], len(batch))
            created += response.json()["created_count"]
        self.assertEqual(created, 2634)
        self.database.expire_all()
        generated = self.database.scalars(select(DatasetItem).where(DatasetItem.source == "AUGMENTED")).all()
        self.assertEqual(len(generated), 2634)
        self.assertEqual({item.annotation_json["_meta"]["augmentation"]["source_item_id"] for item in generated}, set(source_ids))
        for item in generated:
            self.assertEqual(item.split, "TRAIN")
            self.assertTrue(Path(item.media_path).is_file())
            self.assertAlmostEqual(item.annotation_json["boxes"][0]["x"], 0.6)

    def test_invalid_range_returns_structured_422_without_generating_images(self) -> None:
        source_ids = self.add_sources(1)
        response = self.client.post(self.url, json={"source_item_ids": source_ids, "rotation_max_degrees": 90})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "rotation_max_degrees"])
        self.assertEqual(response.json()["detail"][0]["type"], "less_than_equal")
        self.assertFalse((self.root / "uploads").exists())

    def test_validation_and_test_images_remain_unaugmented(self) -> None:
        source_ids = self.add_sources(1, "VAL") + self.add_sources(1, "TEST")
        response = self.client.post(self.url, json={"source_item_ids": source_ids, "horizontal_flip": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created_count"], 0)
        self.assertEqual(response.json()["skipped_non_training_count"], 2)

    def test_missing_image_reports_original_filename(self) -> None:
        source_ids = self.add_sources(1)
        self.image_path.unlink()
        response = self.client.post(self.url, json={"source_item_ids": source_ids, "horizontal_flip": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("source_0.png", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
