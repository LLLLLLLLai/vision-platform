from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product
from app.models.world import ModelRegistry, ObjectRelation, ProductScene, SceneObject
from app.services.world_model_service import sync_recipe_world_model


router = APIRouter()


class SceneCreate(BaseModel):
    product_id: int
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    version: str = "1.0"
    coordinate_system: str = "IMAGE_2D"
    alignment_config: dict[str, Any] = Field(default_factory=dict)


class SceneObjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=100)
    parent_object_id: int | None = None
    location_mode: str = "FIXED_ROI"
    geometry: dict[str, Any] = Field(default_factory=dict)
    expected_state: dict[str, Any] = Field(default_factory=dict)
    perception_config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class SceneObjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=100)
    parent_object_id: int | None = None
    location_mode: str = "FIXED_ROI"
    expected_state: dict[str, Any] = Field(default_factory=dict)
    perception_config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    enabled: bool = True


class RelationCreate(BaseModel):
    source_object_id: int
    target_object_id: int
    relation_type: str = Field(min_length=1, max_length=100)
    expected_relation: dict[str, Any] = Field(default_factory=dict)


class ModelRegistryCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=100)
    provider: str = "LOCAL"
    runtime: str = "TRANSFORMERS"
    version: str | None = None
    model_path: str | None = None
    service_url: str | None = None
    config_json: dict[str, Any] = Field(default_factory=dict)


def _object_payload(item: SceneObject) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "object_type": item.object_type,
        "parent_object_id": item.parent_object_id,
        "location_mode": item.location_mode,
        "geometry": item.geometry,
        "expected_state": item.expected_state,
        "perception_config": item.perception_config,
        "sort_order": item.sort_order,
        "enabled": item.enabled,
        "roi_ids": [roi.id for roi in item.rois],
    }


def _scene_payload(scene: ProductScene) -> dict[str, Any]:
    return {
        "id": scene.id,
        "product_id": scene.product_id,
        "code": scene.code,
        "name": scene.name,
        "version": scene.version,
        "status": scene.status,
        "coordinate_system": scene.coordinate_system,
        "reference_image_path": scene.reference_image_path,
        "reference_width": scene.reference_width,
        "reference_height": scene.reference_height,
        "alignment_config": scene.alignment_config,
        "objects": [_object_payload(item) for item in scene.objects],
        "relations": [
            {
                "id": relation.id,
                "source_object_id": relation.source_object_id,
                "target_object_id": relation.target_object_id,
                "relation_type": relation.relation_type,
                "expected_relation": relation.expected_relation,
                "enabled": relation.enabled,
            }
            for relation in scene.relations
        ],
    }


@router.get("/scenes")
def list_scenes(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    scenes = database.scalars(
        select(ProductScene)
        .options(selectinload(ProductScene.objects))
        .where(ProductScene.is_deleted.is_(False))
        .order_by(ProductScene.id.desc())
    ).all()
    return [
        {
            "id": scene.id,
            "product_id": scene.product_id,
            "code": scene.code,
            "name": scene.name,
            "version": scene.version,
            "status": scene.status,
            "coordinate_system": scene.coordinate_system,
            "object_count": len(scene.objects),
        }
        for scene in scenes
    ]


@router.post("/scenes")
def create_scene(
    payload: SceneCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    if database.get(Product, payload.product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    scene = ProductScene(**payload.model_dump())
    database.add(scene)
    database.commit()
    database.refresh(scene)
    return {"id": scene.id, "code": scene.code, "status": scene.status}


@router.get("/scenes/{scene_id}")
def scene_detail(
    scene_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    scene = database.scalar(
        select(ProductScene)
        .options(
            selectinload(ProductScene.objects).selectinload(SceneObject.rois),
            selectinload(ProductScene.relations),
        )
        .where(ProductScene.id == scene_id)
    )
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found.")
    return _scene_payload(scene)


@router.get("/recipes/{recipe_id}/scene")
def recipe_scene(
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
    scene = sync_recipe_world_model(database, recipe)
    database.commit()
    return scene_detail(scene.id, database)


@router.post("/recipes/{recipe_id}/sync")
def sync_recipe_scene(
    recipe_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    return recipe_scene(recipe_id, database)


@router.post("/scenes/{scene_id}/objects")
def create_scene_object(
    scene_id: int,
    payload: SceneObjectCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    if database.get(ProductScene, scene_id) is None:
        raise HTTPException(status_code=404, detail="Scene not found.")
    if payload.parent_object_id is not None:
        parent = database.get(SceneObject, payload.parent_object_id)
        if parent is None or parent.scene_id != scene_id:
            raise HTTPException(status_code=400, detail="Invalid parent object.")
    item = SceneObject(scene_id=scene_id, **payload.model_dump())
    database.add(item)
    database.commit()
    database.refresh(item)
    return {"id": item.id, "code": item.code, "object_type": item.object_type}


@router.put("/objects/{object_id}")
def update_scene_object(
    object_id: int,
    payload: SceneObjectUpdate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    item = database.get(SceneObject, object_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Scene object not found.")
    if payload.parent_object_id == object_id:
        raise HTTPException(status_code=400, detail="Object cannot parent itself.")
    if payload.parent_object_id is not None:
        parent = database.get(SceneObject, payload.parent_object_id)
        if parent is None or parent.scene_id != item.scene_id:
            raise HTTPException(status_code=400, detail="Invalid parent object.")
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    database.commit()
    database.refresh(item)
    return _object_payload(item)


@router.post("/scenes/{scene_id}/relations")
def create_relation(
    scene_id: int,
    payload: RelationCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    source = database.get(SceneObject, payload.source_object_id)
    target = database.get(SceneObject, payload.target_object_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Scene object not found.")
    if source.scene_id != scene_id or target.scene_id != scene_id:
        raise HTTPException(status_code=400, detail="Objects must belong to scene.")
    relation = ObjectRelation(scene_id=scene_id, **payload.model_dump())
    database.add(relation)
    database.commit()
    database.refresh(relation)
    return {"id": relation.id, "relation_type": relation.relation_type}


@router.get("/models")
def list_models(database: Session = Depends(get_db)) -> list[dict[str, Any]]:
    models = database.scalars(
        select(ModelRegistry)
        .where(ModelRegistry.is_deleted.is_(False))
        .order_by(ModelRegistry.id)
    ).all()
    return [
        {
            "id": model.id,
            "code": model.code,
            "name": model.name,
            "capability": model.capability,
            "runtime": model.runtime,
            "service_url": model.service_url,
            "enabled": model.enabled,
        }
        for model in models
    ]


@router.post("/models")
def register_model(
    payload: ModelRegistryCreate,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    model = ModelRegistry(**payload.model_dump())
    database.add(model)
    database.commit()
    database.refresh(model)
    return {"id": model.id, "code": model.code, "capability": model.capability}
