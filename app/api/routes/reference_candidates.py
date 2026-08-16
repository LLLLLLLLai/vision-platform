from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceCandidate, ReferenceGroup, ReferenceImage
from app.services.algorithm_client import AlgorithmServiceClient


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
    return [
        _candidate_payload(
            candidate,
            database.get(Recipe, candidate.recipe_id),
            database.get(RegionOfInterest, candidate.roi_id),
            database.get(ReferenceGroup, candidate.group_id),
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

    reference = ReferenceImage(
        group_id=candidate.group_id,
        image_path=candidate.candidate_image_path,
        quality_status="PENDING",
    )
    database.add(reference)
    database.commit()
    database.refresh(reference)

    try:
        response = await algorithm_client.embedding(candidate.candidate_image_path)
        embedding_path = Path(
            PROJECT_ROOT
            / "embeddings"
            / str(candidate.group_id)
            / f"{reference.id}.npy"
        )
        embedding_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(embedding_path, np.asarray(response["embedding"], dtype=np.float32))
        reference.embedding_path = str(embedding_path)
        reference.embedding_dimension = int(response["dimension"])
        reference.quality_status = "READY"
    except Exception:
        reference.quality_status = "PENDING_RETRY"

    candidate.status = "PROMOTED"
    candidate.promoted_reference_image_id = reference.id
    candidate.reason = "Promoted to approved reference library by user."
    database.commit()
    return {
        "id": candidate.id,
        "status": candidate.status,
        "reference_image_id": reference.id,
        "embedding_status": reference.quality_status,
    }
