import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


if __name__ == "__main__":
    uvicorn.run(
        "dinov2_service.main:app",
        host="127.0.0.1",
        port=9022,
        workers=1,
    )

