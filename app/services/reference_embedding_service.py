import json
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from app.core.config import PROJECT_ROOT, settings


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


_ARRAY_CACHE: OrderedDict[str, tuple[int, int, np.ndarray]] = OrderedDict()
_ARRAY_CACHE_LOCK = threading.Lock()


def embedding_storage_root() -> Path:
    root = Path(settings.embedding_storage_root).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def resolve_embedding_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = embedding_storage_root() / path
    return path.resolve()


def embedding_storage_key(path: str | Path) -> str:
    resolved = resolve_embedding_path(path)
    try:
        return resolved.relative_to(embedding_storage_root()).as_posix()
    except ValueError:
        return str(resolved)


def _safe_segment(value: object, fallback: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_-]+", "_", str(value or "").upper()).strip("_")
    return (normalized or fallback)[:64]


def reference_set_directory(
    group: object,
    set_version: int,
    *,
    recipe: object | None = None,
    roi: object | None = None,
) -> Path:
    root = embedding_storage_root()
    if recipe is None or roi is None:
        group_id = int(getattr(group, "id"))
        return (
            root
            / f"SHARD_{group_id % 256:02X}"
            / f"GROUP_{group_id:08d}"
            / f"SET_V{set_version:04d}"
        )
    return (
        root
        / f"LINE_{_safe_segment(getattr(recipe, 'line_code', None), 'UNKNOWN')}"
        / f"MATERIAL_{_safe_segment(getattr(recipe, 'material_code', None), 'UNKNOWN')}"
        / f"PROCESS_{_safe_segment(getattr(recipe, 'process_code', None), 'UNKNOWN')}"
        / f"CAMERA_{_safe_segment(getattr(recipe, 'camera_code', None), 'UNKNOWN')}"
        / f"SHOT_{int(getattr(recipe, 'capture_index', 1)):02d}"
        / f"RECIPE_{int(getattr(recipe, 'id')):08d}"
        / f"ROI_{int(getattr(roi, 'id')):08d}"
        / f"SET_V{set_version:04d}"
    )


def normalize_embedding(values: Iterable[float] | np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        raise ValueError("Embedding vector cannot be empty or zero.")
    return vector / norm


def _load_array(path: str | Path) -> np.ndarray:
    resolved = resolve_embedding_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"Embedding file does not exist: {resolved}")
    stat = resolved.stat()
    cache_key = str(resolved)
    with _ARRAY_CACHE_LOCK:
        cached = _ARRAY_CACHE.get(cache_key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            _ARRAY_CACHE.move_to_end(cache_key)
            return cached[2]
    array = np.asarray(np.load(resolved, allow_pickle=False), dtype=np.float32)
    with _ARRAY_CACHE_LOCK:
        _ARRAY_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, array)
        _ARRAY_CACHE.move_to_end(cache_key)
        cache_limit = max(1, settings.reference_embedding_memory_cache_size)
        while len(_ARRAY_CACHE) > cache_limit:
            _ARRAY_CACHE.popitem(last=False)
    return array


def invalidate_embedding_cache(path: str | Path | None = None) -> None:
    with _ARRAY_CACHE_LOCK:
        if path is None:
            _ARRAY_CACHE.clear()
            return
        _ARRAY_CACHE.pop(str(resolve_embedding_path(path)), None)


def save_embedding(path: Path, values: Iterable[float] | np.ndarray) -> int:
    vector = normalize_embedding(values)
    resolved = resolve_embedding_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    np.save(resolved, vector.astype(np.float16))
    invalidate_embedding_cache(resolved)
    return int(vector.size)


def load_embedding(path: str | Path, index: int | None = None) -> np.ndarray:
    array = _load_array(path)
    if array.ndim == 1:
        vector = array
    elif array.ndim == 2:
        row_index = 0 if index is None and array.shape[0] == 1 else index
        if row_index is None or row_index < 0 or row_index >= array.shape[0]:
            raise ValueError(f"Embedding row index is invalid for {path}: {index}")
        vector = array[row_index]
    else:
        raise ValueError(f"Unsupported embedding shape for {path}: {array.shape}")
    return normalize_embedding(vector)


def load_reference_vectors(references: Sequence[object]) -> list[ReferenceVector]:
    vectors: list[ReferenceVector] = []
    for reference in references:
        embedding_path = getattr(reference, "embedding_path", None)
        if not embedding_path:
            continue
        try:
            vector = load_embedding(
                embedding_path,
                getattr(reference, "embedding_index", None),
            )
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


