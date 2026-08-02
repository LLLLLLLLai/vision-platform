import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "sam2_service.main:app",
        host=os.getenv("SAM2_HOST", "127.0.0.1"),
        port=int(os.getenv("SAM2_PORT", "9025")),
        workers=1,
    )
