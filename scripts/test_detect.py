from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx


DEFAULT_IMAGE = Path(
    r"C:\Users\Administrator\Desktop\vision-platform\test_images\ASSY-CAMERA1PICTURE1.png"
)


def parse_camera_picture(image_path: Path) -> tuple[str, int]:
    match = re.search(
        r"CAMERA[\s_-]*0*(\d+)[\s_-]*PICTURE[\s_-]*0*(\d+)",
        image_path.stem.upper(),
    )
    if match is None:
        raise ValueError(
            "图片名称必须包含 CAMERA数字PICTURE数字，例如 CAMERA1PICTURE1。"
        )
    return f"CAMERA{int(match.group(1))}", int(match.group(2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用 Vision Platform /api/detect")
    parser.add_argument("--url", default="http://127.0.0.1:9011/api/detect")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--sn", default="DETECT-TEST-001")
    parser.add_argument("--line", required=True, help="拉线，例如 LINE01")
    parser.add_argument("--materialcode", required=True, help="物料号，例如 MAT001")
    parser.add_argument("--operation", required=True, help="工序，例如 OP20")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        print(f"素材图片不存在：{image_path}", file=sys.stderr)
        return 2
    try:
        camera, picture = parse_camera_picture(image_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = {
        "sn": args.sn,
        "line": args.line,
        "materialcode": args.materialcode,
        "operation": args.operation,
        "camera": camera,
        "picture": picture,
        "image_paths": [str(image_path)],
    }
    print("请求参数：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        response = httpx.post(args.url, json=payload, timeout=180.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"调用失败：{exc}", file=sys.stderr)
        return 1
    result = response.json()
    print("检测结果：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("code") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
