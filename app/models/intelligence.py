"""Scene, dataset and model-management persistence for the intelligence layer.

The existing :mod:`app.models.world` module models a product's physical layout.
This module deliberately uses ``InspectionScenario`` for executable detection
scenes so the two concepts never collide in code or in the user interface.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.inspection import DetectionTask
    from app.models.recipe import RegionOfInterest


class InspectionScenario(TimestampMixin, Base):
    """A reusable business detection scene, independent from a recipe ROI."""

    __tablename__ = "inspection_scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="GENERAL", index=True)
    mode: Mapped[str] = mapped_column(String(30), default="VLM_DIRECT", index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    published_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list["InspectionScenarioVersion"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
    )


class InspectionScenarioVersion(TimestampMixin, Base):
    """An immutable published scene definition or an editable draft."""

    __tablename__ = "inspection_scenario_versions"
    __table_args__ = (
        UniqueConstraint("scenario_id", "version", name="uq_scene_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_scenarios.id"), index=True
    )
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    prompt_template: Mapped[str | None] = mapped_column(String(8000), nullable=True)
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    primary_vlm_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("vlm_model_configs.id"), nullable=True, index=True
    )
    review_vlm_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("vlm_model_configs.id"), nullable=True, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    scenario: Mapped[InspectionScenario] = relationship(back_populates="versions")
    nodes: Mapped[list["ScenarioNode"]] = relationship(
        back_populates="scenario_version",
        cascade="all, delete-orphan",
    )
    edges: Mapped[list["ScenarioEdge"]] = relationship(
        back_populates="scenario_version",
        cascade="all, delete-orphan",
    )
    bindings: Mapped[list["RoiScenarioBinding"]] = relationship(
        back_populates="scenario_version",
    )
    executions: Mapped[list["ScenarioExecution"]] = relationship(
        back_populates="scenario_version",
    )
    primary_vlm_model: Mapped["VlmModelConfig | None"] = relationship(
        foreign_keys=[primary_vlm_model_id],
    )
    review_vlm_model: Mapped["VlmModelConfig | None"] = relationship(
        foreign_keys=[review_vlm_model_id],
    )


class ScenarioNode(TimestampMixin, Base):
    """A workflow node. Nodes are evaluated in ``sort_order`` for V1."""

    __tablename__ = "scenario_nodes"
    __table_args__ = (
        UniqueConstraint("scenario_version_id", "node_key", name="uq_scene_node_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_version_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    node_type: Mapped[str] = mapped_column(String(50), index=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    canvas_x: Mapped[float] = mapped_column(Float, default=0.0)
    canvas_y: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    scenario_version: Mapped[InspectionScenarioVersion] = relationship(
        back_populates="nodes"
    )


class ScenarioEdge(TimestampMixin, Base):
    """Directed edge and output mapping retained for the workflow canvas."""

    __tablename__ = "scenario_edges"
    __table_args__ = (
        UniqueConstraint(
            "scenario_version_id",
            "source_node_key",
            "target_node_key",
            name="uq_scene_edge",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_version_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), index=True
    )
    source_node_key: Mapped[str] = mapped_column(String(100))
    target_node_key: Mapped[str] = mapped_column(String(100))
    mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    scenario_version: Mapped[InspectionScenarioVersion] = relationship(
        back_populates="edges"
    )


class RoiScenarioBinding(TimestampMixin, Base):
    """The published executable scene selected for one recipe ROI."""

    __tablename__ = "roi_scenario_bindings"

    id: Mapped[int] = mapped_column(primary_key=True)
    roi_id: Mapped[int] = mapped_column(
        ForeignKey("regions_of_interest.id"), unique=True, index=True
    )
    scenario_version_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), index=True
    )
    input_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    scenario_version: Mapped[InspectionScenarioVersion] = relationship(
        back_populates="bindings"
    )


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    purpose: Mapped[str] = mapped_column(String(30), default="TEST", index=True)
    media_type: Mapped[str] = mapped_column(String(30), default="IMAGE")
    annotation_type: Mapped[str] = mapped_column(String(30), default="NONE")
    # Dataset-local labels let one dataset contain every category selected by
    # the operator without coupling annotation to an unrelated model registry.
    label_schema_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    # A published scene can contribute its production ROI crops to a dataset.
    # Collected samples remain PENDING until an operator labels them.
    collection_scenario_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), nullable=True, index=True
    )
    auto_collect_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_collect_limit: Mapped[int] = mapped_column(Integer, default=1000)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[list["DatasetItem"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )


class DatasetItem(TimestampMixin, Base):
    __tablename__ = "dataset_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    media_path: Mapped[str] = mapped_column(String(1000))
    original_name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(30), default="IMAGE")
    ground_truth: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    annotation_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    annotation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    split: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="UPLOAD")
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    dataset: Mapped[Dataset] = relationship(back_populates="items")


class VlmModelConfig(TimestampMixin, Base):
    """A credential-safe configuration for an OpenAI-compatible VLM endpoint."""

    __tablename__ = "vlm_model_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    model_name: Mapped[str] = mapped_column(String(200))
    api_key_ciphertext: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    api_key_env_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_key_hint: Mapped[str | None] = mapped_column(String(30), nullable=True)
    thinking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1024)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class VisionModel(TimestampMixin, Base):
    """A logical user-maintained trainable vision model definition."""

    __tablename__ = "vision_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    base_model: Mapped[str | None] = mapped_column(String(300), nullable=True)
    labels_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list["VisionModelVersion"]] = relationship(
        back_populates="vision_model",
        cascade="all, delete-orphan",
    )


class VisionModelVersion(TimestampMixin, Base):
    __tablename__ = "vision_model_versions"
    __table_args__ = (
        UniqueConstraint("vision_model_id", "version", name="uq_vision_model_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vision_model_id: Mapped[int] = mapped_column(
        ForeignKey("vision_models.id"), index=True
    )
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    weights_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_paths_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    vision_model: Mapped[VisionModel] = relationship(back_populates="versions")


class AutomationJob(TimestampMixin, Base):
    """Persistent, restart-safe asynchronous work requested from the UI/API."""

    __tablename__ = "automation_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    scenario_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), nullable=True, index=True
    )
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    vision_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("vision_models.id"), nullable=True
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ScenarioExecution(TimestampMixin, Base):
    """Immutable execution fact; raw model output is never overwritten."""

    __tablename__ = "scenario_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_version_id: Mapped[int] = mapped_column(
        ForeignKey("inspection_scenario_versions.id"), index=True
    )
    detection_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("detection_tasks.id"), nullable=True, index=True
    )
    roi_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions_of_interest.id"), nullable=True, index=True
    )
    dataset_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_items.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(30), default="PRODUCTION", index=True)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING", index=True)
    result: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    scenario_version: Mapped[InspectionScenarioVersion] = relationship(
        back_populates="executions"
    )
    review: Mapped["ScenarioExecutionReview | None"] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ScenarioExecutionReview(TimestampMixin, Base):
    """Asynchronous VLM review plus optional human override for one execution."""

    __tablename__ = "scenario_execution_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_executions.id"), unique=True, index=True
    )
    review_vlm_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("vlm_model_configs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    raw_output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    manual_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    manual_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    manually_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    execution: Mapped[ScenarioExecution] = relationship(back_populates="review")
