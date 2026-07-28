from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ReferenceGroup(TimestampMixin, Base):
    __tablename__ = "reference_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(100), index=True)
    class_code: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

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
    model_code: Mapped[str] = mapped_column(String(100), default="dinov2-base")
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[ReferenceGroup] = relationship(back_populates="images")

