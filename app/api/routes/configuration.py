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
from app.models.intelligence import InspectionScenarioVersion, RoiScenarioBinding
from app.models.recipe import Recipe, RecipeFeatureAnchor, RegionOfInterest
from app.models.reference import ReferenceGroup, ReferenceImage, ReferenceObjectType
from app.models.system import Product, Station
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.image_processing import analyze_roi_color
from app.services.reference_embedding_service import write_reference_matrix
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
    project_name: str | None = Field(default=None, max_length=200)
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
    project_name: str | None = Field(default=None, max_length=200)
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
    alignment_anchor: bool = False


class FeatureAnchorCreate(BaseModel):
    code: str = Field(default="FEATURE_ANCHOR", min_length=1, max_length=100)
    name: str = Field(default="图像定位特征点", min_length=1, max_length=200)
    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)
    width_ratio: float = Field(gt=0, le=1)
    height_ratio: float = Field(gt=0, le=1)
    padding: int = Field(default=0, ge=0, le=500)
    enabled: bool = True


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
    # Version numbers are system-managed.  A saved draft deliberately keeps
    # the currently published number until it is explicitly published.
    values.pop("version", None)
    values["project_name"] = (values.get("project_name") or "").strip() or None
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


def _ensure_recipe_editable(recipe: Recipe) -> None:
    if recipe.is_deleted:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    if recipe.status not in {"DRAFT", "SAVED"}:
        raise HTTPException(
            status_code=409,
            detail="已发布或已归档配方不能直接修改，请先创建新的编辑草稿。",
        )


def _mark_recipe_changed(recipe: Recipe) -> None:
    if recipe.status == "SAVED":
        recipe.status = "DRAFT"


def _recipe_family_code(recipe: Recipe) -> str:
    """Return the stable family key for both new and legacy recipe rows."""

    return (recipe.recipe_family_code or recipe.code or "").strip()


def _normalize_recipe_family(recipe: Recipe) -> str:
    """Fill a legacy row's family key before it joins a new draft/version.

    Fresh installations receive this value at creation time and existing
    SQLite installations receive it through ``init_database``.  Keeping this
    tiny safeguard here also makes direct API use safe while an upgrade is in
    progress and avoids treating an empty family key as a shared family.
    """

    family_code = _recipe_family_code(recipe)
    if family_code and recipe.recipe_family_code != family_code:
        recipe.recipe_family_code = family_code
    return family_code


def _recipe_version_metadata(database: Session, recipe: Recipe) -> dict[str, Any]:
    """Expose draft/published state without treating the draft as production.

    Existing installations may contain published rows created before
    ``version_no`` was introduced.  Those rows are considered version one for
    display and for calculating the next published revision.
    """

    family_code = _recipe_family_code(recipe)
    family_filter = (
        Recipe.recipe_family_code == family_code
        if recipe.recipe_family_code
        else Recipe.code == family_code
    )
    family_rows = database.scalars(
        select(Recipe)
        .where(
            family_filter,
            Recipe.is_deleted.is_(False),
        )
        .order_by(Recipe.id.desc())
    ).all()
    if not family_rows and family_code:
        # Legacy rows in a unit test or a database that has not yet completed
        # the SQLite migration can still be addressed by their business code.
        family_rows = [recipe]
    version_numbers = [
        item.version_no or (1 if item.status == "PUBLISHED" else 0)
        for item in family_rows
    ]
    next_version_no = max(version_numbers, default=0) + 1
    production = next(
        (item for item in family_rows if item.status == "PUBLISHED"),
        None,
    )
    draft = next(
        (item for item in family_rows if item.status in {"DRAFT", "SAVED"}),
        None,
    )
    current_version_no = recipe.version_no or (
        1 if recipe.status == "PUBLISHED" else 0
    )
    if recipe.status == "PUBLISHED":
        display_version = f"V{current_version_no}（生产）"
    elif recipe.status in {"DRAFT", "SAVED"}:
        display_version = f"草稿（待发布 V{next_version_no}）"
    else:
        display_version = f"V{current_version_no or '-'}（历史）"
    return {
        "recipe_family_code": family_code,
        "version_no": current_version_no,
        "display_version": display_version,
        "next_version_no": next_version_no,
        "source_recipe_id": recipe.source_recipe_id,
        "production_recipe_id": production.id if production else None,
        "draft_recipe_id": draft.id if draft else None,
        "history_count": len(family_rows),
        "is_production_version": recipe.status == "PUBLISHED",
    }


