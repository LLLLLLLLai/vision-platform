import asyncio
import json
import shutil
import re
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import PROJECT_ROOT, settings
from app.db.session import get_db
from app.models.inspection import (
    DetectionApiCall,
    DetectionItemResult,
    DetectionTask,
)
from app.models.intelligence import (
    InspectionScenario,
    InspectionScenarioVersion,
    ScenarioExecution,
    ScenarioExecutionReview,
)
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product, Station
from app.services.image_processing import crop_roi
from app.services.inspection_engine import InspectionEngine, load_recipe_for_execution
from app.services.smb_storage import SmbStorage, SmbStorageError


router = APIRouter()
public_router = APIRouter()
engine = InspectionEngine()


@router.get("/history")
def inspection_history(
    limit: int = 50,
    sn: str | None = None,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(DetectionTask)
    if sn and sn.strip():
        statement = statement.where(DetectionTask.sn.contains(sn.strip()))
    tasks = database.scalars(
        statement.order_by(DetectionTask.id.desc()).limit(min(max(limit, 1), 200))
    ).all()
    return [
        {
            "id": task.id,
            "request_id": task.request_id,
            "sn": task.sn,
            "recipe_id": task.recipe_id,
            "recipe_version": task.recipe_version,
            "status": task.status,
            "image_paths": task.result_image_paths,
            "elapsed_ms": task.elapsed_ms,
            "created_at": task.created_at.isoformat(),
        }
        for task in tasks
    ]


def _metrics(statuses: list[str]) -> dict[str, Any]:
    total = len(statuses)
    ok = sum(status == "OK" for status in statuses)
    ng = sum(status == "NG" for status in statuses)
    error = total - ok - ng
    return {
        "total": total,
        "ok": ok,
        "ng": ng,
        "error": error,
        "ok_rate": round(ok / total, 4) if total else 0.0,
        "ng_rate": round(ng / total, 4) if total else 0.0,
        "error_rate": round(error / total, 4) if total else 0.0,
        "confirmed_accuracy": None,
        "confirmed_count": 0,
    }


_BINARY_REVIEW_VERDICTS = {"OK", "NG"}


def _binary_review_verdict(value: str | None) -> str | None:
    """Return a review conclusion suitable as a production truth label."""

    normalized = str(value or "").upper()
    return normalized if normalized in _BINARY_REVIEW_VERDICTS else None


def _resolved_review_verdict(
    vlm_verdict: str | None,
    manual_verdict: str | None,
) -> str | None:
    """Prefer a human verdict, but do not treat UNCERTAIN as a truth label."""

    return _binary_review_verdict(manual_verdict) or _binary_review_verdict(vlm_verdict)


def _p95_elapsed_ms(values: list[float | None]) -> float | None:
    samples = sorted(float(value) for value in values if value is not None)
    if not samples:
        return None
    index = max(0, -(-len(samples) * 95 // 100) - 1)
    return round(samples[index], 2)


def _scene_health(metrics: dict[str, Any]) -> dict[str, str]:
    if int(metrics.get("false_accept_count") or 0) > 0:
        return {
            "level": "CRITICAL",
            "label": "存在漏判",
            "reason": "人工确认发现原始 OK 实为 NG。",
        }
    if int(metrics.get("error") or 0) > 0:
        return {
            "level": "WARNING",
            "label": "存在执行异常",
            "reason": "部分场景调用未得到 OK 或 NG 结果。",
        }
    if int(metrics.get("false_reject_count") or 0) > 0:
        return {
            "level": "WARNING",
            "label": "存在误判",
            "reason": "人工确认发现原始 NG 实为 OK。",
        }
    if int(metrics.get("confirmed_count") or 0) == 0:
        return {
            "level": "PENDING",
            "label": "缺少人工真值",
            "reason": "尚未有人工确认的 OK/NG 样本，无法衡量生产准确率。",
        }
    return {"level": "STABLE", "label": "稳定", "reason": "未发现已确认的漏判、误判或执行异常。"}


def _production_quality_metrics(
    values: list[tuple[str | None, str | None, str | None, float | None]],
) -> dict[str, Any]:
    raw_statuses = [str(raw or "ERROR").upper() for raw, _, _, _ in values]
    raw = _metrics(raw_statuses)
    effective_comparisons = [
        (str(raw_result or "ERROR").upper(), verdict)
        for raw_result, vlm_verdict, manual_verdict, _ in values
        if (verdict := _resolved_review_verdict(vlm_verdict, manual_verdict)) is not None
    ]
    vlm_comparisons = [
        (str(raw_result or "ERROR").upper(), verdict)
        for raw_result, vlm_verdict, _, _ in values
        if (verdict := _binary_review_verdict(vlm_verdict)) is not None
    ]
    manual_comparisons = [
        (str(raw_result or "ERROR").upper(), verdict)
        for raw_result, _, manual_verdict, _ in values
        if (verdict := _binary_review_verdict(manual_verdict)) is not None
    ]
    resolved = [verdict for _, verdict in effective_comparisons]
    elapsed_values = [elapsed for _, _, _, elapsed in values]
    total = len(values)
    metrics: dict[str, Any] = {
        **raw,
        "raw": raw,
        "primary_pass_rate": raw["ok_rate"],
        "reviewed_count": len(effective_comparisons),
        "review_coverage": round(len(effective_comparisons) / total, 4) if total else 0.0,
        "review_disagreement_count": sum(
            raw_result != review_result for raw_result, review_result in effective_comparisons
        ),
        "review_agreement_rate": (
            round(
                sum(raw_result == review_result for raw_result, review_result in effective_comparisons)
                / len(effective_comparisons),
                4,
            )
            if effective_comparisons
            else None
        ),
        "review_result": _metrics(resolved) if resolved else None,
        "vlm_reviewed_count": len(vlm_comparisons),
        "vlm_review_coverage": round(len(vlm_comparisons) / total, 4) if total else 0.0,
        "vlm_agreement_rate": (
            round(
                sum(raw_result == review_result for raw_result, review_result in vlm_comparisons)
                / len(vlm_comparisons),
                4,
            )
            if vlm_comparisons
            else None
        ),
        "human_reviewed_count": sum(manual is not None for _, _, manual, _ in values),
        "human_truth_count": len(manual_comparisons),
        "confirmed_count": len(manual_comparisons),
        "manual_confirmation_coverage": (
            round(len(manual_comparisons) / total, 4) if total else 0.0
        ),
        "confirmed_accuracy": (
            round(
                sum(raw_result == manual_result for raw_result, manual_result in manual_comparisons)
                / len(manual_comparisons),
                4,
            )
            if manual_comparisons
            else None
        ),
        "false_accept_count": sum(
            raw_result == "OK" and manual_result == "NG"
            for raw_result, manual_result in manual_comparisons
        ),
        "false_reject_count": sum(
            raw_result == "NG" and manual_result == "OK"
            for raw_result, manual_result in manual_comparisons
        ),
        "average_elapsed_ms": (
            round(sum(float(value) for value in elapsed_values if value is not None) / sum(value is not None for value in elapsed_values), 2)
            if any(value is not None for value in elapsed_values)
            else None
        ),
        "p95_elapsed_ms": _p95_elapsed_ms(elapsed_values),
    }
    metrics["review_accuracy"] = metrics["review_agreement_rate"]
    metrics["manual_accuracy"] = metrics["confirmed_accuracy"]
    metrics["health"] = _scene_health(metrics)
    return metrics


def _scene_metrics(
    rows: list[tuple[str, str, str | None, str | None, str | None, float | None]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for scene_code, scene_name, raw_result, vlm_verdict, manual_verdict, elapsed_ms in rows:
        group = grouped.setdefault(
            scene_code,
            {"name": scene_name, "values": []},
        )
        group["values"].append((raw_result, vlm_verdict, manual_verdict, elapsed_ms))
    payload: list[dict[str, Any]] = []
    for scene_code, group in sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]["values"]), item[1]["name"], item[0]),
    ):
        values = group["values"]
        metrics = _production_quality_metrics(values)
        payload.append(
            {
                "key": group["name"],
                "scene_code": scene_code,
                **metrics,
            }
        )
    return payload


@router.get("/reports")
def inspection_reports(
    days: int = 365,
    start_date: str | None = None,
    end_date: str | None = None,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    today = datetime.utcnow().date()
    try:
        selected_start = date.fromisoformat(start_date) if start_date else None
        selected_end = date.fromisoformat(end_date) if end_date else None
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="日期格式必须为 YYYY-MM-DD。",
        ) from exc
    if selected_start is None and selected_end is None:
        selected_days = min(max(days, 1), 365)
        selected_end = today
        selected_start = selected_end - timedelta(days=selected_days - 1)
    elif selected_start is None:
        selected_end = selected_end or today
        selected_start = selected_end - timedelta(days=min(max(days, 1), 365) - 1)
    elif selected_end is None:
        selected_end = today
    if selected_end < selected_start:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期。")
    if (selected_end - selected_start).days > 365:
        raise HTTPException(status_code=400, detail="统计时间范围不能超过 366 天。")
    started_at = datetime.combine(selected_start, datetime.min.time())
    ended_at = datetime.combine(selected_end + timedelta(days=1), datetime.min.time())
    scene_statement = (
        select(
            InspectionScenario.code,
            InspectionScenario.name,
            ScenarioExecution.result,
            ScenarioExecutionReview.verdict,
            ScenarioExecutionReview.manual_verdict,
            ScenarioExecution.created_at,
            ScenarioExecution.elapsed_ms,
        )
        .select_from(ScenarioExecution)
        .join(
            InspectionScenarioVersion,
            ScenarioExecution.scenario_version_id == InspectionScenarioVersion.id,
        )
        .join(
            InspectionScenario,
            InspectionScenarioVersion.scenario_id == InspectionScenario.id,
        )
        .outerjoin(
            ScenarioExecutionReview,
            ScenarioExecutionReview.execution_id == ScenarioExecution.id,
        )
        .where(
            ScenarioExecution.source == "PRODUCTION",
            ScenarioExecution.created_at >= started_at,
            ScenarioExecution.created_at < ended_at,
        )
    )
    scene_rows = database.execute(scene_statement).all()
    monthly_values: dict[str, list[tuple[str | None, str | None, str | None, float | None]]] = defaultdict(list)
    scene_values: list[tuple[str | None, str | None, str | None, float | None]] = []
    scene_dimension_rows: list[tuple[str, str, str | None, str | None, str | None, float | None]] = []
    for row in scene_rows:
        execution_month = (
            f"{row.created_at.year:04d}-{row.created_at.month:02d}"
            if row.created_at
            else f"{selected_start.year:04d}-{selected_start.month:02d}"
        )
        monthly_values[execution_month].append(
            (row.result, row.verdict, row.manual_verdict, row.elapsed_ms)
        )
        scene_values.append((row.result, row.verdict, row.manual_verdict, row.elapsed_ms))
        scene_dimension_rows.append(
            (
                row.code,
                row.name,
                row.result,
                row.verdict,
                row.manual_verdict,
                row.elapsed_ms,
            )
        )
    monthly = []
    current_year = selected_start.year
    current_month = selected_start.month
    while (current_year, current_month) <= (selected_end.year, selected_end.month):
        month_key = f"{current_year:04d}-{current_month:02d}"
        monthly.append({"date": month_key, **_production_quality_metrics(monthly_values[month_key])})
        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1

    overall = _production_quality_metrics(scene_values)
    return {
        "start_date": selected_start.isoformat(),
        "end_date": selected_end.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "overall": overall,
        "monthly": monthly,
        "dimensions": {
            "scene": _scene_metrics(scene_dimension_rows),
        },
        "accuracy_note": (
            "本报表按 ROI 关联的生产场景汇总。人工确认样本准确率只统计人工确认的 OK/NG；"
            "“暂不确定”不会作为真值。VLM 复核一致率仅表示模型结果与 VLM 结论是否一致，"
            "不能替代人工真值。漏判指原始 OK、人工确认 NG；误判指原始 NG、人工确认 OK。"
        ),
    }


class ExecuteRequest(BaseModel):
    sn: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)
    request_id: str | None = None
    recipe_code: str | None = None
    product_code: str | None = None
    station_code: str | None = None


class PublicDetectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sn: str | None = Field(
        default=None,
        alias="sn",
        max_length=200,
        validation_alias=AliasChoices("sn", "barcode", "product_barcode"),
    )
    image_paths: list[str] = Field(
        alias="image_paths",
        min_length=1,
        max_length=2,
    )
    line: str | None = Field(
        default=None,
        alias="line",
        validation_alias=AliasChoices("line", "line_code"),
    )
    materialcode: str | None = Field(
        default=None,
        alias="materialCode",
        validation_alias=AliasChoices(
            "materialCode",
            "materialcode",
            "material_code",
        ),
    )
    operation: str | None = Field(
        default=None,
        alias="operation",
        validation_alias=AliasChoices("operation", "process_code"),
    )
    camera: str | None = Field(
        default=None,
        alias="camera",
        validation_alias=AliasChoices("camera", "camera_code"),
    )
    picture: int | None = Field(
        default=None,
        alias="times",
        ge=1,
        validation_alias=AliasChoices(
            "times",
            "picture",
            "capture_index",
            "picture_index",
            "photo_index",
        ),
    )

    @property
    def line_code(self) -> str | None:
        return self.line

    @property
    def material_code(self) -> str | None:
        return self.materialcode

    @property
    def process_code(self) -> str | None:
        return self.operation

    @property
    def camera_code(self) -> str | None:
        return self.camera

    @property
    def capture_index(self) -> int | None:
        return self.picture


