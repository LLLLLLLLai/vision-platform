import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.datasets import router
from app.db.base import Base
from app.db.session import get_db
from app.models.intelligence import AutomationJob, Dataset, DatasetItem


class DatasetTypeChangeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.dataset = Dataset(code="TYPE_TEST", name="切换测试", purpose="TRAIN", media_type="IMAGE", annotation_type="DETECTION", label_schema_json=["FU5", "FU6"])
        self.database.add(self.dataset)
        self.database.flush()
        self.item = DatasetItem(dataset_id=self.dataset.id, media_path="retained.png", original_name="retained.png", media_type="IMAGE", annotation_status="LABELED", annotation_json={"boxes": [{"label": "FU5"}]}, split="TRAIN")
        self.database.add(self.item)
        self.database.flush()
        self.augmented = DatasetItem(dataset_id=self.dataset.id, media_path="augmented.png", original_name="augmented.png", media_type="IMAGE", source="AUGMENTED", annotation_status="LABELED", annotation_json={"boxes": [{"label": "FU5"}], "_meta": {"augmentation": {"root_source_item_id": self.item.id}, "preannotation": {"model": 1}}}, split="TRAIN")
        self.database.add(self.augmented)
        self.database.commit()
        app = FastAPI()
        app.include_router(router, prefix="/datasets")
        app.dependency_overrides[get_db] = lambda: self.database
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.database.close()
        self.engine.dispose()

    def change(self, target="CLASSIFICATION", **overrides):
        payload = {"annotation_type": target, "confirm_clear_annotations": True, "expected_revision": self.dataset.revision}
        payload.update(overrides)
        return self.client.post(f"/datasets/{self.dataset.id}/annotation-type", json=payload)

    def test_switch_clears_annotations_preserves_images_labels_and_splits(self):
        response = self.change()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cleared_count"], 2)
        self.assertEqual(self.item.annotation_json, {})
        self.assertEqual(self.item.annotation_status, "PENDING")
        self.assertEqual(self.item.media_path, "retained.png")
        self.assertEqual(self.item.split, "TRAIN")
        self.assertFalse(self.item.is_deleted)
        self.assertEqual(self.dataset.label_schema_json, ["FU5", "FU6"])
        self.assertEqual(self.augmented.annotation_json, {"_meta": {"augmentation": {"root_source_item_id": self.item.id}}})
        self.assertEqual(self.change("SEGMENTATION").status_code, 200)
        self.assertEqual(self.change("DETECTION").status_code, 200)

    def test_confirmation_and_revision_required(self):
        self.assertEqual(self.change(confirm_clear_annotations=False).status_code, 400)
        self.assertEqual(self.change(confirm_clear_annotations="yes").status_code, 422)
        self.assertEqual(self.change(expected_revision=9999).status_code, 409)
        self.assertIn("boxes", self.item.annotation_json)

    def test_same_type_keeps_annotations(self):
        response = self.change("DETECTION")
        self.assertFalse(response.json()["changed"])
        self.assertIn("boxes", self.item.annotation_json)

    def test_active_tasks_block_changes(self):
        job = AutomationJob(job_type="YOLO_TRAINING", dataset_id=self.dataset.id)
        self.database.add(job)
        for status in ["QUEUED", "WAITING_GPU", "RUNNING", "CANCEL_REQUESTED"]:
            job.status = status
            self.database.commit()
            self.assertEqual(self.change().status_code, 409)
        job.status = "COMPLETED"
        job.result_json = {"historical": "unchanged"}
        self.database.commit()
        self.assertEqual(self.change().status_code, 200)
        self.assertEqual(job.result_json, {"historical": "unchanged"})

    def test_test_dataset_cannot_change(self):
        self.dataset.purpose = "TEST"
        self.database.commit()
        self.assertEqual(self.change().status_code, 400)

    def test_invalid_type_rejected(self):
        self.assertEqual(self.change("NONE").status_code, 422)
