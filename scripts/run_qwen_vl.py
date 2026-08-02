import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "qwen_vl_service.main:app",
        host=os.getenv("QWEN_VL_HOST", "0.0.0.0"),
        port=int(os.getenv("QWEN_VL_PORT", "9023")),
        workers=1,
    )
