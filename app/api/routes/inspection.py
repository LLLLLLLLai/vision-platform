import json
import shutil
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models.inspection import DetectionApiCall, DetectionTask
from app.models.recipe import Recipe
from app.models.system import Product, Station
from app.services.inspection_engine import InspectionEngine, load_recipe_for_execution


router = APIRouter()
public_router = APIRouter()
engine = InspectionEngine()


@router.get("/history")
def inspection_history(
    limit: int = 50,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    tasks = database.scalars(
        select(DetectionTask)
        .order_by(DetectionTask.id.desc())
        .limit(min(max(limit, 1), 200))
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


class ExecuteRequest(BaseModel):
    sn: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)
    request_id: str | None = None
    recipe_code: str | None = None
    product_code: str | None = None
    station_code: str | None = None


class PublicDetectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sn: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)
    line: str | None = Field(
        default=None,
        validation_alias=AliasChoices("line", "line_code"),
    )
    materialcode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("materialcode", "material_code"),
    )
    operation: str | None = Field(
        default=None,
        validation_alias=AliasChoices("operation", "process_code"),
    )
    camera: str | None = Field(
        default=None,
        validation_alias=AliasChoices("camera", "camera_code"),
    )
    picture: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices(
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
    return database.scalar(
        select(Recipe)
        .where(
            Recipe.status == "PUBLISHED",
            Recipe.line_code == line,
            Recipe.material_code == materialcode,
            Recipe.process_code == operation,
            Recipe.camera_code == camera,
            Recipe.capture_index == picture,
        )
        .order_by(Recipe.id.desc())
    )


async def execute_filename_routed_inspection(
    payload: PublicDetectRequest,
    database: Session,
) -> dict[str, Any]:
    business_fields = (
        payload.line_code,
        payload.material_code,
        payload.process_code,
    )
    image_fields = (
        payload.camera_code,
        payload.capture_index,
    )
    has_any_business_field = any(value is not None for value in business_fields)
    has_all_business_fields = all(value is not None for value in business_fields)
    has_any_image_field = any(value is not None for value in image_fields)
    has_all_image_fields = all(value is not None for value in image_fields)
    if has_any_business_field and not has_all_business_fields:
        return {
            "code": 1001,
            "message": "line, materialcode and operation must be provided together.",
            "result": "ERROR",
            "image_paths": [],
        }
    if has_any_image_field and not has_all_image_fields:
        return {
            "code": 1001,
            "message": "camera and picture must be provided together.",
            "result": "ERROR",
            "image_paths": [],
        }
    if has_any_image_field and not has_all_business_fields:
        return {
            "code": 1001,
            "message": "The five recipe parameters must include line, materialcode and operation.",
            "result": "ERROR",
            "image_paths": [],
        }

    parameter_recipe = (
        match_published_recipe_by_parameters(database, payload)
        if has_all_business_fields and has_all_image_fields
        else None
    )
    if has_all_business_fields and has_all_image_fields and parameter_recipe is None:
        return {
            "code": 2001,
            "message": "No published recipe matched structured parameters.",
            "result": "ERROR",
            "image_paths": [],
        }

    recipe_groups: dict[int, dict[str, Any]] = {}
    for image_path in payload.image_paths:
        recipe = parameter_recipe
        if recipe is None and has_all_business_fields:
            parsed = parse_camera_picture_from_filename(image_path)
            if parsed is None:
                return {
                    "code": 1001,
                    "message": (
                        "camera and picture were not provided and could not be "
                        "parsed from image filename. Expected CAMERA1PICTURE1."
                    ),
                    "result": "ERROR",
                    "image_paths": [],
                }
            camera, picture = parsed
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
            return {
                "code": 2001,
                "message": (
                    "No published recipe matched parameters or image filename: "
                    f"{image_path.replace('\\', '/').rsplit('/', 1)[-1]}"
                ),
                "result": "ERROR",
                "image_paths": [],
            }
        group = recipe_groups.setdefault(
            recipe.id,
            {"recipe": recipe, "image_paths": []},
        )
        group["image_paths"].append(image_path)

    aggregate_result = "OK"
    result_image_paths: list[str] = []
    inspection_results: list[dict[str, Any]] = []
    for group in recipe_groups.values():
        recipe = load_recipe_for_execution(
            database,
            recipe_id=group["recipe"].id,
            require_published=True,
        )
        if recipe is None:
            return {
                "code": 2001,
                "message": "Matched recipe is no longer published.",
                "result": "ERROR",
                "image_paths": [],
            }
        try:
            result = await engine.execute(
                database,
                recipe,
                sn=payload.sn,
                image_paths=group["image_paths"],
            )
        except FileNotFoundError as exc:
            return {
                "code": 1002,
                "message": str(exc),
                "result": "ERROR",
                "image_paths": result_image_paths,
            }
        except Exception as exc:
            return {
                "code": 4001,
                "message": str(exc),
                "result": "ERROR",
                "image_paths": result_image_paths,
            }

        result_image_paths.extend(result.get("image_paths") or [])
        inspection_results.append(
            {
                "request_id": result.get("request_id"),
                "recipe_code": recipe.code,
                "recipe_version": recipe.version,
                "result": result.get("result", "ERROR"),
                "elapsed_ms": result.get("elapsed_ms"),
                "image_results": result.get("image_results") or [],
            }
        )
        group_result = str(result.get("result", "ERROR")).upper()
        if group_result == "ERROR":
            aggregate_result = "ERROR"
        elif group_result == "NG" and aggregate_result != "ERROR":
            aggregate_result = "NG"

    return {
        "code": 0,
        "message": "success",
        "result": aggregate_result,
        "image_paths": result_image_paths,
        "inspection_results": inspection_results,
    }


@router.get("/call-records")
def detection_call_records(
    limit: int = 100,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    records = database.scalars(
        select(DetectionApiCall)
        .order_by(DetectionApiCall.id.desc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return [
        {
            "id": record.id,
            "caller_ip": record.caller_ip,
            "called_at": record.called_at.isoformat(),
            "sn": record.sn,
            "request_payload": record.request_payload,
            "response_payload": record.response_payload,
            "response_code": record.response_code,
            "call_status": record.call_status,
            "elapsed_ms": record.elapsed_ms,
        }
        for record in records
    ]


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
        return await engine.execute(
            database,
            recipe,
            sn=payload.sn,
            image_paths=payload.image_paths,
            request_id=payload.request_id,
        )
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
    response = {
        "code": int(internal_response.get("code", 4001)),
        "message": str(internal_response.get("message", "Internal error.")),
        "result": str(internal_response.get("result", "ERROR")),
        "image_paths": list(internal_response.get("image_paths") or []),
        "inspection_results": list(internal_response.get("inspection_results") or []),
    }
    database.add(
        DetectionApiCall(
            caller_ip=request.client.host if request.client else "unknown",
            called_at=datetime.utcnow(),
            sn=payload.sn,
            request_payload=payload.model_dump(),
            response_payload=response,
            response_code=response["code"],
            call_status="SUCCESS" if response["code"] == 0 else "FAILED",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    )
    database.commit()
    return response
