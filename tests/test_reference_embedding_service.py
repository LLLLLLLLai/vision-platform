import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.inspection import InspectionItem
from app.models.reference import ReferenceGroup, ReferenceImage
from app.services.inspection_engine import InspectionEngine
from app.services.reference_embedding_service import (
    ReferenceVector,
    decide_reference_addition,
    load_embedding,
    rank_reference_vectors,
    save_embedding,
)


class ReferenceEmbeddingPolicyTest(unittest.TestCase):
    def _reference(self, reference_id: int, vector: list[float]) -> ReferenceVector:
        return ReferenceVector(
            reference_id=reference_id,
            image_path=f"reference-{reference_id}.jpg",
            embedding_path=f"reference-{reference_id}.npy",
            vector=np.asarray(vector, dtype=np.float32),
        )

    def test_embedding_is_saved_as_float16_and_loaded_normalized(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "embedding.npy"
            dimension = save_embedding(path, [3.0, 4.0, 0.0])
            stored = np.load(path, allow_pickle=False)
            loaded = load_embedding(path)
        self.assertEqual(dimension, 3)
        self.assertEqual(stored.dtype, np.float16)
        self.assertAlmostEqual(float(np.linalg.norm(loaded)), 1.0, places=6)

    def test_rank_uses_precomputed_reference_vectors(self) -> None:
        result = rank_reference_vectors(
            [1.0, 0.0, 0.0],
            [
                self._reference(1, [1.0, 0.0, 0.0]),
                self._reference(2, [0.0, 1.0, 0.0]),
            ],
        )
        self.assertEqual(result["candidates"][0]["reference_id"], 1)
        self.assertAlmostEqual(result["top1_similarity"], 1.0)
        self.assertEqual(result["reference_count"], 2)

    def test_duplicate_candidate_is_not_added(self) -> None:
        decision = decide_reference_addition(
            [1.0, 0.0, 0.0],
            [self._reference(1, [1.0, 0.0, 0.0])],
            limit=10,
            duplicate_threshold=0.995,
            diversity_margin=0.005,
        )
        self.assertEqual(decision.action, "SKIP_DUPLICATE")

    def test_diverse_candidate_replaces_redundant_reference_at_capacity(self) -> None:
        decision = decide_reference_addition(
            [0.0, 1.0, 0.0],
            [
                self._reference(1, [1.0, 0.0, 0.0]),
                self._reference(2, [0.999, 0.001, 0.0]),
            ],
            limit=2,
            duplicate_threshold=0.995,
            diversity_margin=0.005,
        )
        self.assertEqual(decision.action, "REPLACE_REDUNDANT")
        self.assertEqual(decision.replace_reference_id, 2)


class CachedReferenceExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_inspection_reuses_saved_reference_embedding(self) -> None:
        with TemporaryDirectory() as directory:
            embedding_path = Path(directory) / "reference.npy"
            save_embedding(embedding_path, [1.0, 0.0, 0.0])
            database_engine = create_engine("sqlite://")
            Base.metadata.create_all(database_engine)
            database = Session(database_engine)
            group = ReferenceGroup(
                code="CACHE_TEST",
                name="Cache test",
                object_type="FUSE",
                class_code="FUSE_PRESENT",
            )
            database.add(group)
            database.flush()
            database.add(
                ReferenceImage(
                    group_id=group.id,
                    image_path="reference.jpg",
                    embedding_path=str(embedding_path),
                    embedding_dimension=3,
                    quality_status="READY",
                )
            )
            database.commit()
            item = InspectionItem(
                roi_id=1,
                code="CACHE_CHECK",
                name="Cache check",
                inspection_type="EXISTENCE",
                capability="REFERENCE_SIMILARITY",
                reference_group_id=group.id,
                expected_json={"class_code": "FUSE_PRESENT"},
                rule_json={"min_similarity": 0.9},
            )

            class FakeAlgorithms:
                async def embedding(self, _image_path: str):
                    return {
                        "model": "dinov2-base",
                        "dimension": 3,
                        "embedding": [1.0, 0.0, 0.0],
                    }

                async def similarity(self, *_args, **_kwargs):
                    raise AssertionError("Reference images should not be re-encoded.")

            engine = InspectionEngine()
            engine.algorithms = FakeAlgorithms()
            result = await engine._reference_similarity(database, item, "roi.jpg")
            database.close()
            database_engine.dispose()

        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["actual"]["embedding_cache_used"])


if __name__ == "__main__":
    unittest.main()
