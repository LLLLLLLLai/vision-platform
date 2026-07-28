import json
from pathlib import Path
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = PROJECT_ROOT / "test_images"
SERVICE_URL = "http://127.0.0.1:9022"


def get_json(path: str) -> dict:
    with urlopen(f"{SERVICE_URL}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(path: str, payload: dict) -> dict:
    request = Request(
        f"{SERVICE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    health = get_json("/health")
    print("health:", json.dumps(health, ensure_ascii=False, indent=2))

    result = post_json(
        "/v1/similarity",
        {
            "image_path": str(TEST_DIR / "similar.png"),
            "reference_paths": [
                str(TEST_DIR / "reference.png"),
                str(TEST_DIR / "different.png"),
            ],
            "top_k": 2,
        },
    )
    print("similarity:", json.dumps(result, ensure_ascii=False, indent=2))

