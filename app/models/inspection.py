from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class InspectionItem(TimestampMixin, Base):
    __tablename__ = "inspection_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    roi_id: Mapped[int] = mapped_column(
        ForeignKey("regions_of_interest.id"),
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    inspection_type: Mapped[str] = mapped_column(String(100))
    capability: Mapped[str] = mapped_column(String(100), index=True)
    algorithm_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("algorithm_configs.id"),
        nullable=True,
    )
    reference_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_groups.id"),
        nullable=True,
    )
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rule_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_order: Mapped[int] = mapped_column(Integer, default=0)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    roi: Mapped["RegionOfInterest"] = relationship(
        back_populates="inspection_items",
    )


class DetectionTask(TimestampMixin, Base):
    __tablename__ = "detection_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    sn: Mapped[str] = mapped_column(String(200), index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    recipe_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="RUNNING", index=True)
    original_image_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_image_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    item_results: Mapped[list["DetectionItemResult"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class DetectionApiCall(TimestampMixin, Base):
    __tablename__ = "detection_api_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    caller_ip: Mapped[str] = mapped_column(String(100), index=True)
    called_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    sn: Mapped[str] = mapped_column(String(200), index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_code: Mapped[int] = mapped_column(Integer, index=True)
    call_status: Mapped[str] = mapped_column(String(30), index=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class DetectionItemResult(TimestampMixin, Base):
    __tablename__ = "detection_item_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("detection_tasks.id"),
        index=True,
    )
    image_path: Mapped[str] = mapped_column(String(500))
    roi_id: Mapped[int] = mapped_column(ForeignKey("regions_of_interest.id"))
    inspection_item_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_items.id")
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    roi_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    task: Mapped[DetectionTask] = relationship(back_populates="item_results")


from app.models.recipe import RegionOfInterest
