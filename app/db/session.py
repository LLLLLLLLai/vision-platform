from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def engine_options_for(database_url: str) -> dict[str, Any]:
    """Return dialect-safe engine options for local SQLite and MySQL 8."""
    backend = make_url(database_url).get_backend_name()
    if backend == "sqlite":
        return {
            "connect_args": {"check_same_thread": False, "timeout": 30},
            "pool_pre_ping": True,
        }

    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_size": max(1, settings.database_pool_size),
        "max_overflow": max(0, settings.database_max_overflow),
        "pool_recycle": max(60, settings.database_pool_recycle_seconds),
    }
    if backend == "mysql":
        options["connect_args"] = {
            "connect_timeout": max(1, settings.database_connect_timeout_seconds)
        }
    return options


engine = create_engine(
    settings.database_url,
    **engine_options_for(settings.database_url),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
