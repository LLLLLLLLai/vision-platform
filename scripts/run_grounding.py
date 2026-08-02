import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "grounding_service.main:app",
        host=os.getenv("GROUNDING_HOST", "0.0.0.0"),
        port=int(os.getenv("GROUNDING_PORT", "9021")),
        workers=1,
    )
