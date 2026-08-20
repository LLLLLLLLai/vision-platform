from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.db.session import get_db
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceCandidate, ReferenceGroup, ReferenceImage
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.reference_embedding_service import (
    decide_reference_addition,
    load_reference_vectors,
    save_embedding,
)


router = APIRouter()
algorithm_client = AlgorithmServiceClient()


def _file_url(path: str) -> str | None:
    file_path = Path(path).resolve()
    uploads_root = Path(PROJECT_ROOT / "uploads").resolve()
    try:
        relative = file_path.relative_to(uploads_root)
    except ValueError:
        return None
    return "/files/" + relative.as_posix()


def _candidate_payload(
    candidate: ReferenceCandidate,
    recipe: Recipe | None,
    roi: RegionOfInterest | None,
    group: ReferenceGroup | None,
    reference_count: int,
) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "status": candidate.status,
        "sn": candidate.sn,
        "recipe_id": candidate.recipe_id,
        "recipe_code": recipe.code if recipe else None,
        "recipe_name": recipe.name if recipe else None,
        "roi_id": candidate.roi_id,
        "roi_code": roi.code if roi else None,
        "roi_name": roi.name if roi else None,
        "group_id": candidate.group_id,
        "group_name": group.name if group else None,
        "baseline_image_url": _file_url(candidate.baseline_image_path),
        "candidate_image_url": _file_url(candidate.candidate_image_path),
        "similarity_score": candidate.similarity_score,
        "quality": candidate.quality_json,
        "rule_snapshot": candidate.rule_snapshot,
        "vlm_result": candidate.vlm_result_json,
        "vlm_confidence": candidate.vlm_confidence,
        "reason": candidate.reason,
        "promoted_reference_image_id": candidate.promoted_reference_image_id,
        "active_reference_count": reference_count,
        "reference_limit": settings.reference_approved_limit_per_group,
        "created_at": candidate.created_at.isoformat(),
    }


@router.get("")
def list_candidates(
    status: str | None = None,
    limit: int = 100,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(ReferenceCandidate).where(
        ReferenceCandidate.is_deleted.is_(False)
    )
    if status:
        statement = statement.where(ReferenceCandidate.status == status.upper())
    candidates = database.scalars(
        statement.order_by(ReferenceCandidate.id.desc()).limit(min(max(limit, 1), 500))
    ).all()
    reference_counts = dict(
        database.execute(
            select(ReferenceImage.group_id, func.count(ReferenceImage.id))
            .where(
                ReferenceImage.enabled.is_(True),
                ReferenceImage.is_deleted.is_(False),
            )
            .group_by(ReferenceImage.group_id)
        ).all()
    )
    return [
        _candidate_payload(
            candidate,
            database.get(Recipe, candidate.recipe_id),
            database.get(RegionOfInterest, candidate.roi_id),
            database.get(ReferenceGroup, candidate.group_id),
            int(reference_counts.get(candidate.group_id, 0)),
        )
        for candidate in candidates
    ]


@router.post("/{candidate_id}/reject")
def reject_candidate(
    candidate_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    candidate = database.get(ReferenceCandidate, candidate_id)
    if candidate is None or candidate.is_deleted:
        raise HTTPException(status_code=404, detail="Candidate image not found.")
    if candidate.status == "PROMOTED":
        raise HTTPException(status_code=409, detail="Promoted image cannot be rejected here.")
    candidate.status = "REJECTED"
    candidate.reason = "Rejected by user."
    database.commit()
    return {"id": candidate.id, "status": candidate.status}


@router.post("/{candidate_id}/promote")
async def promote_candidate(
    candidate_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    candidate = database.get(ReferenceCandidate, candidate_id)
    if candidate is None or candidate.is_deleted:
        raise HTTPException(status_code=404, detail="Candidate image not found.")
    if candidate.status not in {"ACCEPTED", "UNCERTAIN"}:
        raise HTTPException(
            status_code=409,
            detail="Only accepted or manually confirmed uncertain images can be promoted.",
        )
    if candidate.promoted_reference_image_id is not None:
        return {
            "id": candidate.id,
            "status": "PROMOTED",
            "reference_image_id": candidate.promoted_reference_image_id,
        }

    active_references = database.scalars(
        select(ReferenceImage).where(
            ReferenceImage.group_id == candidate.group_id,
            ReferenceImage.enabled.is_(True),
            ReferenceImage.is_deleted.is_(False),
        )
    ).all()
    response: dict[str, Any] | None = None
    try:
        response = await algorithm_client.embedding(candidate.candidate_image_path)
    except Exception as exc:
        if len(active_references) >= settings.reference_approved_limit_per_group:
            raise HTTPException(
                status_code=503,
                detail=(
                    "DINOv2 embedding is unavailable and the approved reference set "
                    "is already at capacity. Retry after the model service recovers."
                ),
            ) from exc

    replacement: ReferenceImage | None = None
    if response is not None:
        reference_vectors = load_reference_vectors(active_references)
        if (
            len(active_references) >= settings.reference_approved_limit_per_group
            and len(reference_vectors) != len(active_references)
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Some approved references have no usable embedding. Regenerate "
                    "them before adding another reference."
                ),
            )
        decision = decide_reference_addition(
            response["embedding"],
            reference_vectors,
            limit=settings.reference_approved_limit_per_group,
            duplicate_threshold=settings.reference_duplicate_similarity_threshold,
            diversity_margin=settings.reference_diversity_improvement_margin,
        )
        if decision.action.startswith("SKIP_"):
            candidate.status = "SKIPPED"
            candidate.reason = (
                "候选图与现有正式基准重复或未增加新的正常外观变化，"
                f"未加入基准。最近相似度：{decision.nearest_similarity:.4f}"
                if decision.nearest_similarity is not None
                else "候选图未增加新的正常外观变化，未加入基准。"
            )
            database.commit()
            return {
                "id": candidate.id,
                "status": candidate.status,
                "skipped": True,
                "reason": candidate.reason,
                "active_reference_count": len(active_references),
            }
        if decision.replace_reference_id is not None:
            replacement = database.get(ReferenceImage, decision.replace_reference_id)
            if replacement is not None:
                replacement.enabled = False

    reference = ReferenceImage(
        group_id=candidate.group_id,
        image_path=candidate.candidate_image_path,
        quality_status="PENDING",
    )
    database.add(reference)
    database.flush()
    if response is not None:
        embedding_path = Path(
            PROJECT_ROOT
            / "embeddings"
            / str(candidate.group_id)
            / f"{reference.id}.npy"
        )
        reference.embedding_dimension = save_embedding(
            embedding_path,
            response["embedding"],
        )
        reference.embedding_path = str(embedding_path)
        reference.quality_status = "READY"
    else:
        reference.quality_status = "PENDING_RETRY"

    candidate.status = "PROMOTED"
    candidate.promoted_reference_image_id = reference.id
    candidate.reason = (
        "已加入正式基准，并软停用一张高度重复的旧基准。"
        if replacement is not None
        else "已加入正式基准。"
    )
    database.commit()
    return {
        "id": candidate.id,
        "status": candidate.status,
        "reference_image_id": reference.id,
        "embedding_status": reference.quality_status,
        "replaced_reference_image_id": replacement.id if replacement else None,
        "active_reference_count": min(
            len(active_references) + 1,
            settings.reference_approved_limit_per_group,
        ),
        "reference_limit": settings.reference_approved_limit_per_group,
    }