def normalize_filename_part(value: str) -> str:
    return re.sub(r"[\W_]+", "_", value.upper(), flags=re.UNICODE).strip("_")


def image_filename_key(image_path: str) -> str:
    filename = image_path.replace("\\", "/").rsplit("/", 1)[-1]
    return normalize_filename_part(Path(filename).stem)


def parse_camera_picture_from_filename(
    image_path: str,
) -> tuple[str, int] | None:
    filename = image_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = Path(filename).stem.upper()
    match = re.search(
        r"CAMERA[\s_-]*0*(\d+)[\s_-]*PICTURE[\s_-]*0*(\d+)",
        stem,
    )
    if match is None:
        return None
    camera_number = int(match.group(1))
    picture_number = int(match.group(2))
    if camera_number < 1 or picture_number < 1:
        return None
    return f"CAMERA{camera_number}", picture_number


def normalise_camera_code(value: str | None) -> str:
    """Map common camera aliases such as ``CAM01`` and ``CAMERA1`` together."""

    compact = normalize_filename_part(value or "")
    match = re.fullmatch(r"CAM(?:ERA)?_?0*(\d+)", compact)
    if match is not None:
        return f"CAMERA{int(match.group(1))}"
    return compact


def parse_product_barcode_from_filename(image_path: str) -> str | None:
    filename = image_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = Path(filename).stem.upper()
    match = re.search(
        r"CAMERA[\s_-]*0*\d+[\s_-]*PICTURE[\s_-]*0*\d+[\s_-]+([A-Z0-9]+)",
        stem,
    )
    if match is None:
        return None
    barcode = match.group(1).strip()
    return barcode or None


def non_empty_image_paths(image_paths: list[str]) -> list[str]:
    return [path.strip() for path in image_paths if isinstance(path, str) and path.strip()]


def safe_artifact_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unknown"


def _real_client_ip(request: Request) -> str:
    if settings.trusted_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "unknown"
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded_standard = request.headers.get("forwarded", "")
        match = re.search(r"for=\"?([^;,\"]+)", forwarded_standard, re.IGNORECASE)
        if match:
            return match.group(1).strip("[]")
    return request.client.host if request.client else "unknown"


def recipe_filename_signatures(
    recipe: Recipe,
    product: Product,
    station: Station,
) -> set[str]:
    signatures = {normalize_filename_part(recipe.code)}
    values = (
        recipe.line_code or station.line_code,
        recipe.material_code or product.code,
        recipe.process_code or station.process_code,
        recipe.camera_code,
    )
    if all(values):
        prefix = "_".join(normalize_filename_part(str(value)) for value in values)
        capture_index = recipe.capture_index
        signatures.update(
            {
                f"{prefix}_P{capture_index}",
                f"{prefix}_{capture_index}",
                f"{prefix}_PHOTO{capture_index}",
                f"{prefix}_CAPTURE{capture_index}",
            }
        )
    return {signature for signature in signatures if signature}


def filename_contains_signature(filename_key: str, signature: str) -> bool:
    padded_filename = f"_{filename_key}_"
    return f"_{signature}_" in padded_filename


def match_published_recipe_by_filename(
    database: Session,
    image_path: str,
) -> Recipe | None:
    filename_key = image_filename_key(image_path)
    rows = database.execute(
        select(Recipe, Product, Station)
        .join(Product, Recipe.product_id == Product.id)
        .join(Station, Recipe.station_id == Station.id)
        .where(Recipe.status == "PUBLISHED")
        .order_by(Recipe.id.desc())
    ).all()
    matches: list[tuple[int, int, Recipe]] = []
    for recipe, product, station in rows:
        for signature in recipe_filename_signatures(recipe, product, station):
            if filename_contains_signature(filename_key, signature):
                matches.append((len(signature), recipe.id, recipe))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1]))[2]


def match_published_recipe_by_parameters(
    database: Session,
    payload: PublicDetectRequest,
) -> Recipe | None:
    return match_published_recipe_by_values(
        database,
        line=payload.line_code,
        materialcode=payload.material_code,
        operation=payload.process_code,
        camera=payload.camera_code,
        picture=payload.capture_index,
    )