def _load_recipe_with_content(database: Session, recipe_id: int) -> Recipe:
    recipe = database.scalar(
        select(Recipe)
        .options(
            selectinload(Recipe.feature_anchor),
            selectinload(Recipe.rois).selectinload(
                RegionOfInterest.inspection_items
            ),
        )
        .where(Recipe.id == recipe_id, Recipe.is_deleted.is_(False))
    )
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return recipe


def _find_recipe_draft(database: Session, family_code: str) -> Recipe | None:
    return database.scalar(
        select(Recipe)
        .where(
            Recipe.recipe_family_code == family_code,
            Recipe.status.in_(("DRAFT", "SAVED")),
            Recipe.is_deleted.is_(False),
        )
        .order_by(Recipe.updated_at.desc(), Recipe.id.desc())
    )


def _clone_recipe_content(
    database: Session,
    source: Recipe,
    *,
    code: str,
    name: str,
    recipe_family_code: str,
) -> Recipe:
    """Create an isolated editable copy, including ROI-scene bindings.

    Published rows are immutable.  Copying the base image and the complete
    ROI tree means changing a draft can never alter a historical production
    version or the evidence attached to older detection records.
    """

    clone = Recipe(
        code=code,
        recipe_family_code=recipe_family_code,
        version_no=0,
        source_recipe_id=source.id,
        name=name,
        version=source.version,
        status="DRAFT",
        project_name=source.project_name,
        product_id=source.product_id,
        station_id=source.station_id,
        line_code=source.line_code,
        material_code=source.material_code,
        process_code=source.process_code,
        camera_code=source.camera_code,
        capture_index=source.capture_index,
        reference_width=source.reference_width,
        reference_height=source.reference_height,
    )
    database.add(clone)
    database.flush()

    copied_image_path: Path | None = None
    try:
        if source.base_image_path:
            source_path = Path(source.base_image_path)
            if source_path.is_file():
                copied_image_path = (
                    Path(PROJECT_ROOT / "uploads" / "recipes" / str(clone.id))
                    / f"base{source_path.suffix.lower() or '.jpg'}"
                )
                copied_image_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, copied_image_path)
                clone.base_image_path = str(copied_image_path)

        roi_id_map: dict[int, int] = {}
        for source_roi in source.rois:
            cloned_roi = RegionOfInterest(
                recipe_id=clone.id,
                scene_object_id=None,
                code=source_roi.code,
                name=source_roi.name,
                object_type=source_roi.object_type,
                shape_type=source_roi.shape_type,
                x_ratio=source_roi.x_ratio,
                y_ratio=source_roi.y_ratio,
                width_ratio=source_roi.width_ratio,
                height_ratio=source_roi.height_ratio,
                pixel_coordinates=dict(source_roi.pixel_coordinates or {}),
                padding=source_roi.padding,
                sort_order=source_roi.sort_order,
                alignment_anchor=False,
                enabled=source_roi.enabled,
            )
            database.add(cloned_roi)
            database.flush()
            roi_id_map[source_roi.id] = cloned_roi.id
            for source_item in source_roi.inspection_items:
                database.add(
                    InspectionItem(
                        roi_id=cloned_roi.id,
                        code=source_item.code,
                        name=source_item.name,
                        inspection_type=source_item.inspection_type,
                        capability=source_item.capability,
                        reference_group_id=source_item.reference_group_id,
                        expected_json=dict(source_item.expected_json or {}),
                        rule_json=dict(source_item.rule_json or {}),
                        execution_order=source_item.execution_order,
                        required=source_item.required,
                        enabled=source_item.enabled,
                    )
                )

        source_roi_ids = list(roi_id_map)
        bindings = database.scalars(
            select(RoiScenarioBinding).where(
                RoiScenarioBinding.roi_id.in_(source_roi_ids)
            )
        ).all() if source_roi_ids else []
        for binding in bindings:
            database.add(
                RoiScenarioBinding(
                    roi_id=roi_id_map[binding.roi_id],
                    scenario_version_id=binding.scenario_version_id,
                    input_mapping_json=dict(binding.input_mapping_json or {}),
                    enabled=binding.enabled,
                )
            )

        if source.feature_anchor is not None:
            anchor = source.feature_anchor
            database.add(
                RecipeFeatureAnchor(
                    recipe_id=clone.id,
                    code=anchor.code,
                    name=anchor.name,
                    x_ratio=anchor.x_ratio,
                    y_ratio=anchor.y_ratio,
                    width_ratio=anchor.width_ratio,
                    height_ratio=anchor.height_ratio,
                    padding=anchor.padding,
                    enabled=anchor.enabled,
                )
            )
        database.flush()
    except Exception:
        if copied_image_path is not None:
            copied_image_path.unlink(missing_ok=True)
        raise
    return clone


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
        "alignment_anchor": roi.alignment_anchor,
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


