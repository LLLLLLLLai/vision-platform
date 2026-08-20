import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT
from app.models.inspection import DetectionItemResult, DetectionTask, InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceGroup
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.image_processing import annotate_image, color_ratio, crop_roi
from app.services.reference_embedding_service import (
    load_reference_vectors,
    rank_reference_vectors,
)


def similarity_review_band(
    rule: dict[str, Any],
    minimum: float,
) -> tuple[float, float]:
    lower = float(rule.get("vlm_review_lower", max(0.0, minimum - 0.05)))
    upper = float(rule.get("vlm_review_upper", min(1.0, minimum + 0.03)))
    return max(0.0, min(lower, upper)), min(1.0, max(lower, upper))


def should_run_vlm_review(
    score: float | None,
    rule: dict[str, Any],
    minimum: float,
    force: bool = False,
) -> bool:
    if not bool(rule.get("vlm_review_enabled", False)):
        return False
    if force:
        return True
    if str(rule.get("vlm_review_mode", "LOW_CONFIDENCE")).upper() == "ALWAYS":
        return True
    if score is None:
        return False
    lower, upper = similarity_review_band(rule, minimum)
    return lower <= score <= upper


def vlm_review_status(
    payload: dict[str, Any],
    fallback: str,
) -> tuple[str, dict[str, Any] | None]:
    result = payload.get("result")
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        return fallback, None
    verdict = str(parsed.get("result", "UNCERTAIN")).upper()
    if verdict in {"OK", "NG"}:
        return verdict, parsed
    return fallback, parsed