def match_published_recipe_by_values(
    database: Session,
    *,
    line: str | None,
    materialcode: str | None,
    operation: str | None,
    camera: str | None,
    picture: int | None,
) -> Recipe | None:
    values = (line, materialcode, operation, camera, picture)
    if not all(value is not None for value in values):
        return None
    candidates = database.scalars(
        select(Recipe)
        .where(
            Recipe.status == "PUBLISHED",
            Recipe.line_code == line,
            Recipe.material_code == materialcode,
            Recipe.process_code == operation,
            Recipe.capture_index == picture,
        )
        .order_by(Recipe.id.desc())
    ).all()
    exact_camera = (camera or "").strip().upper()
    exact_match = next(
        (
            recipe
            for recipe in candidates
            if recipe.camera_code.strip().upper() == exact_camera
        ),
        None,
    )
    if exact_match is not None:
        return exact_match
    requested_camera = normalise_camera_code(camera)
    return next(
        (
            recipe
            for recipe in candidates
            if normalise_camera_code(recipe.camera_code) == requested_camera
        ),
        None,
    )


async def execute_filename_routed_inspection(
    payload: PublicDetectRequest,
    database: Session,
    *,
    storage: SmbStorage | None = None,
) -> dict[str, Any]:
    active_image_paths = non_empty_image_paths(payload.image_paths)
    if not active_image_paths:
        return {
            "code": 1001,
            "message": "image_paths 中未提供有效图片路径。",
            "result": "ERROR",
            "image_paths": [],
        }
    normalized_source_paths = {
        (
            path.replace("/", "\\").casefold()
            if SmbStorage.is_remote_path(path)
            else path.replace("\\", "/")
        )
        for path in active_image_paths
    }
    if len(normalized_source_paths) != len(active_image_paths):
        return {
            "code": 1001,
            "message": "image_paths 不能包含重复的图片路径。",
            "result": "ERROR",
            "image_paths": [],
        }
    business_fields = (
        payload.line_code,
        payload.material_code,
        payload.process_code,
    )
    has_any_business_field = any(value is not None for value in business_fields)
    has_all_business_fields = all(value is not None for value in business_fields)
    if has_any_business_field and not has_all_business_fields:
        return {
            "code": 1001,
            "message": "line、materialCode 和 operation 必须同时提供。",
            "result": "ERROR",
            "image_paths": [],
        }
    if payload.camera_code is not None and payload.capture_index is None:
        return {
            "code": 1001,
            "message": "明确传入 camera 时必须同时传入 times。",
            "result": "ERROR",
            "image_paths": [],
        }
    if payload.capture_index is not None and not has_all_business_fields:
        return {
            "code": 1001,
            "message": "传入 times 时必须同时传入 line、materialCode 和 operation。",
            "result": "ERROR",
            "image_paths": [],
        }

    # Each source image is an independent execution unit.  Two cameras can
    # legitimately use the same recipe; keeping them separate lets their
    # alignment, ROI scenarios and result publishing run concurrently without
    # colliding on task IDs or artifact paths.
    image_groups: list[dict[str, Any]] = []
    pass_through_results: list[dict[str, Any]] = []
    routing_errors: list[dict[str, Any]] = []
    parsed_barcodes = {
        barcode
        for image_path in active_image_paths
        if (barcode := parse_product_barcode_from_filename(image_path)) is not None
    }
    if len(parsed_barcodes) > 1:
        return {
            "code": 1001,
            "message": "两张图片解析出的产品条码不一致。",
            "result": "ERROR",
            "image_paths": [],
        }
    resolved_sn = next(iter(parsed_barcodes), None) or (payload.sn or "").strip()
    if not resolved_sn:
        return {
            "code": 1001,
            "message": "未提供 sn，且无法从图片名称解析产品条码。",
            "result": "ERROR",
            "image_paths": [],
        }

    batch_request_id = f"detect-{uuid.uuid4().hex}"

    for source_index, image_path in enumerate(active_image_paths, start=1):
        recipe: Recipe | None = None
        parsed = parse_camera_picture_from_filename(image_path)
        if has_all_business_fields:
            if parsed and payload.camera_code and (
                normalise_camera_code(parsed[0])
                != normalise_camera_code(payload.camera_code)
            ):
                return {
                    "code": 1001,
                    "message": "传入的 camera 与图片名称中的相机编号不一致。",
                    "result": "ERROR",
                    "image_paths": [],
                }
            if parsed and payload.capture_index and parsed[1] != payload.capture_index:
                return {
                    "code": 1001,
                    "message": "传入的 times 与图片名称中的拍照次数不一致。",
                    "result": "ERROR",
                    "image_paths": [],
                }
            camera = (parsed[0] if parsed else None) or payload.camera_code
            picture = (parsed[1] if parsed else None) or payload.capture_index
            if camera is None or picture is None:
                return {
                    "code": 1001,
                    "message": "无法从图片名称解析相机和拍照次数（示例：CAMERA1PICTURE1）。",
                    "result": "ERROR",
                    "image_paths": [],
                }
            recipe = match_published_recipe_by_values(
                database,
                line=payload.line_code,
                materialcode=payload.material_code,
                operation=payload.process_code,
                camera=camera,
                picture=picture,
            )
        if recipe is None and not has_all_business_fields:
            recipe = match_published_recipe_by_filename(database, image_path)
        if recipe is None:
            routing_errors.append(
                {
                    "source_index": source_index,
                    "source_path": image_path,
                    "message": (
                        "未找到已发布工艺配方："
                        f"{image_path.replace('\\', '/').rsplit('/', 1)[-1]}"
                    ),
                }
            )
            continue
        if recipe.execution_mode == "PASS_THROUGH":
            pass_through_results.append(
                {
                    "source_index": source_index,
                    "request_id": f"{batch_request_id}-image-{source_index}",
                    "recipe_code": recipe.code,
                    "recipe_version": recipe.version,
                    "result": "SKIPPED",
                    "elapsed_ms": 0,
                    "image_results": [
                        {
                            "image_path": image_path,
                            "source_image_path": image_path,
                            "result_image_path": image_path,
                            "local_image_path": None,
                            "local_result_image_path": None,
                            "result": "SKIPPED",
                            "inspection_items": [],
                            "pass_through": True,
                            "skip_reason": (
                                recipe.skip_reason
                                or "该相机拍照已配置为无需检测，原图直接透传。"
                            ),
                        }
                    ],
                }
            )
            continue
        image_groups.append(
            {
                "group_key": f"image-{source_index}",
                "source_index": source_index,
                "recipe": recipe,
                "source_paths": [image_path],
                "local_paths": [],
            }
        )

    storage = storage or SmbStorage()
    execution_date = datetime.utcnow().strftime("%Y%m%d")
    smb_transfer_semaphore = asyncio.Semaphore(
        max(1, int(settings.smb_transfer_parallelism))
    )

    async def stage_group_images(group: dict[str, Any]) -> None:
        recipe: Recipe = group["recipe"]
        artifact_root = (
            PROJECT_ROOT
            / "detection_results"
            / execution_date
            / safe_artifact_component(recipe.code)
            / batch_request_id
            / group["group_key"]
        )
        group["artifact_root"] = artifact_root

        async def stage_one(index: int, source_path: str) -> Path:
            filename = storage.staging_filename_for_source(
                source_path,
                position=index + 1,
            )
            destination = artifact_root / "raw" / filename
            async with smb_transfer_semaphore:
                return await asyncio.to_thread(storage.stage_input, source_path, destination)

        group["local_paths"] = [
            str(path)
            for path in await asyncio.gather(
                *(
                    stage_one(index, source_path)
                    for index, source_path in enumerate(group["source_paths"])
                )
            )
        ]

    try:
        await asyncio.gather(*(stage_group_images(group) for group in image_groups))
    except SmbStorageError as exc:
        pass_through_paths = [
            item["image_results"][0]["source_image_path"]
            for item in sorted(pass_through_results, key=lambda item: item["source_index"])
        ]
        return {
            "code": 1003,
            "message": str(exc),
            "sn": resolved_sn,
            "result": "ERROR",
            "image_paths": pass_through_paths,
            "inspection_results": [
                {key: value for key, value in item.items() if key != "source_index"}
                for item in sorted(pass_through_results, key=lambda item: item["source_index"])
            ],
        }

    bind = database.get_bind()
    database_name = getattr(getattr(bind, "url", None), "database", None)
    can_parallelize_groups = not (
        bind.dialect.name == "sqlite" and database_name in {None, "", ":memory:"}
    )
    execution_session_factory = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    image_semaphore = asyncio.Semaphore(max(1, settings.production_image_parallelism))

    async def execute_group(group: dict[str, Any]) -> dict[str, Any]:
        recipe_id = int(group["recipe"].id)
        group_request_id = f"{batch_request_id}-{group['group_key']}"
        async with image_semaphore:
            if can_parallelize_groups:
                with execution_session_factory() as execution_database:
                    recipe = load_recipe_for_execution(
                        execution_database,
                        recipe_id=recipe_id,
                        require_published=True,
                    )
                    if recipe is None:
                        raise RuntimeError("匹配到的工艺配方已取消发布。")
                    result = await engine.execute(
                        execution_database,
                        recipe,
                        sn=resolved_sn,
                        image_paths=group["local_paths"],
                        request_id=group_request_id,
                        artifact_root=group["artifact_root"],
                        roi_parallelism=settings.production_roi_parallelism,
                    )
            else:
                recipe = load_recipe_for_execution(
                    database,
                    recipe_id=recipe_id,
                    require_published=True,
                )
                if recipe is None:
                    raise RuntimeError("匹配到的工艺配方已取消发布。")
                result = await engine.execute(
                    database,
                    recipe,
                    sn=resolved_sn,
                    image_paths=group["local_paths"],
                    request_id=group_request_id,
                    artifact_root=group["artifact_root"],
                    roi_parallelism=settings.production_roi_parallelism,
                )

        local_result_paths = list(result.get("image_paths") or [])

        async def publish_one(local_path: str, source_path: str) -> str:
            if not Path(local_path).is_file():
                return local_path
            async with smb_transfer_semaphore:
                return await asyncio.to_thread(storage.publish_result, local_path, source_path)

        published_paths = list(
            await asyncio.gather(
                *(
                    publish_one(local_path, source_path)
                    for local_path, source_path in zip(
                        local_result_paths,
                        group["source_paths"],
                    )
                )
            )
        )
        result["image_paths"] = published_paths
        for index, image_result in enumerate(result.get("image_results") or []):
            image_result["source_image_path"] = (
                group["source_paths"][index]
                if index < len(group["source_paths"])
                else None
            )
            image_result["local_image_path"] = (
                group["local_paths"][index]
                if index < len(group["local_paths"])
                else None
            )
            image_result["local_result_image_path"] = image_result.get("result_image_path")
            if index < len(published_paths):
                image_result["result_image_path"] = published_paths[index]
        return {
            "source_index": group["source_index"],
            "recipe_code": recipe.code,
            "result": result,
        }

    aggregate_result = "OK"
    result_paths_by_source_index: dict[int, str] = {}
    inspection_results_by_source_index: dict[int, dict[str, Any]] = {}
    for pass_through in pass_through_results:
        source_index = int(pass_through["source_index"])
        result_paths_by_source_index[source_index] = str(
            pass_through["image_results"][0]["source_image_path"]
        )
        inspection_results_by_source_index[source_index] = {
            key: value
            for key, value in pass_through.items()
            if key != "source_index"
        }
    for routing_error in routing_errors:
        source_index = int(routing_error["source_index"])
        source_path = str(routing_error["source_path"])
        result_paths_by_source_index[source_index] = source_path
        inspection_results_by_source_index[source_index] = {
            "request_id": f"{batch_request_id}-image-{source_index}",
            "recipe_code": None,
            "recipe_version": None,
            "result": "ERROR",
            "elapsed_ms": 0,
            "image_results": [
                {
                    "image_path": source_path,
                    "source_image_path": source_path,
                    "result_image_path": source_path,
                    "result": "ERROR",
                    "inspection_items": [],
                    "routing_error": routing_error["message"],
                }
            ],
        }
    group_values = image_groups
    if can_parallelize_groups:
        group_outcomes = await asyncio.gather(
            *(execute_group(group) for group in group_values),
            return_exceptions=True,
        )
    else:
        group_outcomes: list[dict[str, Any] | Exception] = []
        for group in group_values:
            try:
                group_outcomes.append(await execute_group(group))
            except Exception as exc:
                group_outcomes.append(exc)

    errors: list[str] = [str(item["message"]) for item in routing_errors]
    for group, outcome in zip(group_values, group_outcomes):
        source_index = int(group["source_index"])
        source_path = str(group["source_paths"][0])
        if isinstance(outcome, Exception):
            errors.append(str(outcome))
            result_paths_by_source_index[source_index] = source_path
            inspection_results_by_source_index[source_index] = {
                "request_id": f"{batch_request_id}-{group['group_key']}",
                "recipe_code": group["recipe"].code,
                "recipe_version": group["recipe"].version,
                "result": "ERROR",
                "elapsed_ms": None,
                "image_results": [
                    {
                        "image_path": source_path,
                        "source_image_path": source_path,
                        "result_image_path": source_path,
                        "result": "ERROR",
                        "inspection_items": [],
                        "execution_error": str(outcome),
                    }
                ],
            }
            continue
        result = outcome["result"]
        result_image_paths = list(result.get("image_paths") or [])
        result_paths_by_source_index[source_index] = str(
            result_image_paths[0] if result_image_paths else source_path
        )
        inspection_results_by_source_index[source_index] = {
            "request_id": result.get("request_id"),
            "recipe_code": outcome["recipe_code"],
            "recipe_version": result.get("recipe_version"),
            "result": result.get("result", "ERROR"),
            "elapsed_ms": result.get("elapsed_ms"),
            "image_results": result.get("image_results") or [],
        }
        group_result = str(result.get("result", "ERROR")).upper()
        if group_result == "ERROR":
            aggregate_result = "ERROR"
        elif group_result == "NG" and aggregate_result != "ERROR":
            aggregate_result = "NG"

    result_image_paths = [
        result_paths_by_source_index.get(index, image_path)
        for index, image_path in enumerate(active_image_paths, start=1)
    ]
    inspection_results = [
        inspection_results_by_source_index[index]
        for index in range(1, len(active_image_paths) + 1)
        if index in inspection_results_by_source_index
    ]

    if errors:
        return {
            "code": (
                2001
                if routing_errors
                else 1002 if any("Image does not exist" in error for error in errors) else 4001
            ),
            "message": "; ".join(errors),
            "sn": resolved_sn,
            "result": "ERROR",
            "image_paths": result_image_paths,
            "inspection_results": inspection_results,
        }
    return {
        "code": 0,
        "message": (
            "success"
            if not pass_through_results
            else f"success；{len(pass_through_results)} 张图片按无需检测配方原图透传"
        ),
        "sn": resolved_sn,
        "result": aggregate_result,
        "image_paths": result_image_paths,
        "inspection_results": inspection_results,
    }


