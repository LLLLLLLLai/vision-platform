from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ReferenceObjectType(TimestampMixin, Base):
    __tablename__ = "reference_object_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ReferenceGroup(TimestampMixin, Base):
    __tablename__ = "reference_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(100), index=True)
    class_code: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding_set_version: Mapped[int] = mapped_column(Integer, default=0)
    embedding_matrix_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    embedding_manifest_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    embedding_count: Mapped[int] = mapped_column(Integer, default=0)

    images: Mapped[list["ReferenceImage"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class ReferenceImage(TimestampMixin, Base):
    __tablename__ = "reference_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("reference_groups.id"),
        index=True,
    )
    image_path: Mapped[str] = mapped_column(String(500))
    embedding_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_code: Mapped[str] = mapped_column(String(100), default="dinov2-base")
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[ReferenceGroup] = relationship(back_populates="images")


class ReferenceCandidate(TimestampMixin, Base):
    __tablename__ = "reference_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("reference_groups.id"),
        index=True,
    )
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    roi_id: Mapped[int] = mapped_column(
        ForeignKey("regions_of_interest.id"),
        index=True,
    )
    source_task_id: Mapped[int] = mapped_column(
        ForeignKey("detection_tasks.id"),
        index=True,
    )
    source_item_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("detection_item_results.id"),
        nullable=True,
    )
    sn: Mapped[str] = mapped_column(String(200), index=True)
    baseline_image_path: Mapped[str] = mapped_column(String(500))
    candidate_image_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(100), index=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    vlm_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    vlm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default="PENDING_VLM",
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    promoted_reference_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_images.id"),
        nullable=True,
    )
