import shutil
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT, settings
from app.db.session import SessionLocal
from app.models.inspection import DetectionItemResult, DetectionTask, InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceCandidate, ReferenceGroup, ReferenceImage
from app.services.algorithm_client import AlgorithmServiceClient


ACTIVE_CANDIDATE_STATUSES = {
    "PENDING_VLM",
    "ACCEPTED",
    "REJECTED",
    "UNCERTAIN",
    "ERROR",
}


def primary_rules_pass(result_rows: list[DetectionItemResult]) -> bool:
    for row in result_rows:
        if row.status != "OK":
            return False
        actual = row.actual_json if isinstance(row.actual_json, dict) else {}
        primary_status = str(actual.get("primary_status", row.status)).upper()
        if primary_status != "OK":
            return False
    return True


def image_quality_metrics(image_path: str) -> dict[str, Any]:
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Unable to read candidate ROI: {image_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    dark_ratio = float(np.mean(gray <= 8))
    bright_ratio = float(np.mean(gray >= 247))
    height, width = gray.shape[:2]
    passed = (
        min(width, height) >= 24
        and 15.0 <= brightness <= 245.0
        and blur_variance >= 15.0
        and dark_ratio <= 0.80
        and bright_ratio <= 0.80
    )
    return {
        "passed": passed,
        "width": width,
        "height": height,
        "brightness": round(brightness, 3),
        "blur_variance": round(blur_variance, 3),
        "dark_ratio": round(dark_ratio, 6),
        "bright_ratio": round(bright_ratio, 6),
    }


def perceptual_hash(image_path: str) -> str:
    with Image.open(image_path) as opened:
        image = opened.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
    pixels = np.asarray(image, dtype=np.float32)
    bits = pixels >= float(np.mean(pixels))
    packed = np.packbits(bits.reshape(-1))
    return packed.tobytes().hex()


def perceptual_hash_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right)) * 4
    return sum((int(a, 16) ^ int(b, 16)).bit_count() for a, b in zip(left, right))


def parse_candidate_decision(
    response: dict[str, Any],
    minimum_confidence: float,
) -> tuple[str, float | None, dict[str, Any], str]:
    result = response.get("result")
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        return "ERROR", None, {}, "VLM did not return structured comparison data."

    verdict = str(parsed.get("result", "UNCERTAIN")).upper()
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        confidence = None

    required_true = (
        "same_object",
        "object_present",
        "appearance_consistent",
        "installation_consistent",
        "image_quality_ok",
    )
    evidence_passed = all(parsed.get(field) is True for field in required_true)
    evidence_passed = evidence_passed and parsed.get("critical_difference") is False
    confidence_passed = confidence is not None and confidence >= minimum_confidence
    reason = str(parsed.get("reason") or "VLM comparison completed.")

    if verdict == "PASS" and evidence_passed and confidence_passed:
        return "ACCEPTED", confidence, parsed, reason
    if verdict == "UNCERTAIN" or not confidence_passed:
        return "UNCERTAIN", confidence, parsed, reason
    return "REJECTED", confidence, parsed, reason


def build_candidate_prompt(
    roi: RegionOfInterest,
    rows: list[tuple[DetectionItemResult, InspectionItem]],
) -> tuple[str, dict[str, Any]]:
    rules = []
    for item_result, item in rows:
        rules.append(
            {
                "name": item.name,
                "type": item.inspection_type,
                "expected": item.expected_json,
                "actual": item_result.actual_json,
                "status": item_result.status,
                "score": item_result.score,
            }
        )
    prompt = (
        f"Compare the approved baseline and candidate images for inspection area "
        f"'{roi.name}'. Confirm that the same expected object is present, its visible "
        "model appearance and installation state remain consistent, and there is no "
        "critical difference. OCR and color hard-rule results are supplied as evidence; "
        "do not override a failed hard rule."
    )
    return prompt, {"roi_code": roi.code, "roi_name": roi.name, "rules": rules}


