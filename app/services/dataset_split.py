"""Stable 70/20/10 training split helpers.

The assignment is written to dataset items when a training job is created and
the exact manifest is stored in that job snapshot.  This makes a training run
reproducible even if the dataset changes later.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any


SPLIT_ORDER = ("TRAIN", "VAL", "TEST")


def _split_counts(total: int) -> dict[str, int]:
    if total <= 0:
        return {name: 0 for name in SPLIT_ORDER}

    train = int(total * 0.7)
    validation = int(total * 0.2)
    test = total - train - validation

    if total >= 3:
        validation = max(validation, 1)
        test = max(test, 1)
        train = total - validation - test
        if train < 1:
            train = 1
            validation = max(1, total - train - test)
            test = total - train - validation
    elif total == 2:
        train, validation, test = 1, 1, 0
    else:
        train, validation, test = 1, 0, 0

    return {"TRAIN": train, "VAL": validation, "TEST": test}


def assign_training_splits(
    items: Iterable[Any],
    *,
    seed: str,
) -> dict[str, Any]:
    """Assign deterministic exact-ratio splits and return an immutable manifest."""

    active_items = [item for item in items if not getattr(item, "is_deleted", False)]
    ordered_items = sorted(
        active_items,
        key=lambda item: hashlib.sha256(
            f"{seed}:{getattr(item, 'content_hash', None) or getattr(item, 'id', '')}".encode("utf-8")
        ).hexdigest(),
    )
    counts = _split_counts(len(ordered_items))
    boundaries = (counts["TRAIN"], counts["TRAIN"] + counts["VAL"])
    manifest: list[dict[str, Any]] = []
    changed = 0

    for index, item in enumerate(ordered_items):
        split = "TRAIN" if index < boundaries[0] else "VAL" if index < boundaries[1] else "TEST"
        if getattr(item, "split", None) != split:
            item.split = split
            changed += 1
        manifest.append({"dataset_item_id": item.id, "split": split})

    return {
        "ratio": {"TRAIN": 0.7, "VAL": 0.2, "TEST": 0.1},
        "counts": counts,
        "total": len(ordered_items),
        "changed": changed,
        "manifest": manifest,
    }
