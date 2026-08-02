from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.recipe import RegionOfInterest


class ProductScene(TimestampMixin, Base):
    __tablename__ = "product_scenes"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    coordinate_system: Mapped[str] = mapped_column(String(30), default="IMAGE_2D")
    reference_image_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    reference_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alignment_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    objects: Mapped[list["SceneObject"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        foreign_keys="SceneObject.scene_id",
    )
    relations: Mapped[list["ObjectRelation"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
    )


class SceneObject(TimestampMixin, Base):
    __tablename__ = "scene_objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("product_scenes.id"), index=True
    )
    parent_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("scene_objects.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(100), index=True)
    location_mode: Mapped[str] = mapped_column(String(30), default="FIXED_ROI")
    geometry: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    perception_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    scene: Mapped[ProductScene] = relationship(
        back_populates="objects",
        foreign_keys=[scene_id],
    )
    parent: Mapped["SceneObject | None"] = relationship(
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list["SceneObject"]] = relationship(back_populates="parent")
    rois: Mapped[list["RegionOfInterest"]] = relationship(
        back_populates="scene_object",
    )


class ObjectRelation(TimestampMixin, Base):
    __tablename__ = "object_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("product_scenes.id"), index=True
    )
    source_object_id: Mapped[int] = mapped_column(
        ForeignKey("scene_objects.id"), index=True
    )
    target_object_id: Mapped[int] = mapped_column(
        ForeignKey("scene_objects.id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(100), index=True)
    expected_relation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    scene: Mapped[ProductScene] = relationship(back_populates="relations")


class ModelRegistry(TimestampMixin, Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    capability: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100), default="LOCAL")
    runtime: Mapped[str] = mapped_column(String(100), default="TRANSFORMERS")
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    service_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
