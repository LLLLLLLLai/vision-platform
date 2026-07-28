from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
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


from app.models.recipe import RegionOfInterest

