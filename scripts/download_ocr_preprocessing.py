import argparse
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlretrieve


MODELS = ("PP-LCNet_x1_0_doc_ori", "PP-LCNet_x1_0_textline_ori", "UVDoc")
BASE_URL = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"


def main():
    parser = argparse.ArgumentParser(description="联网准备可选 OCR 方向分类和去畸变模型，推理时不下载。")
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "vision-models" / "paddleocr")
    arguments = parser.parse_args()
    destination = arguments.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name in MODELS:
        if (destination / f"{name}_infer" / "inference.pdiparams").is_file():
            print(f"Already installed: {name}", flush=True)
            continue
        with TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / f"{name}.tar"
            print(f"Downloading: {name}", flush=True)
            urlretrieve(f"{BASE_URL}/{name}_infer.tar", archive_path)
            with tarfile.open(archive_path) as archive:
                for member in archive.getmembers():
                    target = (destination / member.name).resolve()
                    if not target.is_relative_to(destination / f"{name}_infer") or member.issym() or member.islnk():
                        raise ValueError(f"Unsafe archive entry: {member.name}")
                archive.extractall(destination, filter="data")
            print(f"Installed: {name}", flush=True)


if __name__ == "__main__":
    main()
