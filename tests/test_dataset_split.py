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

    def test_four_samples_keep_two_for_training(self) -> None:
        items = self._items(4)

        result = assign_training_splits(items, seed="SMALL_SET")

        self.assertEqual(result["counts"], {"TRAIN": 2, "VAL": 1, "TEST": 1})

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

    def test_augmented_siblings_stay_with_their_source_partition(self) -> None:
        source = SimpleNamespace(
            id=1,
            content_hash="source-1",
            split=None,
            is_deleted=False,
            annotation_json={},
        )
        augmented = SimpleNamespace(
            id=2,
            content_hash="augmented-1",
            split=None,
            is_deleted=False,
            annotation_json={
                "_meta": {"augmentation": {"source_item_id": 1, "root_source_item_id": 1}}
            },
        )
        unrelated = [
            SimpleNamespace(
                id=index,
                content_hash=f"source-{index}",
                split=None,
                is_deleted=False,
                annotation_json={},
            )
            for index in range(3, 9)
        ]

        result = assign_training_splits([source, augmented, *unrelated], seed="HARNESS:augmentation")

        self.assertEqual(source.split, augmented.split)
        self.assertEqual(result["group_total"], 7)
        self.assertEqual(result["total"], 8)
        manifest = {item["dataset_item_id"]: item for item in result["manifest"]}
        self.assertEqual(manifest[1]["augmentation_group"], manifest[2]["augmentation_group"])

    def test_source_partition_wins_when_old_augmented_row_disagrees(self) -> None:
        source = SimpleNamespace(id=5, split="TRAIN", is_deleted=False, annotation_json={})
        augmented = SimpleNamespace(
            id=8,
            split="VAL",
            is_deleted=False,
            annotation_json={"_meta": {"augmentation": {"root_source_item_id": 5}}},
        )

        result = assign_training_splits([augmented, source], seed="SOURCE_PRIORITY")

        self.assertEqual(source.split, "TRAIN")
        self.assertEqual(augmented.split, "TRAIN")
        self.assertEqual(result["group_total"], 1)


if __name__ == "__main__":
    unittest.main()
