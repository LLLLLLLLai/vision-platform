import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.recipe import Recipe
from app.services.algorithm_client import AlgorithmServiceClient
from app.services.discovery_service import (
    build_localization_groups,
    enrich_harness_connection_candidates,
    normalize_grounding_result,
    normalize_inventory_result,
)
from app.services.harness_segmentation import (
    harness_segments_to_candidates,
    merge_harness_segmentations,
    segment_orange_harness,
)


router = APIRouter()
client = AlgorithmServiceClient()


class VlmJudgeRequest(BaseModel):
    image_path: str
    prompt: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(default_factory=dict)
    max_new_tokens: int = Field(default=160, ge=16, le=512)


class ObjectDiscoverRequest(BaseModel):
    recipe_id: int
    object_types: list[str] = Field(default_factory=list, max_length=30)
    max_objects: int = Field(default=30, ge=1, le=80)


@router.get("/status")
async def algorithm_status() -> dict[str, Any]:
    services = {
        "grounding_dino": settings.grounding_service_url,
        "dinov2": settings.dinov2_service_url,
        "qwen3_vl": settings.qwen_vl_service_url,
        "paddleocr": settings.paddleocr_service_url,
        "sam2": settings.sam2_service_url,
    }

    async def inspect(name: str, url: str) -> tuple[str, dict[str, Any]]:
        try:
            result = await client.health(url)
            return name, {"status": "READY", "detail": result}
        except Exception as exc:
            return name, {"status": "UNAVAILABLE", "detail": str(exc)}

    checks = await asyncio.gather(
        *(inspect(name, url) for name, url in services.items())
    )
    return dict(checks)


@router.post("/vlm/judge")
async def vlm_judge(payload: VlmJudgeRequest) -> dict[str, Any]:
    return await client.vlm_judge(
        image_path=payload.image_path,
        prompt=payload.prompt,
        expected=payload.expected,
        max_new_tokens=payload.max_new_tokens,
    )


@router.post("/discover")
async def discover_objects(
    payload: ObjectDiscoverRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    recipe = database.get(Recipe, payload.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="配方不存在")
    if not recipe.base_image_path:
        raise HTTPException(status_code=400, detail="请先上传配方图片")
    try:
        grounding_health = await client.health(settings.grounding_service_url)
        if grounding_health.get("status") != "READY":
            raise RuntimeError(grounding_health.get("error") or "定位模型未就绪")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Grounding DINO 定位服务未就绪，不返回不可信候选框：{exc}",
        ) from exc
    try:
        inventory_result = await client.inventory_objects(
            image_path=recipe.base_image_path,
            object_types=payload.object_types,
            max_types=min(12, len(payload.object_types) or 12),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Qwen3-VL 物体清单解析失败，可继续手动画框：{exc}",
        ) from exc
    inventory = normalize_inventory_result(inventory_result)
    try:
        color_harness_segmentation = await asyncio.to_thread(
            segment_orange_harness,
            recipe.base_image_path,
        )
    except Exception as exc:
        color_harness_segmentation = {
            "mode": "ORANGE_HARNESS_HSV",
            "supported_scope": "橙色高压线束",
            "segment_count": 0,
            "segments": [],
            "mask_path": None,
            "overlay_path": None,
            "error": str(exc),
        }
    if not inventory:
        segmentation_candidates = harness_segments_to_candidates(
            color_harness_segmentation,
        )
        return {
            "code": 0,
            "message": "未发现可定位的物体类型",
            "engine": "QWEN3_VL + GROUNDING_DINO",
            "inventory": [],
            "candidates": [],
            "candidate_count": 0,
            "segmentation_candidates": segmentation_candidates,
            "segmentation_candidate_count": len(segmentation_candidates),
            "harness_segmentation": color_harness_segmentation,
        }
    localization_groups = build_localization_groups(
        inventory,
        payload.object_types,
    )
    combined_inventory: list[dict[str, Any]] = []
    combined_objects: list[dict[str, Any]] = []
    harness_segmentation_seeds: list[dict[str, Any]] = []
    localization_elapsed_ms = 0.0
    localization_model = "Grounding DINO"
    image_width = 0
    image_height = 0
    group_summaries: list[dict[str, Any]] = []
    try:
        for group in localization_groups:
            group_inventory = group["inventory"]
            localization_result = await client.localize(
                image_path=recipe.base_image_path,
                prompts=[item["prompt_en"] for item in group_inventory],
                threshold=group["threshold"],
                text_threshold=group["text_threshold"],
            )
            combined_inventory.extend(group_inventory)
            if group["name"] == "HARNESS_SEGMENTATION_SEEDS":
                harness_segmentation_seeds = normalize_grounding_result(
                    localization_result,
                    group_inventory,
                    max_objects=10,
                )
            else:
                combined_objects.extend(localization_result.get("objects", []))
            localization_elapsed_ms += float(
                localization_result.get("elapsed_ms") or 0
            )
            localization_model = localization_result.get(
                "model",
                localization_model,
            )
            image_width = int(localization_result.get("image_width") or image_width)
            image_height = int(localization_result.get("image_height") or image_height)
            group_summaries.append(
                {
                    "name": group["name"],
                    "prompt_count": len(group_inventory),
                    "raw_candidate_count": len(localization_result.get("objects", [])),
                }
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Grounding DINO 定位失败，不返回 Qwen 推测坐标：{exc}",
        ) from exc
    combined_localization_result = {
        "image_width": image_width,
        "image_height": image_height,
        "objects": combined_objects,
    }
    candidates = normalize_grounding_result(
        combined_localization_result,
        combined_inventory,
        payload.max_objects,
    )
    candidates = enrich_harness_connection_candidates(
        recipe.base_image_path,
        candidates,
        payload.max_objects,
    )
    harness_segmentation = color_harness_segmentation
    if harness_segmentation_seeds:
        try:
            sam2_result = await client.segment_harness(
                image_path=recipe.base_image_path,
                seeds=[
                    {
                        "label": item["label"],
                        "bbox": item["bbox"],
                        "confidence": item["confidence"],
                        "color": next(
                            (
                                inventory_item.get("color", "unknown")
                                for inventory_item in combined_inventory
                                if inventory_item.get("prompt_en") == item.get("prompt_en")
                            ),
                            "unknown",
                        ),
                    }
                    for item in harness_segmentation_seeds
                ],
            )
            harness_segmentation = merge_harness_segmentations(
                sam2_result,
                color_harness_segmentation,
            )
        except Exception as exc:
            harness_segmentation = {
                **color_harness_segmentation,
                "sam2_error": str(exc),
            }
    segmentation_candidates = harness_segments_to_candidates(harness_segmentation)
    return {
        "code": 0,
        "message": "success",
        "engine": "QWEN3_VL + GROUNDING_DINO",
        "inventory_model": inventory_result.get("model", "Qwen3-VL"),
        "localization_model": localization_model,
        "elapsed_ms": round(
            float(inventory_result.get("elapsed_ms") or 0)
            + localization_elapsed_ms,
            2,
        ),
        "inventory": inventory,
        "localization_groups": group_summaries,
        "harness_segmentation_seed_count": len(harness_segmentation_seeds),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "segmentation_candidates": segmentation_candidates,
        "segmentation_candidate_count": len(segmentation_candidates),
        "harness_segmentation": harness_segmentation,
    }