@router.get("/call-records")
def detection_call_records(
    limit: int = 100,
    sn: str | None = None,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(DetectionApiCall)
    if sn and sn.strip():
        statement = statement.where(DetectionApiCall.sn.contains(sn.strip()))
    records = database.scalars(
        statement.order_by(DetectionApiCall.id.desc()).limit(min(max(limit, 1), 500))
    ).all()
    return [
        {
            "id": record.id,
            "caller_ip": record.caller_ip,
            "called_at": record.called_at.isoformat(),
            "sn": record.sn,
            "operation": _record_operation(record.request_payload),
            "request_payload": record.request_payload,
            "response_payload": record.response_payload,
            "response_code": record.response_code,
            "call_status": record.call_status,
            "elapsed_ms": record.elapsed_ms,
        }
        for record in records
    ]


def _record_operation(payload: dict[str, Any] | None) -> str:
    values = payload or {}
    return str(values.get("operation") or values.get("process_code") or "-")


def _local_asset_url(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    static_roots = (
        (PROJECT_ROOT / "uploads").resolve(),
        (PROJECT_ROOT / "detection_results").resolve(),
    )
    prefixes = ("/files", "/results")
    for root, prefix in zip(static_roots, prefixes):
        try:
            return f"{prefix}/{candidate.relative_to(root).as_posix()}"
        except ValueError:
            continue
    return None


def _standard_roi_crop_url(
    record_id: int,
    recipe: Recipe,
    roi: RegionOfInterest,
) -> str | None:
    if not recipe.base_image_path:
        return None
    base_image = Path(recipe.base_image_path).expanduser()
    if not base_image.is_file():
        return None
    directory = (
        PROJECT_ROOT
        / "detection_results"
        / "record_details"
        / str(record_id)
        / safe_artifact_component(recipe.code)
    )
    destination = directory / f"standard_{safe_artifact_component(roi.code)}.jpg"
    if not destination.is_file():
        crop_roi(str(base_image), roi, destination)
    return _local_asset_url(str(destination))


def _elapsed_value(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scenario_timing_payload(
    execution: ScenarioExecution | None,
    *,
    item: dict[str, Any],
    persisted: DetectionItemResult | None,
) -> dict[str, Any] | None:
    raw_output = (
        (execution.output_json if execution is not None else None)
        or item.get("actual")
        or (persisted.actual_json if persisted is not None else None)
        or {}
    )
    output = raw_output if isinstance(raw_output, dict) else {}
    raw_traces = output.get("traces")
    traces = raw_traces if isinstance(raw_traces, list) else []
    scenario_version = execution.scenario_version if execution is not None else None
    scenario = scenario_version.scenario if scenario_version is not None else None
    mode = str(
        (scenario.mode if scenario is not None else None)
        or item.get("capability")
        or ""
    ).upper()
    total_elapsed_ms = _elapsed_value(
        execution.elapsed_ms if execution is not None else item.get("elapsed_ms")
    )
    execution_id = execution.id if execution is not None else item.get("scenario_execution_id")
    if not mode and not traces and total_elapsed_ms is None:
        return None

    common = {
        "execution_id": execution_id,
        "mode": mode or "SCENARIO",
        "scenario_name": scenario.name if scenario is not None else item.get("item_name"),
        "scenario_version": scenario_version.version if scenario_version is not None else item.get("scenario_version"),
        "total_elapsed_ms": total_elapsed_ms,
    }
    if mode == "VLM_DIRECT":
        vlm_trace = next(
            (
                trace
                for trace in traces
                if isinstance(trace, dict)
                and str(trace.get("node_type") or "").upper() == "VLM"
            ),
            None,
        )
        return {
            **common,
            "display_type": "VLM",
            "vlm_elapsed_ms": _elapsed_value(
                vlm_trace.get("elapsed_ms") if vlm_trace is not None else total_elapsed_ms
            ),
        }

    nodes = [
        {
            "node_key": trace.get("node_key"),
            "node_name": trace.get("node_name") or trace.get("node_key") or "节点",
            "node_type": trace.get("node_type") or "UNKNOWN",
            "status": trace.get("status") or "UNCERTAIN",
            "elapsed_ms": _elapsed_value(trace.get("elapsed_ms")),
        }
        for trace in traces
        if isinstance(trace, dict)
    ]
    return {
        **common,
        "display_type": "WORKFLOW" if mode == "WORKFLOW" else "SCENARIO",
        "nodes": nodes,
    }


def _scenario_execution_for_record_item(
    item: dict[str, Any],
    *,
    task: DetectionTask | None,
    execution_by_id: dict[int, ScenarioExecution],
    executions_by_task_roi: dict[tuple[int, int], list[ScenarioExecution]],
) -> ScenarioExecution | None:
    try:
        execution_id = int(item.get("scenario_execution_id"))
    except (TypeError, ValueError):
        execution_id = None
    if execution_id is not None:
        return execution_by_id.get(execution_id)
    try:
        roi_id = int(item.get("roi_id"))
    except (TypeError, ValueError):
        roi_id = None
    if task is None or roi_id is None:
        return None
    candidates = executions_by_task_roi.get((task.id, roi_id), [])
    return candidates[0] if len(candidates) == 1 else None


def _manual_review_payload(execution: ScenarioExecution | None) -> dict[str, Any] | None:
    """Expose the human-review state alongside one production ROI execution."""

    if execution is None:
        return None
    review = execution.review
    return {
        "execution_id": execution.id,
        "primary_result": execution.result,
        "review_status": review.status if review is not None else None,
        "vlm_verdict": review.verdict if review is not None else None,
        "manual_verdict": review.manual_verdict if review is not None else None,
        "manual_note": review.manual_note if review is not None else None,
        "manually_reviewed_at": (
            review.manually_reviewed_at.isoformat()
            if review is not None and review.manually_reviewed_at
            else None
        ),
    }


@router.get("/call-records/{record_id}")
def detection_call_record_detail(
    record_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    record = database.get(DetectionApiCall, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到检测调用记录。")
    response_payload = record.response_payload or {}
    inspection_results = list(response_payload.get("inspection_results") or [])
    request_ids = [
        str(result.get("request_id"))
        for result in inspection_results
        if result.get("request_id")
    ]
    tasks = database.scalars(
        select(DetectionTask)
        .options(selectinload(DetectionTask.item_results))
        .where(DetectionTask.request_id.in_(request_ids))
    ).all() if request_ids else []
    task_by_request = {task.request_id: task for task in tasks}
    task_ids = [task.id for task in tasks]
    scenario_executions = database.scalars(
        select(ScenarioExecution)
        .options(
            selectinload(ScenarioExecution.scenario_version).selectinload(
                InspectionScenarioVersion.scenario
            ),
            selectinload(ScenarioExecution.review),
        )
        .where(
            ScenarioExecution.detection_task_id.in_(task_ids),
            ScenarioExecution.source == "PRODUCTION",
        )
    ).all() if task_ids else []
    execution_by_id = {execution.id: execution for execution in scenario_executions}
    executions_by_task_roi: dict[tuple[int, int], list[ScenarioExecution]] = defaultdict(list)
    for execution in scenario_executions:
        if execution.detection_task_id is not None and execution.roi_id is not None:
            executions_by_task_roi[(execution.detection_task_id, execution.roi_id)].append(execution)

    recipes: list[dict[str, Any]] = []
    for inspection in inspection_results:
        recipe = load_recipe_for_execution(
            database,
            recipe_code=str(inspection.get("recipe_code") or ""),
            require_published=False,
        )
        if recipe is None:
            continue
        task = task_by_request.get(str(inspection.get("request_id") or ""))
        roi_by_code = {roi.code: roi for roi in recipe.rois}
        persisted_results = {
            item.roi_id: item
            for item in (task.item_results if task else [])
        }
        image_blocks: list[dict[str, Any]] = []
        for image_result in inspection.get("image_results") or []:
            roi_blocks: list[dict[str, Any]] = []
            for item in image_result.get("inspection_items") or []:
                roi_code = str(item.get("roi_code") or "")
                roi = roi_by_code.get(roi_code)
                persisted = persisted_results.get(roi.id) if roi is not None else None
                execution = _scenario_execution_for_record_item(
                    item,
                    task=task,
                    execution_by_id=execution_by_id,
                    executions_by_task_roi=executions_by_task_roi,
                )
                roi_blocks.append(
                    {
                        "roi_code": roi_code,
                        "roi_name": item.get("roi_name") or (roi.name if roi else roi_code),
                        "status": item.get("status") or (persisted.status if persisted else "ERROR"),
                        "scene_name": item.get("item_name") or item.get("scene_type"),
                        "score": item.get("score") if item.get("score") is not None else (persisted.score if persisted else None),
                        "message": item.get("message") or (persisted.message if persisted else None),
                        "processing": item.get("actual") or (persisted.actual_json if persisted else {}),
                        "scenario_timing": _scenario_timing_payload(
                            execution,
                            item=item,
                            persisted=persisted,
                        ),
                        "manual_review": _manual_review_payload(execution),
                        "standard_roi_image_url": _standard_roi_crop_url(record.id, recipe, roi) if roi else None,
                        "actual_roi_image_url": item.get("roi_image_url") or _local_asset_url(persisted.roi_image_path if persisted else None),
                    }
                )
            image_blocks.append(
                {
                    "source_image_path": image_result.get("source_image_path") or image_result.get("image_path"),
                    "actual_image_url": _local_asset_url(
                        image_result.get("local_image_path") or image_result.get("image_path")
                    ),
                    "result_image_path": image_result.get("result_image_path"),
                    "result_image_url": _local_asset_url(
                        image_result.get("local_result_image_path")
                    ),
                    "status": image_result.get("result"),
                    "pass_through": bool(image_result.get("pass_through")),
                    "skip_reason": image_result.get("skip_reason"),
                    "alignment": image_result.get("alignment") or {},
                    "rois": roi_blocks,
                }
            )
        recipes.append(
            {
                "recipe_code": recipe.code,
                "recipe_name": recipe.name,
                "recipe_version": inspection.get("recipe_version") or recipe.version,
                "execution_mode": recipe.execution_mode,
                "skip_reason": recipe.skip_reason,
                "result": inspection.get("result"),
                "elapsed_ms": inspection.get("elapsed_ms"),
                "standard_image_url": _local_asset_url(recipe.base_image_path),
                "images": image_blocks,
            }
        )
    return {
        "id": record.id,
        "caller_ip": record.caller_ip,
        "called_at": record.called_at.isoformat(),
        "sn": record.sn,
        "operation": _record_operation(record.request_payload),
        "request_payload": record.request_payload,
        "response_payload": response_payload,
        "recipes": recipes,
    }


@router.post("/test")
async def test_recipe(
    recipe_id: int = Form(...),
    roi_id: int | None = Form(None),
    draft_rules: str | None = Form(None),
    review_config: str | None = Form(None),
    file: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = load_recipe_for_execution(
        database,
        recipe_id=recipe_id,
        require_published=False,
    )
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    suffix = Path(file.filename or "test.jpg").suffix.lower() or ".jpg"
    destination = Path(
        PROJECT_ROOT / "uploads" / "tests" / f"{uuid.uuid4().hex}{suffix}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        if draft_rules is not None:
            if roi_id is None:
                raise HTTPException(status_code=400, detail="roi_id is required.")
            roi = next((item for item in recipe.rois if item.id == roi_id), None)
            if roi is None:
                raise HTTPException(status_code=404, detail="ROI not found.")
            rules = json.loads(draft_rules)
            review = json.loads(review_config or "{}")
            if not isinstance(rules, list) or not rules:
                raise HTTPException(status_code=400, detail="Draft rules are required.")
            if not isinstance(review, dict):
                raise HTTPException(status_code=400, detail="Invalid review config.")
            return await engine.test_draft_roi(
                database,
                recipe,
                roi,
                str(destination),
                rules,
                review,
            )
        return await engine.execute(
            database,
            recipe,
            sn="TEST",
            image_paths=[str(destination)],
            force_vlm_review=True,
            roi_ids={roi_id} if roi_id is not None else None,
        )
    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid draft test JSON.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/execute")
async def execute_inspection(
    payload: ExecuteRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = load_recipe_for_execution(
        database,
        recipe_code=payload.recipe_code,
        product_code=payload.product_code,
        station_code=payload.station_code,
        require_published=True,
    )
    if recipe is None:
        return {
            "code": 2001,
            "message": "No published recipe matched the request.",
            "result": "ERROR",
            "image_paths": [],
        }
    try:
        response = await engine.execute(
            database,
            recipe,
            sn=payload.sn,
            image_paths=payload.image_paths,
            request_id=payload.request_id,
        )
        return response
    except FileNotFoundError as exc:
        return {
            "code": 1002,
            "message": str(exc),
            "result": "ERROR",
            "image_paths": [],
        }
    except Exception as exc:
        return {
            "code": 4001,
            "message": str(exc),
            "result": "ERROR",
            "image_paths": [],
        }


@public_router.post("/detect")
async def public_detect(
    payload: PublicDetectRequest,
    request: Request,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    started = time.perf_counter()
    internal_response = await execute_filename_routed_inspection(payload, database)
    resolved_sn = str(internal_response.get("sn") or payload.sn or "UNKNOWN").strip()
    internal_code = int(internal_response.get("code", 4001))
    response = {
        "code": (
            settings.public_detect_success_code
            if internal_code == 0
            else internal_code
        ),
        "message": str(internal_response.get("message", "Internal error.")),
        "result": str(internal_response.get("result", "ERROR")),
        "image_paths": list(internal_response.get("image_paths") or []),
        "inspection_results": list(internal_response.get("inspection_results") or []),
    }
    request_payload = payload.model_dump(by_alias=True)
    request_payload["sn"] = resolved_sn
    database.add(
        DetectionApiCall(
            caller_ip=_real_client_ip(request),
            called_at=datetime.utcnow(),
            sn=resolved_sn,
            request_payload=request_payload,
            response_payload=response,
            response_code=response["code"],
            call_status="SUCCESS" if internal_code == 0 else "FAILED",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    )
    database.commit()
    return response