class InspectionEngine:
    def __init__(self) -> None:
        self.algorithms = AlgorithmServiceClient()

    async def execute(
        self,
        database: Session,
        recipe: Recipe,
        sn: str,
        image_paths: list[str],
        request_id: str | None = None,
        force_vlm_review: bool = False,
        roi_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        request_id = request_id or uuid.uuid4().hex
        task = DetectionTask(
            request_id=request_id,
            sn=sn,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            original_image_paths=image_paths,
        )
        database.add(task)
        database.commit()
        database.refresh(task)
        task_root = Path(PROJECT_ROOT / "detection_results" / request_id)
        result_image_paths: list[str] = []
        image_results: list[dict[str, Any]] = []
        overall_status = "OK"

        try:
            for image_index, image_path in enumerate(image_paths, start=1):
                image_file = Path(image_path).expanduser().resolve()
                if not image_file.is_file():
                    raise FileNotFoundError(f"Image does not exist: {image_file}")
                item_results: list[dict[str, Any]] = []
                annotations: list[dict[str, Any]] = []
                image_status = "OK"

                for roi in sorted(recipe.rois, key=lambda value: value.sort_order):
                    if not roi.enabled:
                        continue
                    if roi_ids is not None and roi.id not in roi_ids:
                        continue
                    roi_file = task_root / f"image_{image_index}" / f"{roi.code}.jpg"
                    _, box = crop_roi(str(image_file), roi, roi_file)
                    roi_status = "OK"

                    for item in sorted(
                        roi.inspection_items,
                        key=lambda value: value.execution_order,
                    ):
                        if not item.enabled:
                            continue
                        result = await self._execute_item(
                            database,
                            item,
                            str(roi_file),
                            force_vlm_review=force_vlm_review,
                        )
                        if result["status"] == "ERROR":
                            overall_status = image_status = roi_status = "ERROR"
                        elif result["status"] == "NG" and image_status != "ERROR":
                            if overall_status != "ERROR":
                                overall_status = "NG"
                            image_status = roi_status = "NG"
                        database.add(
                            DetectionItemResult(
                                task_id=task.id,
                                image_path=str(image_file),
                                roi_id=roi.id,
                                inspection_item_id=item.id,
                                status=result["status"],
                                expected_json=item.expected_json,
                                actual_json=result.get("actual", {}),
                                score=result.get("score"),
                                message=result.get("message"),
                                roi_image_path=str(roi_file),
                                elapsed_ms=result.get("elapsed_ms"),
                            )
                        )
                        item_results.append(
                            {
                                "roi_code": roi.code,
                                "roi_image_url": (
                                    f"/results/{request_id}/image_{image_index}/"
                                    f"{roi.code}.jpg"
                                ),
                                "item_code": item.code,
                                "item_name": item.name,
                                "inspection_type": item.inspection_type,
                                "capability": item.capability,
                                "scene_type": item.rule_json.get("scene_type"),
                                "primary_model": (
                                    result.get("actual", {})
                                    .get("primary_result", {})
                                    .get("model")
                                    or item.rule_json.get("primary_model")
                                ),
                                "vlm_review_enabled": bool(
                                    item.rule_json.get("vlm_review_enabled", False)
                                ),
                                **result,
                            }
                        )
                    annotations.append(
                        {"box": box, "code": roi.code, "status": roi_status}
                    )

                result_path = task_root / f"result_{image_index}.jpg"
                annotate_image(str(image_file), annotations, result_path)
                result_image_paths.append(str(result_path))
                image_results.append(
                    {
                        "image_path": str(image_file),
                        "result_image_path": str(result_path),
                        "result": image_status,
                        "inspection_items": item_results,
                    }
                )

            task.status = overall_status
            task.result_image_paths = result_image_paths
            task.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            task.completed_at = datetime.utcnow()
            database.commit()
        except Exception as exc:
            task.status = "ERROR"
            task.error_message = str(exc)
            task.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            task.completed_at = datetime.utcnow()
            database.commit()
            raise

        return {
            "code": 0,
            "message": "success",
            "request_id": request_id,
            "sn": sn,
            "result": overall_status,
            "recipe_code": recipe.code,
            "recipe_version": recipe.version,
            "image_paths": result_image_paths,
            "image_results": image_results,
            "elapsed_ms": task.elapsed_ms,
        }

    async def _execute_item(
        self,
        database: Session,
        item: InspectionItem,
        roi_path: str,
        force_vlm_review: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            if item.capability.upper() == "REFERENCE_SIMILARITY":
                result = await self._reference_similarity(
                    database,
                    item,
                    roi_path,
                    force_vlm_review=force_vlm_review,
                )
            elif item.capability.upper() == "COLOR_RATIO":
                result = self._color(item, roi_path)
                result = await self._apply_vlm_review(
                    item,
                    roi_path,
                    result,
                    primary_model="OpenCV",
                    minimum=float(item.rule_json.get("min_ratio", 0.15)),
                    force_vlm_review=force_vlm_review,
                )
            elif item.capability.upper() == "OCR_TEXT":
                result = await self._ocr(item, roi_path)
                primary_model = str(result.pop("primary_model", "PaddleOCR"))
                result = await self._apply_vlm_review(
                    item,
                    roi_path,
                    result,
                    primary_model=primary_model,
                    minimum=0.0,
                    force_vlm_review=force_vlm_review,
                )
            elif item.capability.upper() == "VLM_JUDGEMENT":
                result = await self._vlm_judgement(item, roi_path)
            else:
                raise ValueError(f"Unsupported capability: {item.capability}")
        except Exception as exc:
            result = {
                "status": "ERROR",
                "actual": {},
                "score": None,
                "message": str(exc),
            }
            if (
                force_vlm_review
                and item.capability.upper() != "VLM_JUDGEMENT"
                and bool(item.rule_json.get("vlm_review_enabled", False))
            ):
                primary_models = {
                    "REFERENCE_SIMILARITY": "DINOv2",
                    "COLOR_RATIO": "OpenCV",
                    "OCR_TEXT": "PaddleOCR",
                }
                result = await self._apply_vlm_review(
                    item,
                    roi_path,
                    result,
                    primary_model=primary_models.get(
                        item.capability.upper(),
                        item.capability,
                    ),
                    minimum=0.0,
                    force_vlm_review=True,
                )
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    async def test_draft_roi(
        self,
        database: Session,
        recipe: Recipe,
        roi: RegionOfInterest,
        image_path: str,
        rules: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        request_id = f"draft-{uuid.uuid4().hex}"
        task_root = Path(PROJECT_ROOT / "detection_results" / request_id)
        roi_path = task_root / f"{roi.code}.jpg"
        _, box = crop_roi(image_path, roi, roi_path)
        item_results: list[dict[str, Any]] = []
        roi_status = "OK"

        for index, rule in enumerate(rules, start=1):
            rule_type = str(rule.get("type", "")).upper()
            value = str(rule.get("value", "")).strip()
            scene_type = str(rule.get("scene", "OBJECT_EXISTENCE"))
            common_rule = {
                "scene_type": scene_type,
                "vlm_review_enabled": bool(review.get("enabled", False)),
                "vlm_review_mode": str(review.get("mode", "ALWAYS")),
                "vlm_review_lower": float(review.get("lower", 0.85)),
                "vlm_review_upper": float(review.get("upper", 0.93)),
                "vlm_prompt": str(review.get("prompt", "")),
                "vlm_prompt_auto": bool(review.get("prompt_auto", True)),
                "vlm_uncertain_result": "NG",
            }
            if rule_type == "EXISTENCE":
                item = InspectionItem(
                    roi_id=roi.id,
                    code=f"{roi.code}_EXISTENCE_{index}",
                    name=f"存在校验 {index}",
                    inspection_type="EXISTENCE",
                    capability="REFERENCE_SIMILARITY",
                    reference_group_id=int(rule["reference_group_id"]),
                    expected_json={
                        "exists": True,
                        "class_code": str(rule.get("class_code", roi.code)),
                    },
                    rule_json={
                        **common_rule,
                        "min_similarity": float(value),
                        "primary_model": "DINOv2",
                    },
                )
            elif rule_type == "COLOR":
                item = InspectionItem(
                    roi_id=roi.id,
                    code=f"{roi.code}_COLOR_{index}",
                    name=f"颜色校验 {index}",
                    inspection_type="COLOR",
                    capability="COLOR_RATIO",
                    expected_json={"color": value.upper()},
                    rule_json={
                        **common_rule,
                        "min_ratio": 0.15,
                        "max_ratio": 1.0,
                        "primary_model": "OpenCV",
                    },
                )
            elif rule_type == "TEXT":
                item = InspectionItem(
                    roi_id=roi.id,
                    code=f"{roi.code}_TEXT_{index}",
                    name=f"OCR文字校验 {index}",
                    inspection_type="TEXT",
                    capability="OCR_TEXT",
                    expected_json={"text": value},
                    rule_json={
                        **common_rule,
                        "operator": "CONTAINS",
                        "case_sensitive": False,
                        "primary_model": "PaddleOCR",
                    },
                )
            else:
                raise ValueError(f"Unsupported draft rule type: {rule_type}")

            result = await self._execute_item(
                database,
                item,
                str(roi_path),
                force_vlm_review=True,
            )
            if result["status"] == "ERROR":
                roi_status = "ERROR"
            elif result["status"] == "NG" and roi_status != "ERROR":
                roi_status = "NG"
            item_results.append(
                {
                    "roi_code": roi.code,
                    "item_code": item.code,
                    "item_name": item.name,
                    "inspection_type": item.inspection_type,
                    "capability": item.capability,
                    "scene_type": scene_type,
                    "primary_model": (
                        result.get("actual", {})
                        .get("primary_result", {})
                        .get("model")
                        or item.rule_json.get("primary_model")
                    ),
                    "vlm_review_enabled": bool(review.get("enabled", False)),
                    **result,
                }
            )

        result_path = task_root / "result_1.jpg"
        annotate_image(
            image_path,
            [{"box": box, "code": roi.code, "status": roi_status}],
            result_path,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "code": 0,
            "message": "success",
            "request_id": request_id,
            "sn": "DRAFT_TEST",
            "result": roi_status,
            "recipe_code": recipe.code,
            "recipe_version": recipe.version,
            "image_paths": [str(result_path)],
            "image_results": [
                {
                    "image_path": image_path,
                    "result_image_path": str(result_path),
                    "roi_image_path": str(roi_path),
                    "roi_image_url": f"/results/{request_id}/{roi_path.name}",
                    "result": roi_status,
                    "inspection_items": item_results,
                }
            ],
            "elapsed_ms": elapsed_ms,
        }

    async def _reference_similarity(
        self,
        database: Session,
        item: InspectionItem,
        roi_path: str,
        force_vlm_review: bool = False,
    ) -> dict[str, Any]:
        if item.reference_group_id is None:
            raise ValueError("Reference group is not configured.")
        group = database.scalar(
            select(ReferenceGroup)
            .options(selectinload(ReferenceGroup.images))
            .where(ReferenceGroup.id == item.reference_group_id)
        )
        if group is None:
            raise ValueError("Reference group does not exist.")
        active_references = [
            image
            for image in group.images
            if image.enabled and not image.is_deleted
        ]
        reference_paths = [image.image_path for image in active_references]
        if not reference_paths:
            raise ValueError("Reference group has no enabled images.")
        reference_vectors = load_reference_vectors(active_references)
        if reference_vectors:
            query_response = await self.algorithms.embedding(roi_path)
            response = rank_reference_vectors(
                query_response["embedding"],
                reference_vectors,
                top_k=min(3, len(reference_vectors)),
            )
            response["model"] = query_response.get("model", "dinov2-base")
            response["embedding_cache_used"] = True
        else:
            response = await self.algorithms.similarity(
                roi_path,
                reference_paths,
                top_k=min(3, len(reference_paths)),
            )
            response["embedding_cache_used"] = False
        score = float(response["top1_similarity"])
        minimum = float(item.rule_json.get("min_similarity", 0.85))
        primary_status = "OK" if score >= minimum else "NG"
        actual = {
            "matched_class": group.class_code,
            "matched_reference": response["candidates"][0]["reference_path"],
            "similarity": score,
            "top_k_mean": response.get("top_k_mean"),
            "reference_count": response.get("reference_count", len(reference_paths)),
            "embedding_cache_used": response.get("embedding_cache_used", False),
            "primary_status": primary_status,
        }
        return await self._apply_vlm_review(
            item,
            roi_path,
            {
                "status": primary_status,
                "actual": actual,
                "score": score,
                "message": f"Similarity {score:.4f}, required >= {minimum:.4f}",
            },
            primary_model="DINOv2",
            minimum=minimum,
            force_vlm_review=force_vlm_review,
            default_prompt=(
                f"Verify whether the ROI contains the expected "
                f"{group.class_code} component and whether it is correctly installed. "
                "Judge only this component."
            ),
        )

    async def _apply_vlm_review(
        self,
        item: InspectionItem,
        roi_path: str,
        primary_result: dict[str, Any],
        *,
        primary_model: str,
        minimum: float,
        default_prompt: str | None = None,
        force_vlm_review: bool = False,
    ) -> dict[str, Any]:
        result = {
            "status": primary_result["status"],
            "actual": dict(primary_result.get("actual") or {}),
            "score": primary_result.get("score"),
            "message": str(primary_result.get("message") or ""),
        }
        result["actual"]["primary_status"] = primary_result["status"]
        result["actual"]["primary_result"] = {
            "model": primary_model,
            "status": primary_result["status"],
            "score": primary_result.get("score"),
            "details": primary_result.get("actual") or {},
            "message": primary_result.get("message"),
        }
        if not should_run_vlm_review(
            primary_result.get("score"),
            item.rule_json,
            minimum,
            force=force_vlm_review,
        ):
            return result

        fallback = str(item.rule_json.get("vlm_uncertain_result", "NG")).upper()
        if fallback not in {"OK", "NG"}:
            fallback = "NG"
        prompt = str(
            item.rule_json.get("vlm_prompt")
            or default_prompt
            or (
                f"Review the inspection result for {item.name}. "
                "Judge only the visible ROI and return OK, NG, or UNCERTAIN."
            )
        )
        try:
            review_response = await self.algorithms.vlm_judge(
                roi_path,
                prompt,
                expected={
                    **item.expected_json,
                    "primary_model": primary_model,
                    "primary_status": primary_result["status"],
                    "primary_score": primary_result.get("score"),
                },
                max_new_tokens=120,
            )
            review_status, parsed = vlm_review_status(review_response, fallback)
            result["status"] = review_status
            result["actual"]["vlm_review"] = {
                "status": review_status,
                "parsed": parsed,
                "model": review_response.get("model") or "Qwen3-VL-4B-Instruct",
                "elapsed_ms": review_response.get("elapsed_ms"),
                "prompt": prompt,
                "expected": item.expected_json,
            }
            result["message"] += f"; Qwen review => {review_status}"
        except Exception as exc:
            result["status"] = fallback
            result["actual"]["vlm_review"] = {
                "status": fallback,
                "model": "Qwen3-VL-4B-Instruct",
                "error": str(exc),
                "prompt": prompt,
                "expected": item.expected_json,
            }
            result["message"] += f"; Qwen review failed => {fallback}"
        return result

    async def _vlm_judgement(
        self,
        item: InspectionItem,
        roi_path: str,
    ) -> dict[str, Any]:
        prompt = str(
            item.rule_json.get("prompt")
            or item.expected_json.get("requirement")
            or f"Inspect {item.name}. Return OK, NG, or UNCERTAIN."
        )
        response = await self.algorithms.vlm_judge(
            roi_path,
            prompt,
            expected=item.expected_json,
            max_new_tokens=160,
        )
        status, parsed = vlm_review_status(response, "NG")
        score = None
        if isinstance(parsed, dict) and parsed.get("confidence") is not None:
            try:
                score = float(parsed["confidence"])
            except (TypeError, ValueError):
                score = None
        return {
            "status": status,
            "actual": {
                "primary_status": status,
                "primary_result": {
                    "model": response.get("model") or "Qwen3-VL-4B-Instruct",
                    "status": status,
                    "score": score,
                    "details": parsed or {},
                    "message": (parsed or {}).get("reason") if isinstance(parsed, dict) else None,
                },
            },
            "score": score,
            "message": (
                str((parsed or {}).get("reason") or f"Qwen judgement => {status}")
                if isinstance(parsed, dict)
                else f"Qwen judgement => {status}"
            ),
        }

    def _color(self, item: InspectionItem, roi_path: str) -> dict[str, Any]:
        expected_color = str(item.expected_json.get("color", "")).upper()
        minimum = float(item.rule_json.get("min_ratio", 0.15))
        maximum = float(item.rule_json.get("max_ratio", 1.0))
        ratio = color_ratio(roi_path, expected_color)
        return {
            "status": "OK" if minimum <= ratio <= maximum else "NG",
            "actual": {"color": expected_color, "ratio": ratio},
            "score": ratio,
            "message": (
                f"{expected_color} ratio {ratio:.4f}, "
                f"required {minimum:.4f}-{maximum:.4f}"
            ),
        }

    async def _ocr(self, item: InspectionItem, roi_path: str) -> dict[str, Any]:
        payload = await self.algorithms.ocr(roi_path)
        actual_text = self._extract_ocr_text(payload)
        expected_text = str(item.expected_json.get("text", ""))
        operator = str(item.rule_json.get("operator", "CONTAINS")).upper()
        case_sensitive = bool(item.rule_json.get("case_sensitive", False))
        actual = actual_text if case_sensitive else actual_text.upper()
        expected = expected_text if case_sensitive else expected_text.upper()
        if operator == "EQUALS":
            passed = actual == expected
        elif operator == "REGEX":
            passed = re.search(expected_text, actual_text) is not None
        elif operator == "STARTS_WITH":
            passed = actual.startswith(expected)
        else:
            passed = expected in actual
        return {
            "status": "OK" if passed else "NG",
            "actual": {
                "text": actual_text,
                "lines": payload.get("lines", []),
                "confidence": payload.get("confidence"),
            },
            "score": payload.get("confidence"),
            "message": f'OCR text "{actual_text}", operator {operator}',
            "primary_model": payload.get("model") or "PP-OCRv5-mobile",
        }

    @staticmethod
    def _extract_ocr_text(payload: dict[str, Any]) -> str:
        if isinstance(payload.get("text"), str):
            return payload["text"]
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            return result["text"]
        if isinstance(result, list):
            values = [
                item["text"]
                for item in result
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            return " ".join(values)
        raise ValueError("OCR service response does not contain text.")


def load_recipe_for_execution(
    database: Session,
    *,
    recipe_id: int | None = None,
    recipe_code: str | None = None,
    product_code: str | None = None,
    station_code: str | None = None,
    require_published: bool = True,
) -> Recipe | None:
    from app.models.system import Product, Station

    statement = select(Recipe).options(
        selectinload(Recipe.rois).selectinload(
            RegionOfInterest.inspection_items
        )
    )
    if recipe_id is not None:
        statement = statement.where(Recipe.id == recipe_id)
    if recipe_code:
        statement = statement.where(Recipe.code == recipe_code)
    if product_code:
        statement = statement.join(Product).where(Product.code == product_code)
    if station_code:
        statement = statement.join(Station).where(Station.code == station_code)
    if require_published:
        statement = statement.where(Recipe.status == "PUBLISHED")
    return database.scalars(statement.order_by(Recipe.id.desc())).first()
