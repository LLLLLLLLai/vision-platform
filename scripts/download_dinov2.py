import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from huggingface_hub import snapshot_download


MODEL_ID = "facebook/dinov2-base"
MODEL_DIR = PROJECT_ROOT / "vision-models" / "dinov2-base"


if __name__ == "__main__":
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    downloaded_path = snapshot_download(
        repo_id=MODEL_ID,
        local_dir=MODEL_DIR,
        allow_patterns=[
            "config.json",
            "preprocessor_config.json",
            "model.safetensors",
            "README.md",
        ],
    )
    print(f"DINOv2 Base downloaded to: {downloaded_path}")

