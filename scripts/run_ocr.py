import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "ocr_service.main:app",
        host=os.getenv("OCR_HOST", "127.0.0.1"),
        port=int(os.getenv("OCR_PORT", "9024")),
        workers=1,
    )
