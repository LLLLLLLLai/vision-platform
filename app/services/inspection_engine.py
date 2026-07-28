import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT, settings
from app.models.inspection import DetectionItemResult, DetectionTask, InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceGroup
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.image_processing import annotate_image, color_ratio, crop_roi


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
                    roi_file = task_root / f"image_{image_index}" / f"{roi.code}.jpg"
                    _, box = crop_roi(str(image_file), roi, roi_file)
                    roi_status = "OK"

                    for item in sorted(
                        roi.inspection_items,
                        key=lambda value: value.execution_order,
                    ):
                        if not item.enabled:
                            continue
                        result = await self._execute_item(database, item, str(roi_file))
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
                                "item_code": item.code,
                                "item_name": item.name,
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
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            if item.capability.upper() == "REFERENCE_SIMILARITY":
                result = await self._reference_similarity(database, item, roi_path)
            elif item.capability.upper() == "COLOR_RATIO":
                result = self._color(item, roi_path)
            elif item.capability.upper() == "OCR_TEXT":
                result = await self._ocr(item, roi_path)
            else:
                raise ValueError(f"Unsupported capability: {item.capability}")
        except Exception as exc:
            result = {
                "status": "ERROR",
                "actual": {},
                "score": None,
                "message": str(exc),
            }
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result

    async def _reference_similarity(
        self,
        database: Session,
        item: InspectionItem,
        roi_path: str,
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
        reference_paths = [
            image.image_path
            for image in group.images
            if image.enabled and not image.is_deleted
        ]
        if not reference_paths:
            raise ValueError("Reference group has no enabled images.")
        response = await self.algorithms.similarity(
            roi_path,
            reference_paths,
            top_k=min(3, len(reference_paths)),
        )
        score = float(response["top1_similarity"])
        minimum = float(item.rule_json.get("min_similarity", 0.85))
        return {
            "status": "OK" if score >= minimum else "NG",
            "actual": {
                "matched_class": group.class_code,
                "matched_reference": response["candidates"][0]["reference_path"],
                "similarity": score,
            },
            "score": score,
            "message": f"Similarity {score:.4f}, required >= {minimum:.4f}",
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
        async with httpx.AsyncClient(
            timeout=settings.algorithm_timeout_seconds
        ) as client:
            response = await client.post(
                f"{settings.paddleocr_service_url.rstrip('/')}/ocr",
                json={"image_path": roi_path},
            )
            response.raise_for_status()
            payload = response.json()
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
            "actual": {"text": actual_text},
            "score": None,
            "message": f'OCR text "{actual_text}", operator {operator}',
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

