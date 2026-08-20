import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import PROJECT_ROOT
from app.db.session import get_db
from app.models.inspection import InspectionItem
from app.models.recipe import Recipe, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceImage, ReferenceObjectType
from app.models.system import Product, Station
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.image_processing import analyze_roi_color
from app.services.reference_embedding_service import save_embedding
from app.services.world_model_service import (
    sync_recipe_world_model,
    sync_roi_to_world_object,
)


router = APIRouter()
algorithm_client = AlgorithmServiceClient()
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class ProductCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class StationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    line_code: str | None = None
    process_code: str | None = None


class RecipeCreate(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    version: str = "1.0"
    product_id: int
    station_id: int
    line_code: str | None = None
    material_code: str | None = None
    process_code: str | None = None
    camera_code: str | None = None
    capture_index: int = Field(default=1, ge=1)


class RecipeUpdate(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    version: str = "1.0"
    product_id: int
    station_id: int
    line_code: str | None = None
    material_code: str | None = None
    process_code: str | None = None
    camera_code: str | None = None
    capture_index: int = Field(default=1, ge=1)


class RoiCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=100)
    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)
    width_ratio: float = Field(gt=0, le=1)
    height_ratio: float = Field(gt=0, le=1)
    padding: int = Field(default=0, ge=0, le=500)
    sort_order: int = 0


class InspectionItemCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    inspection_type: str
    capability: str
    reference_group_id: int | None = None
    expected_json: dict[str, Any] = Field(default_factory=dict)
    rule_json: dict[str, Any] = Field(default_factory=dict)
    execution_order: int = 0
    required: bool = True


class ReferenceGroupCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    object_type: str
    class_code: str
    description: str | None = None


class ReferenceObjectTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


def _commit(database: Session) -> None:
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(status_code=409, detail="Code already exists.") from exc


def _normalize_code(value: str) -> str:
    return "_".join(value.strip().upper().replace("-", "_").split())


def _validated_reference_object_type(database: Session, value: str) -> str:
    code = _normalize_code(value)
    object_type = database.scalar(
        select(ReferenceObjectType).where(
            ReferenceObjectType.code == code,
            ReferenceObjectType.enabled.is_(True),
            ReferenceObjectType.is_deleted.is_(False),
        )
    )
    if object_type is None:
        raise HTTPException(
            status_code=422,
            detail="对象类型必须选择视觉标准库中已启用的类型。",
        )
    return code


def _recipe_values(
    database: Session,
    payload: RecipeCreate | RecipeUpdate,
) -> dict[str, Any]:
    product = database.get(Product, payload.product_id)
    station = database.get(Station, payload.station_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found.")

    values = payload.model_dump()
    values["line_code"] = payload.line_code or station.line_code
    values["material_code"] = payload.material_code or product.code
    values["process_code"] = payload.process_code or station.process_code
    required_parts = (
        values["line_code"],
        values["material_code"],
        values["process_code"],
        values["camera_code"],
    )
    if not all(required_parts):
        raise HTTPException(
            status_code=400,
            detail="line, material, process and camera codes are required.",
        )
    if not values["code"]:
        values["code"] = "_".join(
            [
                _normalize_code(str(values["line_code"])),
                _normalize_code(str(values["material_code"])),
                _normalize_code(str(values["process_code"])),
                _normalize_code(str(values["camera_code"])),
                f"P{int(values['capture_index']):02d}",
            ]
        )[:100]
    return values


def _save_upload(upload: UploadFile, directory: Path) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported image type.")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid.uuid4().hex}{suffix}"
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    try:
        with Image.open(destination) as image:
            image.verify()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file.") from exc
    return destination


def _file_url(path: str | None) -> str | None:
    if not path:
        return None
    file_path = Path(path).resolve()
    uploads_root = Path(PROJECT_ROOT / "uploads").resolve()
    try:
        relative = file_path.relative_to(uploads_root)
    except ValueError:
        return None
    return "/files/" + relative.as_posix()


def _delete_upload_path(path: str | Path | None) -> None:
    if not path:
        return
    target = Path(path).resolve()
    uploads_root = Path(PROJECT_ROOT / "uploads").resolve()
    try:
        target.relative_to(uploads_root)
    except ValueError:
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)


def _item_payload(item: InspectionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "inspection_type": item.inspection_type,
        "capability": item.capability,
        "reference_group_id": item.reference_group_id,
        "expected_json": item.expected_json,
        "rule_json": item.rule_json,
        "execution_order": item.execution_order,
        "required": item.required,
        "enabled": item.enabled,
    }