def _feature_anchor_payload(anchor: RecipeFeatureAnchor | None) -> dict[str, Any] | None:
    if anchor is None:
        return None
    return {
        "id": anchor.id,
        "code": anchor.code,
        "name": anchor.name,
        "x_ratio": anchor.x_ratio,
        "y_ratio": anchor.y_ratio,
        "width_ratio": anchor.width_ratio,
        "height_ratio": anchor.height_ratio,
        "padding": anchor.padding,
        "enabled": anchor.enabled,
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
            ReferenceImage.is_deleted.is_(False),
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
        "detection_ready": bool(
            reference.enabled and reference.quality_status == "READY"
        ),
    }


def _reference_group_context(
    database: Session,
    group_id: int,
) -> tuple[Recipe | None, RegionOfInterest | None]:
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
            selectinload(Recipe.feature_anchor),
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
            "project_name": item.project_name,
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
            **_recipe_version_metadata(database, item),
        })
    return payload


@router.post("/recipes")
def create_recipe(
    payload: RecipeCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    values = _recipe_values(database, payload)
    recipe = Recipe(
        **values,
        recipe_family_code=values["code"],
        version_no=0,
        version="",
        status="DRAFT",
    )
    database.add(recipe)
    _commit(database)
    database.refresh(recipe)
    return {
        "id": recipe.id,
        "code": recipe.code,
        "status": recipe.status,
        **_recipe_version_metadata(database, recipe),
    }


@router.put("/recipes/{recipe_id}")
def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    for field, value in _recipe_values(database, payload).items():
        setattr(recipe, field, value)
    _mark_recipe_changed(recipe)
    database.commit()
    return {"id": recipe.id, "code": recipe.code, "status": recipe.status}


@router.delete("/recipes/{recipe_id}")
def delete_recipe(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, bool]:
    """Soft-delete a recipe without removing its historical inspection trace."""

    recipe = database.get(Recipe, recipe_id)
    if recipe is None or recipe.is_deleted:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    was_published = recipe.status == "PUBLISHED"
    recipe.is_deleted = True
    recipe.status = "ARCHIVED"
    database.commit()
    return {"deleted": True, "was_published": was_published}


@router.get("/recipes/{recipe_id}")
def recipe_detail(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.scalar(
        select(Recipe)
        .options(
            selectinload(Recipe.feature_anchor),
            selectinload(Recipe.rois).selectinload(
                RegionOfInterest.inspection_items
            )
        )
        .where(Recipe.id == recipe_id, Recipe.is_deleted.is_(False))
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
        "project_name": recipe.project_name,
        "product_id": recipe.product_id,
        "station_id": recipe.station_id,
        "material_code": recipe.material_code or (product.code if product else ""),
        "line_code": recipe.line_code or (station.line_code if station else ""),
        "process_code": recipe.process_code or (station.process_code if station else ""),
        "station_code": station.code if station else "",
        "camera_code": recipe.camera_code,
        "capture_index": recipe.capture_index,
        **_recipe_version_metadata(database, recipe),
        "base_image_path": recipe.base_image_path,
        "base_image_url": _file_url(recipe.base_image_path),
        "reference_width": recipe.reference_width,
        "reference_height": recipe.reference_height,
        "feature_anchor": _feature_anchor_payload(recipe.feature_anchor),
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
    _ensure_recipe_editable(recipe)
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
    if recipe.feature_anchor is not None:
        database.delete(recipe.feature_anchor)
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
        "cleared_feature_anchor": True,
    }


@router.put("/recipes/{recipe_id}/feature-anchor")
def save_recipe_feature_anchor(
    recipe_id: int,
    payload: FeatureAnchorCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    if recipe.base_image_path is None:
        raise HTTPException(status_code=400, detail="请先上传配方基准图片。")
    anchor = database.scalar(
        select(RecipeFeatureAnchor).where(RecipeFeatureAnchor.recipe_id == recipe.id)
    )
    values = payload.model_dump()
    if anchor is None:
        anchor = RecipeFeatureAnchor(recipe_id=recipe.id, **values)
        database.add(anchor)
    else:
        for field, value in values.items():
            setattr(anchor, field, value)
    _mark_recipe_changed(recipe)
    database.commit()
    database.refresh(anchor)
    return _feature_anchor_payload(anchor) or {}


@router.delete("/recipes/{recipe_id}/feature-anchor")
def delete_recipe_feature_anchor(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, bool]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    anchor = database.scalar(
        select(RecipeFeatureAnchor).where(RecipeFeatureAnchor.recipe_id == recipe.id)
    )
    if anchor is not None:
        database.delete(anchor)
        _mark_recipe_changed(recipe)
        database.commit()
    return {"deleted": anchor is not None}


@router.post("/recipes/{recipe_id}/rois")
def create_roi(
    recipe_id: int,
    payload: RoiCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    values = payload.model_dump()
    values["object_type"] = _validated_reference_object_type(
        database,
        payload.object_type,
    )
    values["pixel_coordinates"] = {
        "reference_width": recipe.reference_width,
        "reference_height": recipe.reference_height,
    }
    if values.get("alignment_anchor"):
        database.query(RegionOfInterest).filter(
            RegionOfInterest.recipe_id == recipe_id,
            RegionOfInterest.alignment_anchor.is_(True),
        ).update({RegionOfInterest.alignment_anchor: False})
    roi = RegionOfInterest(recipe_id=recipe_id, **values)
    database.add(roi)
    database.flush()
    sync_roi_to_world_object(database, recipe, roi)
    _mark_recipe_changed(recipe)
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
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    values = payload.model_dump()
    values["object_type"] = _validated_reference_object_type(
        database,
        payload.object_type,
    )
    if values.get("alignment_anchor"):
        database.query(RegionOfInterest).filter(
            RegionOfInterest.recipe_id == roi.recipe_id,
            RegionOfInterest.id != roi.id,
            RegionOfInterest.alignment_anchor.is_(True),
        ).update({RegionOfInterest.alignment_anchor: False})
    for field, value in values.items():
        setattr(roi, field, value)
    sync_roi_to_world_object(database, recipe, roi)
    _mark_recipe_changed(recipe)
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
    database.commit()
    database.refresh(reference)
    try:
        response = await algorithm_client.embedding(str(reference_path))
        matrix = write_reference_matrix(
            group,
            [reference],
            {reference.id: response["embedding"]},
            recipe=recipe,
            roi=roi,
        )
        for existing_image in existing_images:
            existing_image.enabled = False
        for item in database.scalars(
            select(InspectionItem).where(
                InspectionItem.roi_id == roi.id,
                InspectionItem.inspection_type == "EXISTENCE",
            )
        ).all():
            item.reference_group_id = group.id
            expected = dict(item.expected_json or {})
            expected.update(
                {
                    "exists": True,
                    "class_code": group.class_code,
                    "reference_image_url": _file_url(str(reference_path)),
                }
            )
            item.expected_json = expected
        database.commit()
    except Exception as exc:
        reference.quality_status = "PENDING_RETRY"
        reference.enabled = False
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
        "embedding_set_version": matrix["version"],
        "embedding_matrix_path": matrix["matrix_path"],
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
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    database.delete(roi)
    _mark_recipe_changed(recipe)
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
    recipe = database.get(Recipe, roi.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    item = InspectionItem(roi_id=roi_id, **payload.model_dump())
    database.add(item)
    database.flush()
    sync_roi_to_world_object(database, recipe, roi)
    _mark_recipe_changed(recipe)
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
    recipe = database.get(Recipe, roi.recipe_id) if roi is not None else None
    if recipe is not None:
        _ensure_recipe_editable(recipe)
    database.delete(item)
    database.flush()
    if roi is not None:
        database.expire(roi, ["inspection_items"])
        if recipe is not None:
            sync_roi_to_world_object(database, recipe, roi)
            _mark_recipe_changed(recipe)
    database.commit()
    return {"deleted": True}


@router.post("/recipes/{recipe_id}/save")
def save_recipe_draft(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Persist an editable recipe without making it available to ``/detect``."""

    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    recipe.status = "SAVED"
    database.commit()
    return {"id": recipe.id, "code": recipe.code, "status": recipe.status}


@router.post("/recipes/{recipe_id}/copy")
def copy_recipe(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Clone a recipe into an independent draft before operators edit it."""

    source = _load_recipe_with_content(database, recipe_id)

    clone_code = f"{source.code}_COPY_{uuid.uuid4().hex[:6].upper()}"[:100]
    clone = Recipe(
        code=clone_code,
        recipe_family_code=clone_code,
        version_no=0,
        source_recipe_id=source.id,
        name=f"{source.name}（副本）"[:200],
        version=source.version,
        status="DRAFT",
        project_name=source.project_name,
        product_id=source.product_id,
        station_id=source.station_id,
        line_code=source.line_code,
        material_code=source.material_code,
        process_code=source.process_code,
        camera_code=source.camera_code,
        capture_index=source.capture_index,
        reference_width=source.reference_width,
        reference_height=source.reference_height,
    )
    database.add(clone)
    database.flush()

    copied_image_path: Path | None = None
    try:
        if source.base_image_path:
            source_path = Path(source.base_image_path)
            if source_path.is_file():
                copied_image_path = (
                    Path(PROJECT_ROOT / "uploads" / "recipes" / str(clone.id))
                    / f"base{source_path.suffix.lower() or '.jpg'}"
                )
                copied_image_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, copied_image_path)
                clone.base_image_path = str(copied_image_path)

        roi_id_map: dict[int, int] = {}
        for source_roi in source.rois:
            cloned_roi = RegionOfInterest(
                recipe_id=clone.id,
                scene_object_id=None,
                code=source_roi.code,
                name=source_roi.name,
                object_type=source_roi.object_type,
                shape_type=source_roi.shape_type,
                x_ratio=source_roi.x_ratio,
                y_ratio=source_roi.y_ratio,
                width_ratio=source_roi.width_ratio,
                height_ratio=source_roi.height_ratio,
                pixel_coordinates=dict(source_roi.pixel_coordinates or {}),
                padding=source_roi.padding,
                sort_order=source_roi.sort_order,
                alignment_anchor=False,
                enabled=source_roi.enabled,
            )
            database.add(cloned_roi)
            database.flush()
            roi_id_map[source_roi.id] = cloned_roi.id
            for source_item in source_roi.inspection_items:
                database.add(
                    InspectionItem(
                        roi_id=cloned_roi.id,
                        code=source_item.code,
                        name=source_item.name,
                        inspection_type=source_item.inspection_type,
                        capability=source_item.capability,
                        reference_group_id=source_item.reference_group_id,
                        expected_json=dict(source_item.expected_json or {}),
                        rule_json=dict(source_item.rule_json or {}),
                        execution_order=source_item.execution_order,
                        required=source_item.required,
                        enabled=source_item.enabled,
                    )
                )

        bindings = database.scalars(
            select(RoiScenarioBinding).where(
                RoiScenarioBinding.roi_id.in_(list(roi_id_map.keys()))
            )
        ).all()
        for binding in bindings:
            database.add(
                RoiScenarioBinding(
                    roi_id=roi_id_map[binding.roi_id],
                    scenario_version_id=binding.scenario_version_id,
                    input_mapping_json=dict(binding.input_mapping_json or {}),
                    enabled=binding.enabled,
                )
            )

        if source.feature_anchor is not None:
            anchor = source.feature_anchor
            database.add(
                RecipeFeatureAnchor(
                    recipe_id=clone.id,
                    code=anchor.code,
                    name=anchor.name,
                    x_ratio=anchor.x_ratio,
                    y_ratio=anchor.y_ratio,
                    width_ratio=anchor.width_ratio,
                    height_ratio=anchor.height_ratio,
                    padding=anchor.padding,
                    enabled=anchor.enabled,
                )
            )
        database.commit()
    except Exception:
        database.rollback()
        if copied_image_path is not None:
            copied_image_path.unlink(missing_ok=True)
        raise
    return {
        "id": clone.id,
        "code": clone.code,
        "name": clone.name,
        "status": clone.status,
        "source_recipe_id": source.id,
        **_recipe_version_metadata(database, clone),
    }


@router.post("/recipes/{recipe_id}/draft")
def create_recipe_draft(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Open one editable draft for a logical recipe without duplicating it."""

    source = _load_recipe_with_content(database, recipe_id)
    if source.status in {"DRAFT", "SAVED"}:
        return {
            "id": source.id,
            "code": source.code,
            "name": source.name,
            "status": source.status,
            "reused": True,
            **_recipe_version_metadata(database, source),
        }

    family_code = _normalize_recipe_family(source)
    existing_draft = _find_recipe_draft(database, family_code)
    if existing_draft is not None:
        return {
            "id": existing_draft.id,
            "code": existing_draft.code,
            "name": existing_draft.name,
            "status": existing_draft.status,
            "reused": True,
            **_recipe_version_metadata(database, existing_draft),
        }
    try:
        draft = _clone_recipe_content(
            database,
            source,
            code=source.code,
            name=source.name,
            recipe_family_code=family_code,
        )
        database.commit()
    except Exception:
        database.rollback()
        raise
    return {
        "id": draft.id,
        "code": draft.code,
        "name": draft.name,
        "status": draft.status,
        "reused": False,
        **_recipe_version_metadata(database, draft),
    }


@router.get("/recipes/{recipe_id}/versions")
def list_recipe_versions(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = _load_recipe_with_content(database, recipe_id)
    family_code = _recipe_family_code(recipe)
    family_filter = (
        Recipe.recipe_family_code == family_code
        if recipe.recipe_family_code
        else Recipe.code == family_code
    )
    rows = database.scalars(
        select(Recipe)
        .where(
            family_filter,
            Recipe.is_deleted.is_(False),
        )
        .order_by(Recipe.version_no.desc(), Recipe.id.desc())
    ).all()
    return {
        "recipe_family_code": family_code,
        "versions": [
            {
                "id": item.id,
                "name": item.name,
                "code": item.code,
                "status": item.status,
                "version": item.version,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                **_recipe_version_metadata(database, item),
            }
            for item in rows
        ],
    }


@router.post("/recipes/{recipe_id}/rollback")
def rollback_recipe_version(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Restore history by making a new editable draft, never by overwriting it."""

    source = _load_recipe_with_content(database, recipe_id)
    family_code = _normalize_recipe_family(source)
    existing_draft = _find_recipe_draft(database, family_code)
    if existing_draft is not None:
        if existing_draft.source_recipe_id == source.id:
            return {
                "id": existing_draft.id,
                "status": existing_draft.status,
                "reused": True,
                **_recipe_version_metadata(database, existing_draft),
            }
        raise HTTPException(
            status_code=409,
            detail="该配方已有未发布草稿；请先继续编辑、发布或删除当前草稿后再回滚。",
        )
    try:
        draft = _clone_recipe_content(
            database,
            source,
            code=source.code,
            name=source.name,
            recipe_family_code=family_code,
        )
        database.commit()
    except Exception:
        database.rollback()
        raise
    return {
        "id": draft.id,
        "status": draft.status,
        "reused": False,
        "rollback_from_recipe_id": source.id,
        **_recipe_version_metadata(database, draft),
    }


@router.post("/recipes/{recipe_id}/publish")
def publish_recipe(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    _ensure_recipe_editable(recipe)
    if not recipe.base_image_path:
        raise HTTPException(status_code=400, detail="Base image is required.")
    if not recipe.rois:
        raise HTTPException(status_code=400, detail="At least one ROI is required.")
    bindings = database.scalars(
        select(RoiScenarioBinding).join(InspectionScenarioVersion).where(
            RoiScenarioBinding.roi_id.in_([roi.id for roi in recipe.rois]),
            RoiScenarioBinding.enabled.is_(True),
            InspectionScenarioVersion.status == "PUBLISHED",
        )
    ).all()
    bound_roi_ids = {binding.roi_id for binding in bindings}
    unconfigured = [
        roi.code
        for roi in recipe.rois
        if roi.enabled and not roi.inspection_items and roi.id not in bound_roi_ids
    ]
    if unconfigured:
        raise HTTPException(
            status_code=400,
            detail=f"以下 ROI 尚未关联已发布场景或旧规则：{'、'.join(unconfigured)}。",
        )
    if not any(roi.inspection_items or roi.id in bound_roi_ids for roi in recipe.rois):
        raise HTTPException(
            status_code=400,
            detail="至少需要一个已发布场景关联或检测规则。",
        )
    family_code = _normalize_recipe_family(recipe)
    other_recipes = database.scalars(
        select(Recipe).where(
            Recipe.recipe_family_code == family_code,
            Recipe.status == "PUBLISHED",
            Recipe.is_deleted.is_(False),
            Recipe.id != recipe.id,
        )
    ).all()
    for other in other_recipes:
        other.status = "ARCHIVED"
    all_family_versions = database.scalars(
        select(Recipe).where(
            Recipe.recipe_family_code == family_code,
            Recipe.is_deleted.is_(False),
        )
    ).all()
    next_version_no = max(
        (
            item.version_no or (1 if item.status == "PUBLISHED" else 0)
            for item in all_family_versions
        ),
        default=0,
    ) + 1
    recipe.version_no = next_version_no
    recipe.version = f"V{next_version_no}"
    sync_recipe_world_model(database, recipe)
    recipe.status = "PUBLISHED"
    database.commit()
    return {
        "id": recipe.id,
        "status": recipe.status,
        **_recipe_version_metadata(database, recipe),
    }


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
            "embedding_set_version": group.embedding_set_version,
            "embedding_matrix_path": group.embedding_matrix_path,
            "embedding_manifest_path": group.embedding_manifest_path,
            "embedding_count": group.embedding_count,
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
        active_references = database.scalars(
            select(ReferenceImage).where(
                ReferenceImage.group_id == group_id,
                ReferenceImage.enabled.is_(True),
                ReferenceImage.is_deleted.is_(False),
            )
        ).all()
        recipe, roi = _reference_group_context(database, group_id)
        matrix = write_reference_matrix(
            group,
            active_references,
            {reference.id: response["embedding"]},
            recipe=recipe,
            roi=roi,
        )
        database.commit()
        embedding_status = "READY"
    except Exception as exc:
        reference.quality_status = "FAILED"
        reference.enabled = False
        database.commit()
        embedding_status = f"FAILED: {exc}"

    return {
        "id": reference.id,
        "image_path": reference.image_path,
        "image_url": _file_url(reference.image_path),
        "embedding_status": embedding_status,
        "embedding_set_version": group.embedding_set_version,
        "embedding_matrix_path": group.embedding_matrix_path,
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
    group = database.get(ReferenceGroup, reference.group_id)
    if group is not None:
        recipe, roi = _reference_group_context(database, group.id)
        active_references = database.scalars(
            select(ReferenceImage).where(
                ReferenceImage.group_id == group.id,
                ReferenceImage.enabled.is_(True),
                ReferenceImage.is_deleted.is_(False),
            )
        ).all()
        write_reference_matrix(
            group,
            active_references,
            recipe=recipe,
            roi=roi,
        )
    database.commit()
    return {
        "id": reference.id,
        "deleted": True,
        "message": "标准图片已移出当前图库，原始文件仍保留。",
    }
