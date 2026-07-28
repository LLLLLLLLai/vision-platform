from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    camera_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capture_index: Mapped[int] = mapped_column(Integer, default=1)
    base_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rois: Mapped[list["RegionOfInterest"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
    )


class RegionOfInterest(TimestampMixin, Base):
    __tablename__ = "regions_of_interest"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    shape_type: Mapped[str] = mapped_column(String(30), default="RECTANGLE")
    x_ratio: Mapped[float] = mapped_column(Float)
    y_ratio: Mapped[float] = mapped_column(Float)
    width_ratio: Mapped[float] = mapped_column(Float)
    height_ratio: Mapped[float] = mapped_column(Float)
    pixel_coordinates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    padding: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    recipe: Mapped[Recipe] = relationship(back_populates="rois")
    inspection_items: Mapped[list["InspectionItem"]] = relationship(
        back_populates="roi",
        cascade="all, delete-orphan",
    )


from app.models.inspection import InspectionItem