def _roi_payload(roi: RegionOfInterest) -> dict[str, Any]:
    world_object = roi.scene_object
    return {
        "id": roi.id,
        "code": roi.code,
        "name": roi.name,
        "object_type": roi.object_type,
        "x_ratio": roi.x_ratio,
        "y_ratio": roi.y_ratio,
        "width_ratio": roi.width_ratio,
        "height_ratio": roi.height_ratio,
        "padding": roi.padding,
        "sort_order": roi.sort_order,
        "enabled": roi.enabled,
        "scene_object_id": roi.scene_object_id,
        "world_object": (
            {
                "id": world_object.id,
                "code": world_object.code,
                "name": world_object.name,
                "object_type": world_object.object_type,
                "location_mode": world_object.location_mode,
                "expected_state": world_object.expected_state,
                "perception_config": world_object.perception_config,
            }
            if world_object is not None
            else None
        ),
        "inspection_items": [_item_payload(item) for item in roi.inspection_items],
    }


def _latest_auto_reference(
    database: Session,
    recipe: Recipe,
    roi: RegionOfInterest,
) -> dict[str, Any] | None:
    group_code = f"{recipe.code}_{roi.code}_AUTO"[:100]
    group = database.scalar(
        select(ReferenceGroup).where(ReferenceGroup.code == group_code)
    )
    if group is None:
        return None
    reference = database.scalar(
        select(ReferenceImage)
        .where(
            ReferenceImage.group_id == group.id,
            ReferenceImage.enabled.is_(True),
            ReferenceImage.quality_status.in_(["READY", "PENDING", "PENDING_RETRY"]),
        )
        .order_by(ReferenceImage.id.desc())
    )
    if reference is None:
        return None
    return {
        "group_id": group.id,
        "class_code": group.class_code,
        "image_url": _file_url(reference.image_path),
        "embedding_status": reference.quality_status,
    }


