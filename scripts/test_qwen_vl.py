import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument(
        "--prompt",
        default="判断该区域中的零件是否存在、型号是否正确、安装方向是否正常。",
    )
    parser.add_argument("--url", default="http://127.0.0.1:9023")
    args = parser.parse_args()
    response = httpx.post(
        f"{args.url.rstrip('/')}/v1/judge",
        json={
            "image_path": str(args.image.resolve()),
            "prompt": args.prompt,
            "expected": {"result": "correctly installed component"},
            "max_new_tokens": 160,
        },
        timeout=180,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
