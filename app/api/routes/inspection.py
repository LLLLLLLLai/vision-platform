import shutil
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
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
    sn: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)


def normalize_filename_part(value: str) -> str:
    return re.sub(r"[\W_]+", "_", value.upper(), flags=re.UNICODE).strip("_")


def image_filename_key(image_path: str) -> str:
    filename = image_path.replace("\\", "/").rsplit("/", 1)[-1]
    return normalize_filename_part(Path(filename).stem)


def recipe_filename_signatures(
    recipe: Recipe,
    product: Product,
    station: Station,
) -> set[str]:
    signatures = {normalize_filename_part(recipe.code)}
    values = (
        station.line_code,
        product.code,
        station.process_code,
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


async def execute_filename_routed_inspection(
    payload: PublicDetectRequest,
    database: Session,
) -> dict[str, Any]:
    recipe_groups: dict[int, dict[str, Any]] = {}
    for image_path in payload.image_paths:
        recipe = match_published_recipe_by_filename(database, image_path)
        if recipe is None:
            return {
                "code": 2001,
                "message": (
                    "No published recipe matched image filename: "
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
        return await engine.execute(
            database,
            recipe,
            sn="TEST",
            image_paths=[str(destination)],
        )
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
