from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.harness.runtime import get_harness


router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "status": "READY",
        "application": settings.app_name,
        "environment": settings.app_env,
        "harness_profile": get_harness().profile,
        "plugins": [plugin["code"] for plugin in get_harness().describe()["plugins"]],
        "time": datetime.now(timezone.utc).isoformat(),
    }
