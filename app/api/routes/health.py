from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "status": "READY",
        "application": settings.app_name,
        "environment": settings.app_env,
        "time": datetime.now(timezone.utc).isoformat(),
    }