def write_reference_matrix(
    group: object,
    references: Sequence[object],
    supplied_vectors: Mapping[int, Iterable[float] | np.ndarray] | None = None,
    *,
    recipe: object | None = None,
    roi: object | None = None,
) -> dict[str, object]:
    active_references = sorted(
        (
            reference
            for reference in references
            if getattr(reference, "enabled", True) is not False
            and not bool(getattr(reference, "is_deleted", False))
            and (
                str(getattr(reference, "quality_status", "READY")).upper()
                == "READY"
                or int(getattr(reference, "id")) in (supplied_vectors or {})
            )
        ),
        key=lambda reference: int(getattr(reference, "id")),
    )
    if not active_references:
        setattr(group, "embedding_matrix_path", None)
        setattr(group, "embedding_manifest_path", None)
        setattr(group, "embedding_count", 0)
        return {
            "count": 0,
            "version": int(getattr(group, "embedding_set_version", 0) or 0),
        }

    supplied = supplied_vectors or {}
    vectors: list[np.ndarray] = []
    for reference in active_references:
        reference_id = int(getattr(reference, "id"))
        if reference_id in supplied:
            vector = normalize_embedding(supplied[reference_id])
        else:
            embedding_path = getattr(reference, "embedding_path", None)
            if not embedding_path:
                raise ValueError(f"Reference {reference_id} has no embedding path.")
            vector = load_embedding(
                embedding_path,
                getattr(reference, "embedding_index", None),
            )
        vectors.append(vector)

    dimensions = {int(vector.size) for vector in vectors}
    if len(dimensions) != 1:
        raise ValueError(f"Reference embedding dimensions do not match: {dimensions}")

    version = int(getattr(group, "embedding_set_version", 0) or 0) + 1
    directory = reference_set_directory(group, version, recipe=recipe, roi=roi)
    directory.mkdir(parents=True, exist_ok=True)
    matrix_path = directory / "embeddings.npy"
    manifest_path = directory / "manifest.json"
    matrix_temp = directory / "embeddings.npy.tmp"
    manifest_temp = directory / "manifest.json.tmp"
    matrix = np.stack(vectors).astype(np.float16)
    with matrix_temp.open("wb") as output:
        np.save(output, matrix)
    os.replace(matrix_temp, matrix_path)
    matrix_key = embedding_storage_key(matrix_path)
    manifest = {
        "version": version,
        "model_code": "dinov2-base",
        "dtype": "float16",
        "dimension": int(matrix.shape[1]),
        "count": int(matrix.shape[0]),
        "references": [
            {
                "row": index,
                "reference_image_id": int(getattr(reference, "id")),
                "image_path": str(getattr(reference, "image_path")),
            }
            for index, reference in enumerate(active_references)
        ],
    }
    manifest_temp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(manifest_temp, manifest_path)
    manifest_key = embedding_storage_key(manifest_path)

    for index, reference in enumerate(active_references):
        setattr(reference, "embedding_path", matrix_key)
        setattr(reference, "embedding_index", index)
        setattr(reference, "embedding_dimension", int(matrix.shape[1]))
        setattr(reference, "quality_status", "READY")
    setattr(group, "embedding_set_version", version)
    setattr(group, "embedding_matrix_path", matrix_key)
    setattr(group, "embedding_manifest_path", manifest_key)
    setattr(group, "embedding_count", int(matrix.shape[0]))
    invalidate_embedding_cache(matrix_path)
    return {
        "count": int(matrix.shape[0]),
        "dimension": int(matrix.shape[1]),
        "version": version,
        "matrix_path": matrix_key,
        "manifest_path": manifest_key,
    }


def rank_reference_vectors(
    query_values: Iterable[float] | np.ndarray,
    references: Sequence[ReferenceVector],
    top_k: int = 3,
) -> dict[str, object]:
    if not references:
        raise ValueError("No usable reference embeddings are available.")
    query = normalize_embedding(query_values)
    reference_matrix = np.stack([item.vector for item in references])
    similarities = reference_matrix @ query
    candidates = [
        {
            "reference_id": reference.reference_id,
            "reference_path": reference.image_path,
            "similarity": round(float(similarities[index]), 6),
        }
        for index, reference in enumerate(references)
    ]
    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    selected = candidates[: max(1, min(top_k, len(candidates)))]
    selected_scores = np.asarray(
        [float(item["similarity"]) for item in selected],
        dtype=np.float32,
    )
    top1 = float(selected_scores[0])
    top2 = float(selected[1]["similarity"]) if len(selected) > 1 else None
    top_k_mean = float(np.mean(selected_scores))
    top1_weight = max(0.0, min(1.0, settings.reference_similarity_top1_weight))
    robust_similarity = (
        top1
        if len(selected) == 1
        else top1_weight * top1 + (1.0 - top1_weight) * top_k_mean
    )
    return {
        "top1_similarity": top1,
        "top2_similarity": top2,
        "margin": round(top1 - top2, 6) if top2 is not None else None,
        "top_k_mean": round(top_k_mean, 6),
        "top_k_median": round(float(np.median(selected_scores)), 6),
        "top_k_std": round(float(np.std(selected_scores)), 6),
        "robust_similarity": round(float(robust_similarity), 6),
        "top1_weight": top1_weight,
        "selected_count": len(selected),
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