@router.get("/products")
def list_products(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    products = database.scalars(
        select(Product).where(Product.is_deleted.is_(False)).order_by(Product.id)
    ).all()
    return [
        {"id": item.id, "code": item.code, "name": item.name, "enabled": item.enabled}
        for item in products
    ]


@router.post("/products")
def create_product(
    payload: ProductCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    product = Product(**payload.model_dump())
    database.add(product)
    _commit(database)
    database.refresh(product)
    return {"id": product.id, "code": product.code, "name": product.name}


@router.get("/stations")
def list_stations(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stations = database.scalars(
        select(Station).where(Station.is_deleted.is_(False)).order_by(Station.id)
    ).all()
    return [
        {
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "line_code": item.line_code,
            "process_code": item.process_code,
        }
        for item in stations
    ]


@router.post("/stations")
def create_station(
    payload: StationCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    station = Station(**payload.model_dump())
    database.add(station)
    _commit(database)
    database.refresh(station)
    return {"id": station.id, "code": station.code, "name": station.name}


@router.get("/recipes")
def list_recipes(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    recipes = database.scalars(
        select(Recipe)
        .options(
            selectinload(Recipe.rois).selectinload(
                RegionOfInterest.inspection_items
            )
        )
        .where(Recipe.is_deleted.is_(False))
        .order_by(Recipe.id.desc())
    ).all()
    payload = []
    for item in recipes:
        product = database.get(Product, item.product_id)
        station = database.get(Station, item.station_id)
        payload.append({
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "version": item.version,
            "status": item.status,
            "product_id": item.product_id,
            "station_id": item.station_id,
            "base_image_url": _file_url(item.base_image_path),
            "roi_count": len(item.rois),
            "rule_count": sum(len(roi.inspection_items) for roi in item.rois),
            "material_code": item.material_code or (product.code if product else ""),
            "line_code": item.line_code or (station.line_code if station else ""),
            "process_code": item.process_code or (station.process_code if station else ""),
            "station_code": station.code if station else "",
            "camera_code": item.camera_code or "",
            "capture_index": item.capture_index,
        })
    return payload


@router.post("/recipes")
def create_recipe(
    payload: RecipeCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = Recipe(**_recipe_values(database, payload))
    database.add(recipe)
    _commit(database)
    database.refresh(recipe)
    return {"id": recipe.id, "code": recipe.code, "status": recipe.status}


@router.put("/recipes/{recipe_id}")
def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    for field, value in _recipe_values(database, payload).items():
        setattr(recipe, field, value)
    database.commit()
    return {"id": recipe.id, "code": recipe.code, "status": recipe.status}


@router.get("/recipes/{recipe_id}")
def recipe_detail(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.scalar(
        select(Recipe)
        .options(
            selectinload(Recipe.rois).selectinload(
                RegionOfInterest.inspection_items
            )
        )
        .where(Recipe.id == recipe_id)
    )
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    product = database.get(Product, recipe.product_id)
    station = database.get(Station, recipe.station_id)
    return {
        "id": recipe.id,
        "code": recipe.code,
        "name": recipe.name,
        "version": recipe.version,
        "status": recipe.status,
        "product_id": recipe.product_id,
        "station_id": recipe.station_id,
        "material_code": recipe.material_code or (product.code if product else ""),
        "line_code": recipe.line_code or (station.line_code if station else ""),
        "process_code": recipe.process_code or (station.process_code if station else ""),
        "station_code": station.code if station else "",
        "camera_code": recipe.camera_code,
        "capture_index": recipe.capture_index,
        "base_image_path": recipe.base_image_path,
        "base_image_url": _file_url(recipe.base_image_path),
        "reference_width": recipe.reference_width,
        "reference_height": recipe.reference_height,
        "rois": [
            {
                **_roi_payload(roi),
                "reference": _latest_auto_reference(database, recipe, roi),
            }
            for roi in recipe.rois
        ],
    }


@router.post("/recipes/{recipe_id}/image")
def upload_recipe_image(
    recipe_id: int,
    file: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    path = _save_upload(file, Path(PROJECT_ROOT / "uploads" / "recipes" / str(recipe_id)))
    old_image_path = recipe.base_image_path
    old_rois = list(recipe.rois)
    reference_codes = [
        f"{recipe.code}_{roi.code}_AUTO"[:100]
        for roi in old_rois
    ]
    reference_groups = (
        database.scalars(
            select(ReferenceGroup).where(ReferenceGroup.code.in_(reference_codes))
        ).all()
        if reference_codes
        else []
    )
    reference_directories = [
        Path(PROJECT_ROOT / "uploads" / "references" / str(group.id))
        for group in reference_groups
    ]
    with Image.open(path) as image:
        recipe.reference_width = image.width
        recipe.reference_height = image.height
    recipe.base_image_path = str(path)
    recipe.status = "DRAFT"
    try:
        for roi in old_rois:
            database.delete(roi)
        database.flush()
        for group in reference_groups:
            database.delete(group)
        database.commit()
    except Exception:
        database.rollback()
        path.unlink(missing_ok=True)
        raise
    if old_image_path and Path(old_image_path).resolve() != path.resolve():
        _delete_upload_path(old_image_path)
    for directory in reference_directories:
        _delete_upload_path(directory)
    return {
        "image_path": str(path),
        "image_url": _file_url(str(path)),
        "width": recipe.reference_width,
        "height": recipe.reference_height,
        "cleared_roi_count": len(old_rois),
    }


@router.post("/recipes/{recipe_id}/rois")
def create_roi(
    recipe_id: int,
    payload: RoiCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    values = payload.model_dump()
    values["object_type"] = _validated_reference_object_type(
        database,
        payload.object_type,
    )
    values["pixel_coordinates"] = {
        "reference_width": recipe.reference_width,
        "reference_height": recipe.reference_height,
    }
    roi = RegionOfInterest(recipe_id=recipe_id, **values)
    database.add(roi)
    database.flush()
    sync_roi_to_world_object(database, recipe, roi)
    database.commit()
    database.refresh(roi)
    return _roi_payload(roi)


@router.put("/rois/{roi_id}")
def update_roi(
    roi_id: int,
    payload: RoiCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found.")
    values = payload.model_dump()
    values["object_type"] = _validated_reference_object_type(
        database,
        payload.object_type,
    )
    for field, value in values.items():
        setattr(roi, field, value)
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    sync_roi_to_world_object(database, recipe, roi)
    database.commit()
    database.refresh(roi)
    return _roi_payload(roi)


@router.post("/rois/{roi_id}/capture-reference")
async def capture_roi_reference(
    roi_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found.")
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None or not recipe.base_image_path:
        raise HTTPException(status_code=400, detail="Recipe image is required.")

    group_code = f"{recipe.code}_{roi.code}_AUTO"[:100]
    group = database.scalar(
        select(ReferenceGroup).where(ReferenceGroup.code == group_code)
    )
    if group is None:
        group = ReferenceGroup(
            code=group_code,
            name=f"{roi.name} 自动参考图",
            object_type=roi.object_type or "OBJECT",
            class_code=roi.code,
            description="Automatically cropped from the recipe ROI.",
        )
        database.add(group)
        database.commit()
        database.refresh(group)
    else:
        group.name = f"{roi.name} 自动参考图"
        group.object_type = roi.object_type or "OBJECT"
        group.class_code = roi.code

    existing_images = database.scalars(
        select(ReferenceImage).where(ReferenceImage.group_id == group.id)
    ).all()

    reference_root = Path(
        PROJECT_ROOT / "uploads" / "references" / str(group.id)
    )
    reference_root.mkdir(parents=True, exist_ok=True)
    reference_path = reference_root / f"{uuid.uuid4().hex}.jpg"
    with Image.open(recipe.base_image_path) as source:
        image = source.convert("RGB")
        padding = max(0, roi.padding)
        x1 = max(0, int(roi.x_ratio * image.width) - padding)
        y1 = max(0, int(roi.y_ratio * image.height) - padding)
        x2 = min(
            image.width,
            int((roi.x_ratio + roi.width_ratio) * image.width) + padding,
        )
        y2 = min(
            image.height,
            int((roi.y_ratio + roi.height_ratio) * image.height) + padding,
        )
        image.crop((x1, y1, x2, y2)).save(reference_path, quality=95)

    reference = ReferenceImage(
        group_id=group.id,
        image_path=str(reference_path),
        quality_status="PENDING",
    )
    database.add(reference)
    for existing_image in existing_images:
        existing_image.enabled = False
    database.commit()
    database.refresh(reference)
    try:
        response = await algorithm_client.embedding(str(reference_path))
        embedding_path = Path(
            PROJECT_ROOT / "embeddings" / str(group.id) / f"{reference.id}.npy"
        )
        embedding_path.parent.mkdir(parents=True, exist_ok=True)
        save_embedding(embedding_path, response["embedding"])
        reference.embedding_path = str(embedding_path)
        reference.embedding_dimension = int(response["dimension"])
        reference.quality_status = "READY"
        database.commit()
    except Exception as exc:
        reference.quality_status = "PENDING_RETRY"
        database.commit()
        return {
            "group_id": group.id,
            "group_code": group.code,
            "class_code": group.class_code,
            "image_url": _file_url(str(reference_path)),
            "embedding_status": reference.quality_status,
            "embedding_warning": str(exc),
        }
    return {
        "group_id": group.id,
        "group_code": group.code,
        "class_code": group.class_code,
        "image_url": _file_url(str(reference_path)),
        "embedding_status": reference.quality_status,
    }


@router.post("/rois/{roi_id}/analyze-color")
def analyze_color(
    roi_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found.")
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None or not recipe.base_image_path:
        raise HTTPException(status_code=400, detail="Recipe image is required.")
    try:
        return {
            "code": 0,
            "message": "success",
            **analyze_roi_color(recipe.base_image_path, roi),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rois/{roi_id}")
def delete_roi(roi_id: int, database: Session = Depends(get_db)) -> dict[str, bool]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found.")
    database.delete(roi)
    database.commit()
    return {"deleted": True}


@router.post("/rois/{roi_id}/inspection-items")
def create_inspection_item(
    roi_id: int,
    payload: InspectionItemCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    roi = database.get(RegionOfInterest, roi_id)
    if roi is None:
        raise HTTPException(status_code=404, detail="ROI not found.")
    item = InspectionItem(roi_id=roi_id, **payload.model_dump())
    database.add(item)
    database.flush()
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is not None:
        sync_roi_to_world_object(database, recipe, roi)
    database.commit()
    database.refresh(item)
    return _item_payload(item)


@router.delete("/inspection-items/{item_id}")
def delete_inspection_item(
    item_id: int,
    database: Session = Depends(get_db),
) -> dict[str, bool]:
    item = database.get(InspectionItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inspection item not found.")
    roi = database.get(RegionOfInterest, item.roi_id)
    database.delete(item)
    database.flush()
    if roi is not None:
        database.expire(roi, ["inspection_items"])
        recipe = database.get(Recipe, roi.recipe_id)
        if recipe is not None:
            sync_roi_to_world_object(database, recipe, roi)
    database.commit()
    return {"deleted": True}


@router.post("/recipes/{recipe_id}/publish")
def publish_recipe(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    if not recipe.base_image_path:
        raise HTTPException(status_code=400, detail="Base image is required.")
    if not recipe.rois:
        raise HTTPException(status_code=400, detail="At least one ROI is required.")
    if not any(roi.inspection_items for roi in recipe.rois):
        raise HTTPException(
            status_code=400,
            detail="At least one inspection rule is required.",
        )
    other_recipes = database.scalars(
        select(Recipe).where(
            Recipe.line_code == recipe.line_code,
            Recipe.material_code == recipe.material_code,
            Recipe.process_code == recipe.process_code,
            Recipe.camera_code == recipe.camera_code,
            Recipe.capture_index == recipe.capture_index,
            Recipe.status == "PUBLISHED",
            Recipe.id != recipe.id,
        )
    ).all()
    for other in other_recipes:
        other.status = "ARCHIVED"
    sync_recipe_world_model(database, recipe)
    recipe.status = "PUBLISHED"
    database.commit()
    return {"id": recipe.id, "status": recipe.status}


@router.get("/recipes/{recipe_id}/export")
def export_recipe(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    return recipe_detail(recipe_id, database)


@router.get("/reference-groups")
def list_reference_groups(
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    groups = database.scalars(
        select(ReferenceGroup)
        .options(selectinload(ReferenceGroup.images))
        .where(ReferenceGroup.is_deleted.is_(False))
        .order_by(ReferenceGroup.id.desc())
    ).all()
    return [
        {
            "id": group.id,
            "code": group.code,
            "name": group.name,
            "object_type": group.object_type,
            "class_code": group.class_code,
            "description": group.description,
            "enabled": group.enabled,
            "images": [
                {
                    "id": image.id,
                    "image_url": _file_url(image.image_path),
                    "quality_status": image.quality_status,
                    "model_code": image.model_code,
                    "created_at": image.created_at.isoformat(),
                }
                for image in group.images
                if image.enabled and not image.is_deleted
            ],
            "image_count": len(
                [
                    image
                    for image in group.images
                    if image.enabled and not image.is_deleted
                ]
            ),
        }
        for group in groups
    ]


@router.get("/reference-object-types")
def list_reference_object_types(
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    object_types = database.scalars(
        select(ReferenceObjectType)
        .where(
            ReferenceObjectType.enabled.is_(True),
            ReferenceObjectType.is_deleted.is_(False),
        )
        .order_by(ReferenceObjectType.name, ReferenceObjectType.code)
    ).all()
    return [
        {
            "id": object_type.id,
            "code": object_type.code,
            "name": object_type.name,
            "description": object_type.description,
        }
        for object_type in object_types
    ]


@router.post("/reference-object-types")
def create_reference_object_type(
    payload: ReferenceObjectTypeCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    object_type = ReferenceObjectType(
        code=_normalize_code(payload.code),
        name=payload.name.strip(),
        description=payload.description,
    )
    database.add(object_type)
    _commit(database)
    database.refresh(object_type)
    return {
        "id": object_type.id,
        "code": object_type.code,
        "name": object_type.name,
        "description": object_type.description,
    }


@router.post("/reference-groups")
def create_reference_group(
    payload: ReferenceGroupCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    values = payload.model_dump()
    values["object_type"] = _validated_reference_object_type(
        database,
        payload.object_type,
    )
    group = ReferenceGroup(**values)
    database.add(group)
    _commit(database)
    database.refresh(group)
    return {"id": group.id, "code": group.code, "name": group.name}


@router.post("/reference-groups/{group_id}/images")
async def upload_reference_image(
    group_id: int,
    file: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    group = database.get(ReferenceGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Reference group not found.")
    path = _save_upload(
        file,
        Path(PROJECT_ROOT / "uploads" / "references" / str(group_id)),
    )
    reference = ReferenceImage(
        group_id=group_id,
        image_path=str(path),
        quality_status="PENDING",
    )
    database.add(reference)
    database.commit()
    database.refresh(reference)

    try:
        response = await algorithm_client.embedding(str(path))
        embedding_path = Path(
            PROJECT_ROOT / "embeddings" / str(group_id) / f"{reference.id}.npy"
        )
        embedding_path.parent.mkdir(parents=True, exist_ok=True)
        save_embedding(embedding_path, response["embedding"])
        reference.embedding_path = str(embedding_path)
        reference.embedding_dimension = int(response["dimension"])
        reference.quality_status = "READY"
        database.commit()
        embedding_status = "READY"
    except Exception as exc:
        reference.quality_status = "FAILED"
        database.commit()
        embedding_status = f"FAILED: {exc}"

    return {
        "id": reference.id,
        "image_path": reference.image_path,
        "image_url": _file_url(reference.image_path),
        "embedding_status": embedding_status,
    }


@router.delete("/reference-images/{image_id}")
def delete_reference_image(
    image_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    reference = database.get(ReferenceImage, image_id)
    if reference is None or reference.is_deleted:
        raise HTTPException(status_code=404, detail="Standard image not found.")
    reference.enabled = False
    reference.is_deleted = True
    database.commit()
    return {
        "id": reference.id,
        "deleted": True,
        "message": "标准图片已移出当前图库，原始文件仍保留。",
    }
