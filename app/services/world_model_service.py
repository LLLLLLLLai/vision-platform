from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.recipe import Recipe, RegionOfInterest
from app.models.system import Product
from app.models.world import ProductScene, SceneObject


def recipe_view_key(recipe: Recipe) -> str:
    camera = recipe.camera_code or "CAMERA"
    return f"{camera}:P{recipe.capture_index:02d}"


def inspection_expected_state(roi: RegionOfInterest) -> dict[str, Any]:
    rules = []
    for item in sorted(
        roi.inspection_items,
        key=lambda value: value.execution_order,
    ):
        if not item.enabled:
            continue
        rules.append(
            {
                "item_code": item.code,
                "inspection_type": item.inspection_type,
                "capability": item.capability,
                "expected": item.expected_json,
                "rule": item.rule_json,
                "required": item.required,
            }
        )
    return {"rules": rules}


def ensure_scene_for_recipe(
    database: Session,
    recipe: Recipe,
) -> ProductScene:
    product = database.get(Product, recipe.product_id)
    if product is None:
        raise ValueError("Recipe product does not exist.")
    scene = database.scalar(
        select(ProductScene)
        .options(selectinload(ProductScene.objects))
        .where(
            ProductScene.product_id == recipe.product_id,
            ProductScene.is_deleted.is_(False),
        )
        .order_by(ProductScene.id.desc())
    )
    if scene is None:
        scene = ProductScene(
            product_id=recipe.product_id,
            code=f"{product.code}_WORLD"[:100],
            name=f"{product.name} 产品世界模型",
            reference_image_path=recipe.base_image_path,
            reference_width=recipe.reference_width,
            reference_height=recipe.reference_height,
        )
        database.add(scene)
        database.flush()
    elif recipe.base_image_path:
        scene.reference_image_path = recipe.base_image_path
        scene.reference_width = recipe.reference_width
        scene.reference_height = recipe.reference_height
    return scene


def sync_roi_to_world_object(
    database: Session,
    recipe: Recipe,
    roi: RegionOfInterest,
) -> SceneObject:
    scene = ensure_scene_for_recipe(database, recipe)
    item = (
        database.get(SceneObject, roi.scene_object_id)
        if roi.scene_object_id is not None
        else None
    )
    if item is None:
        item = database.scalar(
            select(SceneObject).where(
                SceneObject.scene_id == scene.id,
                SceneObject.code == roi.code,
                SceneObject.is_deleted.is_(False),
            )
        )
    if item is None:
        item = SceneObject(
            scene_id=scene.id,
            code=roi.code,
            name=roi.name,
            object_type=roi.object_type or "OBJECT",
            sort_order=roi.sort_order,
        )
        database.add(item)
        database.flush()

    view_key = recipe_view_key(recipe)
    geometry = dict(item.geometry or {})
    views = dict(geometry.get("views") or {})
    views[view_key] = {
        "recipe_id": recipe.id,
        "roi_id": roi.id,
        "camera_code": recipe.camera_code,
        "capture_index": recipe.capture_index,
        "shape_type": roi.shape_type,
        "x_ratio": roi.x_ratio,
        "y_ratio": roi.y_ratio,
        "width_ratio": roi.width_ratio,
        "height_ratio": roi.height_ratio,
        "padding": roi.padding,
    }
    geometry["views"] = views
    item.code = roi.code
    item.name = roi.name
    item.object_type = roi.object_type or "OBJECT"
    item.geometry = geometry
    item.expected_state = inspection_expected_state(roi)
    perception_config = dict(item.perception_config or {})
    perception_config.update({
        "capabilities": sorted(
            {
                inspection.capability
                for inspection in roi.inspection_items
                if inspection.enabled
            }
        ),
        "view_key": view_key,
    })
    item.perception_config = perception_config
    item.sort_order = roi.sort_order
    roi.scene_object_id = item.id
    return item


def sync_recipe_world_model(
    database: Session,
    recipe: Recipe,
) -> ProductScene:
    scene = ensure_scene_for_recipe(database, recipe)
    for roi in recipe.rois:
        sync_roi_to_world_object(database, recipe, roi)
    database.flush()
    return scene
