import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-id",
        default="IDEA-Research/grounding-dino-base",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "vision-models" / "grounding-dino-base",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.model_id,
        local_dir=args.output,
        ignore_patterns=["*.msgpack", "*.h5", "*.ot"],
    )
    print(f"Grounding DINO downloaded to: {args.output}")


if __name__ == "__main__":
    main()
