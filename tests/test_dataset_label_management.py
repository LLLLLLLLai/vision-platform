import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.datasets import DatasetUpdate, update_dataset
from app.db.base import Base
from app.models.intelligence import Dataset, DatasetItem


class DatasetLabelManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.dataset = Dataset(
            code="LABEL_MANAGEMENT",
            name="类别管理测试",
            purpose="TRAIN",
            media_type="IMAGE",
            annotation_type="DETECTION",
            label_schema_json=["harness", "connector", "unused"],
        )
        self.database.add(self.dataset)
        self.database.flush()
        self.item = DatasetItem(
            dataset_id=self.dataset.id,
            media_path="test.jpg",
            original_name="test.jpg",
            media_type="IMAGE",
            annotation_status="LABELED",
            annotation_json={"boxes": [{"label": "harness"}, {"label": "harness"}]},
        )
        self.database.add(self.item)
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_unused_initial_label_can_be_removed(self) -> None:
        update_dataset(
            self.dataset.id,
            DatasetUpdate(labels=["harness", "connector"]),
            database=self.database,
        )
        self.database.refresh(self.dataset)
        self.assertEqual(self.dataset.label_schema_json, ["harness", "connector"])

    def test_label_used_by_annotations_cannot_be_removed(self) -> None:
        with self.assertRaises(HTTPException) as error:
            update_dataset(
                self.dataset.id,
                DatasetUpdate(labels=["connector", "unused"]),
                database=self.database,
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("harness（1 张）", error.exception.detail)
        self.database.refresh(self.dataset)
        self.assertIn("harness", self.dataset.label_schema_json)

    def test_label_can_be_removed_after_relabeling(self) -> None:
        self.item.annotation_json = {"boxes": [{"label": "connector"}]}
        self.database.commit()
        update_dataset(
            self.dataset.id,
            DatasetUpdate(labels=["connector", "unused"]),
            database=self.database,
        )
        self.database.refresh(self.dataset)
        self.assertEqual(self.dataset.label_schema_json, ["connector", "unused"])


if __name__ == "__main__":
    unittest.main()
