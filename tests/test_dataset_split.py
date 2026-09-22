import unittest
from types import SimpleNamespace

from app.services.dataset_split import assign_training_splits


class DatasetSplitTest(unittest.TestCase):
    def _items(self, total: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=index + 1,
                content_hash=f"sample-{index + 1}",
                split=None,
                is_deleted=False,
            )
            for index in range(total)
        ]

    def test_ten_samples_are_split_as_seven_two_one(self) -> None:
        items = self._items(10)

        result = assign_training_splits(items, seed="HARNESS:revision:1")

        self.assertEqual(result["counts"], {"TRAIN": 7, "VAL": 2, "TEST": 1})
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["changed"], 10)
        self.assertEqual([item.split for item in items].count("TRAIN"), 7)
        self.assertEqual([item.split for item in items].count("VAL"), 2)
        self.assertEqual([item.split for item in items].count("TEST"), 1)

    def test_same_dataset_revision_keeps_the_split_stable(self) -> None:
        items = self._items(10)
        first = assign_training_splits(items, seed="HARNESS:revision:2")
        first_manifest = first["manifest"]

        second = assign_training_splits(items, seed="HARNESS:revision:2")

        self.assertEqual(second["changed"], 0)
        self.assertEqual(second["manifest"], first_manifest)

    def test_deleted_samples_are_not_assigned(self) -> None:
        items = self._items(4)
        items[-1].is_deleted = True

        result = assign_training_splits(items, seed="HARNESS:revision:3")

        self.assertEqual(result["total"], 3)
        self.assertIsNone(items[-1].split)
        self.assertEqual(result["counts"], {"TRAIN": 1, "VAL": 1, "TEST": 1})


if __name__ == "__main__":
    unittest.main()
