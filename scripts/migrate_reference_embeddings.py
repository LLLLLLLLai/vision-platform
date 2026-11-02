import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.db.init_db import init_database
from app.db.session import SessionLocal
from app.models.inspection import InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceImage
from app.services.reference_embedding_service import (
    reference_set_directory,
    write_reference_matrix,
)


def _context(database, group_id: int):
    item = database.scalar(
        select(InspectionItem)
        .where(InspectionItem.reference_group_id == group_id)
        .order_by(InspectionItem.id)
    )
    if item is None:
        return None, None
    roi = database.get(RegionOfInterest, item.roi_id)
    if roi is None:
        return None, None
    return database.get(Recipe, roi.recipe_id), roi


def migrate(*, apply_changes: bool) -> int:
    init_database()
    migrated = 0
    skipped = 0
    failed = 0
    with SessionLocal() as database:
        groups = database.scalars(
            select(ReferenceGroup).where(
                ReferenceGroup.enabled.is_(True),
                ReferenceGroup.is_deleted.is_(False),
            )
        ).all()
        for group in groups:
            references = database.scalars(
                select(ReferenceImage).where(
                    ReferenceImage.group_id == group.id,
                    ReferenceImage.enabled.is_(True),
                    ReferenceImage.is_deleted.is_(False),
                    ReferenceImage.quality_status == "READY",
                )
            ).all()
            if not references:
                skipped += 1
                continue
            recipe, roi = _context(database, group.id)
            target = reference_set_directory(
                group,
                int(group.embedding_set_version or 0) + 1,
                recipe=recipe,
                roi=roi,
            )
            if not apply_changes:
                print(
                    f"DRY-RUN group={group.id} references={len(references)} "
                    f"target={target}"
                )
                migrated += 1
                continue
            try:
                result = write_reference_matrix(
                    group,
                    references,
                    recipe=recipe,
                    roi=roi,
                )
                database.commit()
                print(
                    f"MIGRATED group={group.id} references={result['count']} "
                    f"matrix={result['matrix_path']}"
                )
                migrated += 1
            except Exception as exc:
                database.rollback()
                print(f"FAILED group={group.id} error={exc}")
                failed += 1
    print(f"SUMMARY migrated={migrated} skipped={skipped} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate active reference embeddings into one matrix per ROI set."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write new matrix files and update database records.",
    )
    args = parser.parse_args()
    return migrate(apply_changes=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