class ReferenceCandidateCollector:
    def __init__(self) -> None:
        self.algorithms = AlgorithmServiceClient()

    async def collect_request_ids(self, request_ids: list[str]) -> None:
        if not settings.reference_candidate_collection_enabled:
            return
        for request_id in request_ids:
            try:
                await self._collect_request_id(request_id)
            except Exception:
                continue

    async def _collect_request_id(self, request_id: str) -> None:
        with SessionLocal() as database:
            task = database.scalar(
                select(DetectionTask)
                .options(selectinload(DetectionTask.item_results))
                .where(DetectionTask.request_id == request_id)
            )
            if task is None or task.status != "OK":
                return
            recipe = database.get(Recipe, task.recipe_id)
            if recipe is None:
                return

            rows_by_roi: dict[int, list[DetectionItemResult]] = {}
            for item_result in task.item_results:
                rows_by_roi.setdefault(item_result.roi_id, []).append(item_result)

            for roi_id, result_rows in rows_by_roi.items():
                await self._collect_roi(database, task, recipe, roi_id, result_rows)

    async def _collect_roi(
        self,
        database: Session,
        task: DetectionTask,
        recipe: Recipe,
        roi_id: int,
        result_rows: list[DetectionItemResult],
    ) -> None:
        if not result_rows or not primary_rules_pass(result_rows):
            return
        roi = database.get(RegionOfInterest, roi_id)
        if roi is None:
            return

        item_rows: list[tuple[DetectionItemResult, InspectionItem]] = []
        similarity_row: tuple[DetectionItemResult, InspectionItem] | None = None
        for result_row in result_rows:
            item = database.get(InspectionItem, result_row.inspection_item_id)
            if item is None:
                return
            item_rows.append((result_row, item))
            if item.capability.upper() == "REFERENCE_SIMILARITY":
                similarity_row = (result_row, item)
        if similarity_row is None:
            return

        similarity_result, similarity_item = similarity_row
        score = similarity_result.score
        if score is None or score < settings.reference_candidate_similarity_threshold:
            return
        if similarity_item.reference_group_id is None:
            return
        group = database.get(ReferenceGroup, similarity_item.reference_group_id)
        if group is None or not group.enabled or group.is_deleted:
            return

        baseline_path = str(
            similarity_result.actual_json.get("matched_reference") or ""
        )
        if not Path(baseline_path).is_file():
            baseline = database.scalar(
                select(ReferenceImage)
                .where(
                    ReferenceImage.group_id == group.id,
                    ReferenceImage.enabled.is_(True),
                    ReferenceImage.is_deleted.is_(False),
                    ReferenceImage.quality_status == "READY",
                )
                .order_by(ReferenceImage.id.desc())
            )
            if baseline is None:
                return
            baseline_path = baseline.image_path

        source_path = str(result_rows[0].roi_image_path or "")
        if not Path(source_path).is_file():
            return
        quality = image_quality_metrics(source_path)
        if not quality["passed"]:
            return
        content_hash = perceptual_hash(source_path)
        existing_hashes = database.scalars(
            select(ReferenceCandidate).where(
                ReferenceCandidate.roi_id == roi.id,
                ReferenceCandidate.is_deleted.is_(False),
            )
        ).all()
        if any(
            perceptual_hash_distance(item.content_hash, content_hash)
            <= max(0, settings.reference_candidate_hash_distance)
            for item in existing_hashes
        ):
            return

        destination_root = Path(
            PROJECT_ROOT / "uploads" / "reference_candidates" / str(roi.id)
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / f"{uuid.uuid4().hex}.jpg"
        shutil.copy2(source_path, destination)
        prompt, rule_snapshot = build_candidate_prompt(roi, item_rows)
        candidate = ReferenceCandidate(
            group_id=group.id,
            recipe_id=recipe.id,
            roi_id=roi.id,
            source_task_id=task.id,
            source_item_result_id=similarity_result.id,
            sn=task.sn,
            baseline_image_path=baseline_path,
            candidate_image_path=str(destination),
            content_hash=content_hash,
            similarity_score=float(score),
            quality_json=quality,
            rule_snapshot=rule_snapshot,
            status="PENDING_VLM",
        )
        database.add(candidate)
        database.commit()
        database.refresh(candidate)

        try:
            response = await self.algorithms.vlm_compare(
                baseline_path,
                str(destination),
                prompt,
                expected=rule_snapshot,
            )
            status, confidence, parsed, reason = parse_candidate_decision(
                response,
                settings.reference_candidate_vlm_confidence_threshold,
            )
            candidate.status = status
            candidate.vlm_confidence = confidence
            candidate.vlm_result_json = {
                "model": response.get("model"),
                "elapsed_ms": response.get("elapsed_ms"),
                "parsed": parsed,
            }
            candidate.reason = reason
        except Exception as exc:
            candidate.status = "ERROR"
            candidate.reason = str(exc)
            candidate.vlm_result_json = {"error": str(exc)}
        database.commit()
        self._apply_retention(database, roi.id)

    @staticmethod
    def _apply_retention(database: Session, roi_id: int) -> None:
        candidates = database.scalars(
            select(ReferenceCandidate)
            .where(
                ReferenceCandidate.roi_id == roi_id,
                ReferenceCandidate.is_deleted.is_(False),
                ReferenceCandidate.status.in_(ACTIVE_CANDIDATE_STATUSES),
            )
            .order_by(ReferenceCandidate.id.desc())
        ).all()
        limit = max(1, settings.reference_candidate_limit_per_roi)
        for candidate in candidates[limit:]:
            candidate.is_deleted = True
            candidate.reason = (
                f"Archived by candidate retention policy. {candidate.reason or ''}"
            ).strip()
        database.commit()


candidate_collector = ReferenceCandidateCollector()


async def collect_reference_candidates(request_ids: list[str]) -> None:
    await candidate_collector.collect_request_ids(request_ids)
