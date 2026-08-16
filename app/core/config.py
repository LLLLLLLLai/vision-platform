from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Vision Platform"
    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 9010
    database_url: str = "sqlite:///./data/vision_platform.db"
    grounding_service_url: str = "http://127.0.0.1:9021"
    dinov2_service_url: str = "http://127.0.0.1:9022"
    qwen_vl_service_url: str = "http://127.0.0.1:9023"
    paddleocr_service_url: str = "http://127.0.0.1:9024"
    sam2_service_url: str = "http://127.0.0.1:9025"
    algorithm_timeout_seconds: float = 15.0
    reference_candidate_collection_enabled: bool = True
    reference_candidate_similarity_threshold: float = 0.93
    reference_candidate_vlm_confidence_threshold: float = 0.90
    reference_candidate_limit_per_roi: int = 20

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
