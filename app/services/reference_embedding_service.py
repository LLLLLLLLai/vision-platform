from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class ReferenceVector:
    reference_id: int
    image_path: str
    embedding_path: str
    vector: np.ndarray


@dataclass(frozen=True)
class ReferenceAdditionDecision:
    action: str
    nearest_similarity: float | None = None
    replace_reference_id: int | None = None


def normalize_embedding(values: Iterable[float] | np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        raise ValueError("Embedding vector cannot be empty or zero.")
    return vector / norm


def save_embedding(path: Path, values: Iterable[float] | np.ndarray) -> int:
    vector = normalize_embedding(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, vector.astype(np.float16))
    return int(vector.size)


def load_embedding(path: str | Path) -> np.ndarray:
    embedding_path = Path(path)
    if not embedding_path.is_file():
        raise FileNotFoundError(f"Embedding file does not exist: {embedding_path}")
    return normalize_embedding(np.load(embedding_path, allow_pickle=False))


def load_reference_vectors(references: Sequence[object]) -> list[ReferenceVector]:
    vectors: list[ReferenceVector] = []
    for reference in references:
        embedding_path = getattr(reference, "embedding_path", None)
        if not embedding_path:
            continue
        try:
            vector = load_embedding(embedding_path)
        except (FileNotFoundError, OSError, ValueError):
            continue
        vectors.append(
            ReferenceVector(
                reference_id=int(getattr(reference, "id")),
                image_path=str(getattr(reference, "image_path")),
                embedding_path=str(embedding_path),
                vector=vector,
            )
        )
    return vectors


def rank_reference_vectors(
    query_values: Iterable[float] | np.ndarray,
    references: Sequence[ReferenceVector],
    top_k: int = 3,
) -> dict[str, object]:
    if not references:
        raise ValueError("No usable reference embeddings are available.")
    query = normalize_embedding(query_values)
    candidates = [
        {
            "reference_id": reference.reference_id,
            "reference_path": reference.image_path,
            "similarity": round(float(np.dot(query, reference.vector)), 6),
        }
        for reference in references
    ]
    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    selected = candidates[: max(1, min(top_k, len(candidates)))]
    top1 = float(selected[0]["similarity"])
    top2 = float(selected[1]["similarity"]) if len(selected) > 1 else None
    return {
        "top1_similarity": top1,
        "top2_similarity": top2,
        "margin": round(top1 - top2, 6) if top2 is not None else None,
        "top_k_mean": round(
            float(np.mean([float(item["similarity"]) for item in selected])),
            6,
        ),
        "candidates": selected,
        "reference_count": len(references),
    }


def decide_reference_addition(
    candidate_values: Iterable[float] | np.ndarray,
    references: Sequence[ReferenceVector],
    *,
    limit: int,
    duplicate_threshold: float,
    diversity_margin: float,
) -> ReferenceAdditionDecision:
    if not references:
        return ReferenceAdditionDecision(action="ADD")

    candidate = normalize_embedding(candidate_values)
    candidate_scores = [float(np.dot(candidate, item.vector)) for item in references]
    nearest_similarity = max(candidate_scores)
    if nearest_similarity >= duplicate_threshold:
        return ReferenceAdditionDecision(
            action="SKIP_DUPLICATE",
            nearest_similarity=nearest_similarity,
        )
    if len(references) < max(1, limit):
        return ReferenceAdditionDecision(
            action="ADD",
            nearest_similarity=nearest_similarity,
        )
    if len(references) < 2:
        return ReferenceAdditionDecision(
            action="SKIP_CAPACITY",
            nearest_similarity=nearest_similarity,
        )

    most_redundant_similarity = -1.0
    redundant_pair: tuple[int, int] | None = None
    for left_index, left in enumerate(references):
        for right_index in range(left_index + 1, len(references)):
            similarity = float(np.dot(left.vector, references[right_index].vector))
            if similarity > most_redundant_similarity:
                most_redundant_similarity = similarity
                redundant_pair = (left_index, right_index)

    if (
        redundant_pair is not None
        and nearest_similarity + diversity_margin < most_redundant_similarity
    ):
        left = references[redundant_pair[0]]
        right = references[redundant_pair[1]]
        replace = right if right.reference_id > left.reference_id else left
        return ReferenceAdditionDecision(
            action="REPLACE_REDUNDANT",
            nearest_similarity=nearest_similarity,
            replace_reference_id=replace.reference_id,
        )
    return ReferenceAdditionDecision(
        action="SKIP_NOT_DIVERSE",
        nearest_similarity=nearest_similarity,
    )
