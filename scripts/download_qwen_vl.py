import argparse
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-id",
        default=os.getenv("QWEN_VL_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "vision-models" / "qwen3-vl-4b-instruct",
    )
    parser.add_argument(
        "--source",
        choices=("modelscope", "huggingface"),
        default=os.getenv("QWEN_VL_DOWNLOAD_SOURCE", "modelscope"),
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.source == "modelscope":
        from modelscope import snapshot_download

        snapshot_download(args.model_id, local_dir=str(args.output))
    else:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=args.model_id,
            local_dir=args.output,
        )
    print(f"Model downloaded to: {args.output}")


if __name__ == "__main__":
    main()
