import json
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:9010/api/v1"
TEST_IMAGES = PROJECT_ROOT / "test_images"


def ensure_record(
    client: httpx.Client,
    list_path: str,
    create_path: str,
    code: str,
    payload: dict,
) -> dict:
    records = client.get(list_path).raise_for_status().json()
    existing = next((record for record in records if record["code"] == code), None)
    if existing:
        return existing
    return client.post(create_path, json=payload).raise_for_status().json()


if __name__ == "__main__":
    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        product = ensure_record(
            client,
            "/configuration/products",
            "/configuration/products",
            "DEMO_PRODUCT",
            {"code": "DEMO_PRODUCT", "name": "演示产品"},
        )
        station = ensure_record(
            client,
            "/configuration/stations",
            "/configuration/stations",
            "DEMO_STATION",
            {"code": "DEMO_STATION", "name": "演示工位"},
        )
        recipe = ensure_record(
            client,
            "/configuration/recipes",
            "/configuration/recipes",
            "DEMO_RECIPE",
            {
                "code": "DEMO_RECIPE",
                "name": "DINOv2 演示配方",
                "version": "1.0",
                "product_id": product["id"],
                "station_id": station["id"],
            },
        )
        reference_group = ensure_record(
            client,
            "/configuration/reference-groups",
            "/configuration/reference-groups",
            "DEMO_REFERENCE",
            {
                "code": "DEMO_REFERENCE",
                "name": "演示正确类别",
                "object_type": "DEMO_PART",
                "class_code": "DEMO_OK",
            },
        )

        recipe_detail = client.get(
            f'/configuration/recipes/{recipe["id"]}'
        ).raise_for_status().json()
        if not recipe_detail["base_image_path"]:
            with (TEST_IMAGES / "reference.png").open("rb") as image_file:
                client.post(
                    f'/configuration/recipes/{recipe["id"]}/image',
                    files={"file": ("reference.png", image_file, "image/png")},
                ).raise_for_status()

        groups = client.get("/configuration/reference-groups").raise_for_status().json()
        group_summary = next(
            group for group in groups if group["id"] == reference_group["id"]
        )
        if group_summary["image_count"] == 0:
            with (TEST_IMAGES / "reference.png").open("rb") as image_file:
                upload_result = client.post(
                    f'/configuration/reference-groups/{reference_group["id"]}/images',
                    files={"file": ("reference.png", image_file, "image/png")},
                ).raise_for_status().json()
            print("reference_upload:", upload_result["embedding_status"])

        recipe_detail = client.get(
            f'/configuration/recipes/{recipe["id"]}'
        ).raise_for_status().json()
        if not recipe_detail["rois"]:
            roi = client.post(
                f'/configuration/recipes/{recipe["id"]}/rois',
                json={
                    "code": "DEMO_ROI",
                    "name": "演示零件区域",
                    "object_type": "DEMO_PART",
                    "x_ratio": 0.12,
                    "y_ratio": 0.12,
                    "width_ratio": 0.76,
                    "height_ratio": 0.76,
                    "padding": 0,
                },
            ).raise_for_status().json()
        else:
            roi = recipe_detail["rois"][0]

        item_codes = {item["code"] for item in roi["inspection_items"]}
        if "DEMO_SIMILARITY" not in item_codes:
            client.post(
                f'/configuration/rois/{roi["id"]}/inspection-items',
                json={
                    "code": "DEMO_SIMILARITY",
                    "name": "参考图相似度",
                    "inspection_type": "REFERENCE_SIMILARITY",
                    "capability": "REFERENCE_SIMILARITY",
                    "reference_group_id": reference_group["id"],
                    "expected_json": {"class_code": "DEMO_OK"},
                    "rule_json": {"min_similarity": 0.85},
                },
            ).raise_for_status()
        if "DEMO_COLOR" not in item_codes:
            client.post(
                f'/configuration/rois/{roi["id"]}/inspection-items',
                json={
                    "code": "DEMO_COLOR",
                    "name": "黄色比例",
                    "inspection_type": "COLOR_RATIO",
                    "capability": "COLOR_RATIO",
                    "expected_json": {"color": "YELLOW"},
                    "rule_json": {"min_ratio": 0.05, "max_ratio": 0.9},
                },
            ).raise_for_status()

        with (TEST_IMAGES / "similar.png").open("rb") as image_file:
            test_result = client.post(
                "/inspection/test",
                data={"recipe_id": str(recipe["id"])},
                files={"file": ("similar.png", image_file, "image/png")},
            ).raise_for_status().json()
        print(
            "test_result:",
            json.dumps(
                {
                    "result": test_result["result"],
                    "elapsed_ms": test_result["elapsed_ms"],
                    "items": test_result["image_results"][0]["inspection_items"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

        client.post(
            f'/configuration/recipes/{recipe["id"]}/publish'
        ).raise_for_status()
        production_result = client.post(
            "/inspection/execute",
            json={
                "sn": "DEMO-SN-001",
                "recipe_code": "DEMO_RECIPE",
                "image_paths": [str(TEST_IMAGES / "similar.png")],
            },
        ).raise_for_status().json()
        print(
            "production_result:",
            json.dumps(
                {
                    "code": production_result["code"],
                    "result": production_result["result"],
                    "recipe_code": production_result.get("recipe_code"),
                    "elapsed_ms": production_result.get("elapsed_ms"),
                    "image_paths": production_result["image_paths"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

