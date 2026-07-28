from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.db.base import Base
from app.db.session import engine


def init_database() -> None:
    for directory in (
        "data",
        "uploads",
        "embeddings",
        "detection_results",
        "logs",
    ):
        Path(PROJECT_ROOT / directory).mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

