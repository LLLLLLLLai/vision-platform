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
    if total == 1:
        return {"TRAIN": 1, "VAL": 0, "TEST": 0}
    if total == 2:
        return {"TRAIN": 1, "VAL": 1, "TEST": 0}

    ratios = {"TRAIN": 0.7, "VAL": 0.2, "TEST": 0.1}
    counts = {name: max(1, int(total * ratios[name])) for name in SPLIT_ORDER}
    while sum(counts.values()) < total:
        chosen = max(SPLIT_ORDER, key=lambda name: total * ratios[name] - counts[name])
        counts[chosen] += 1
    while sum(counts.values()) > total:
        chosen = max(
            (name for name in SPLIT_ORDER if counts[name] > 1),
            key=lambda name: counts[name] - total * ratios[name],
        )
        counts[chosen] -= 1
    return counts


def _augmentation_group_key(item: Any) -> str:
    """Return the original-sample group for split-leakage protection.

    Data augmentation creates images that are deliberately very similar to one
    source sample.  Keeping them in a different validation/test partition
    would make reported metrics look better than production reality.
    """

    annotation = getattr(item, "annotation_json", None)
    metadata = annotation.get("_meta") if isinstance(annotation, dict) else None
    augmentation = metadata.get("augmentation") if isinstance(metadata, dict) else None
    source_id = (
        augmentation.get("root_source_item_id", augmentation.get("source_item_id"))
        if isinstance(augmentation, dict)
        else None
    )
    try:
        return f"SOURCE:{int(source_id)}" if source_id is not None else f"SOURCE:{int(item.id)}"
    except (TypeError, ValueError):
        return f"SOURCE:{getattr(item, 'id', '')}"


def _valid_split(value: Any) -> str | None:
    candidate = str(value or "").upper()
    return candidate if candidate in SPLIT_ORDER else None


def assign_training_splits(
    items: Iterable[Any],
    *,
    seed: str,
) -> dict[str, Any]:
    """Assign stable 70/20/10 splits without separating augmented siblings.

    Existing valid assignments are retained.  This makes a training partition
    stable when a user adds more material later, while unassigned groups are
    deterministically placed into the most under-represented split.
    """

    active_items = [item for item in items if not getattr(item, "is_deleted", False)]
    groups: dict[str, list[Any]] = {}
    for item in active_items:
        groups.setdefault(_augmentation_group_key(item), []).append(item)

    ordered_group_keys = sorted(
        groups,
        key=lambda group_key: hashlib.sha256(f"{seed}:{group_key}".encode("utf-8")).hexdigest(),
    )
    target_group_counts = _split_counts(len(ordered_group_keys))
    assigned_groups: dict[str, str] = {}
    group_counts = {name: 0 for name in SPLIT_ORDER}
    changed = 0

    for group_key in ordered_group_keys:
        source_item = next(
            (item for item in groups[group_key] if f"SOURCE:{getattr(item, 'id', '')}" == group_key),
            None,
        )
        source_split = _valid_split(getattr(source_item, "split", None)) if source_item is not None else None
        existing_splits = [
            split for split in (_valid_split(getattr(item, "split", None)) for item in groups[group_key]) if split
        ]
        if not existing_splits:
            continue
        selected_split = source_split or existing_splits[0]
        assigned_groups[group_key] = selected_split
        group_counts[selected_split] += 1
        for item in groups[group_key]:
            if getattr(item, "split", None) != selected_split:
                item.split = selected_split
                changed += 1

    for group_key in ordered_group_keys:
        if group_key in assigned_groups:
            continue
        deficits = {
            split: target_group_counts[split] - group_counts[split]
            for split in SPLIT_ORDER
        }
        selected_split = max(
            SPLIT_ORDER,
            key=lambda split: (deficits[split], -group_counts[split], -SPLIT_ORDER.index(split)),
        )
        assigned_groups[group_key] = selected_split
        group_counts[selected_split] += 1
        for item in groups[group_key]:
            if getattr(item, "split", None) != selected_split:
                item.split = selected_split
                changed += 1

    counts = {name: 0 for name in SPLIT_ORDER}
    manifest: list[dict[str, Any]] = []
    for item in sorted(active_items, key=lambda candidate: getattr(candidate, "id", 0)):
        split = _valid_split(getattr(item, "split", None)) or "TRAIN"
        counts[split] += 1
        manifest.append(
            {
                "dataset_item_id": item.id,
                "split": split,
                "augmentation_group": _augmentation_group_key(item),
            }
        )

    return {
        "ratio": {"TRAIN": 0.7, "VAL": 0.2, "TEST": 0.1},
        "counts": counts,
        "group_target_counts": target_group_counts,
        "group_counts": group_counts,
        "group_total": len(ordered_group_keys),
        "total": len(active_items),
        "changed": changed,
        "manifest": manifest,
    }
