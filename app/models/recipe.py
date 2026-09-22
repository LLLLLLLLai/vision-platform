from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.world import SceneObject


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (
        Index(
            "ix_recipe_business_key",
            "line_code",
            "material_code",
            "process_code",
            "camera_code",
            "capture_index",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    # ``code`` is the business-facing recipe code.  ``recipe_family_code``
    # stays stable across published revisions so historical detection records
    # can always be traced back to one logical recipe.
    recipe_family_code: Mapped[str] = mapped_column(
        String(100), default="", index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, default=0)
    source_recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipes.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    line_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    material_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    process_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    camera_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capture_index: Mapped[int] = mapped_column(Integer, default=1)
    base_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rois: Mapped[list["RegionOfInterest"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
    )
    feature_anchor: Mapped["RecipeFeatureAnchor | None"] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RecipeFeatureAnchor(TimestampMixin, Base):
    """One recipe-level visual feature used to align production images.

    This remains separate from inspection ROIs on purpose: operators can choose
    a stable screw, hole, PCB edge or label for registration without making it
    a quality inspection item.
    """

    __tablename__ = "recipe_feature_anchors"
    __table_args__ = (
        UniqueConstraint("recipe_id", name="uq_recipe_feature_anchor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id"),
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), default="FEATURE_ANCHOR")
    name: Mapped[str] = mapped_column(String(200), default="图像定位特征点")
    x_ratio: Mapped[float] = mapped_column(Float)
    y_ratio: Mapped[float] = mapped_column(Float)
    width_ratio: Mapped[float] = mapped_column(Float)
    height_ratio: Mapped[float] = mapped_column(Float)
    padding: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    recipe: Mapped[Recipe] = relationship(back_populates="feature_anchor")


class RegionOfInterest(TimestampMixin, Base):
    __tablename__ = "regions_of_interest"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    scene_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("scene_objects.id"),
        nullable=True,
        index=True,
    )
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
    alignment_anchor: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    recipe: Mapped[Recipe] = relationship(back_populates="rois")
    scene_object: Mapped["SceneObject | None"] = relationship(
        back_populates="rois",
    )
    inspection_items: Mapped[list["InspectionItem"]] = relationship(
        back_populates="roi",
        cascade="all, delete-orphan",
    )


from app.models.inspection import InspectionItem
