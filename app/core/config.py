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
    embedding_storage_root: str = str(PROJECT_ROOT / "embeddings")
    grounding_service_url: str = "http://127.0.0.1:9021"
    dinov2_service_url: str = "http://127.0.0.1:9022"
    qwen_vl_service_url: str = "http://127.0.0.1:9023"
    paddleocr_service_url: str = "http://127.0.0.1:9024"
    sam2_service_url: str = "http://127.0.0.1:9025"
    algorithm_timeout_seconds: float = 15.0
    reference_candidate_collection_enabled: bool = True
    reference_candidate_vlm_confidence_threshold: float = 0.90
    reference_candidate_limit_per_roi: int = 20
    reference_candidate_hash_distance: int = 4
    reference_approved_limit_per_group: int = 10
    reference_duplicate_similarity_threshold: float = 0.995
    reference_diversity_improvement_margin: float = 0.005
    reference_embedding_memory_cache_size: int = 512
    reference_similarity_scoring_mode: str = "ROBUST_TOP_K"
    reference_similarity_top_k: int = 3
    reference_similarity_top1_weight: float = 0.65
    image_alignment_enabled: bool = True
    image_alignment_max_shift_ratio: float = 0.05
    image_alignment_min_response: float = 0.08
    image_alignment_max_dimension: int = 1280
    image_alignment_anchor_search_margin_ratio: float = 0.35
    image_alignment_anchor_min_inliers: int = 8
    image_alignment_anchor_min_inlier_ratio: float = 0.35
    image_alignment_anchor_max_rotation_degrees: float = 8.0

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
