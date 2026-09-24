from datetime import datetime
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.config import settings
from app.models.intelligence import (
    AutomationJob,
    Dataset,
    DatasetItem,
    InspectionScenarioVersion,
    VlmModelConfig,
)


router = APIRouter()


TRAINING_IMAGE_ARTIFACT_KEYS = {
    "training_curve",
    "confusion_matrix",
    "labels_preview",
}


class SceneEvaluationRequest(BaseModel):
    scenario_version_id: int
    dataset_id: int


class PromptOptimizationRequest(BaseModel):
    scenario_version_id: int
    dataset_id: int
    detection_vlm_model_id: int
    optimizer_vlm_model_id: int
    prompt_template: str = Field(min_length=1, max_length=8000)
    optimization_requirements: str = Field(min_length=1, max_length=4000)
    target_accuracy: float = Field(default=0.95, gt=0.0, le=1.0)
    max_rounds: int = Field(default=10, ge=1, le=100)
    cost_limit: float | None = Field(default=None, ge=0.0)
    # Values are used only to render a variable prompt against the selected
    # test dataset.  The prompt template itself keeps its {{ input.xxx }}
    # placeholders so it remains reusable after optimization.
    input_values: dict[str, Any] = Field(default_factory=dict)


def _training_artifact_paths(job: AutomationJob) -> dict[str, str]:
    result = job.result_json if isinstance(job.result_json, dict) else {}
    model_version = result.get("model_version")
    if not isinstance(model_version, dict):
        return {}
    artifacts = model_version.get("artifact_paths_json")
    return artifacts if isinstance(artifacts, dict) else {}


def _resolve_training_artifact(job: AutomationJob, artifact_key: str) -> Path | None:
    if not str(job.job_type or "").endswith("_TRAINING"):
        return None
    if artifact_key not in TRAINING_IMAGE_ARTIFACT_KEYS:
        return None
    raw_path = _training_artifact_paths(job).get(artifact_key)
    if not raw_path:
        return None
    try:
        artifact_root = Path(settings.training_artifacts_root).resolve()
        artifact_path = Path(str(raw_path)).resolve()
        artifact_path.relative_to(artifact_root)
    except (OSError, ValueError):
        return None
    return artifact_path if artifact_path.is_file() else None


def _training_artifact_urls(job: AutomationJob) -> dict[str, str]:
    return {
        key: f"/api/v1/automation-jobs/{job.id}/artifacts/{key}"
        for key in TRAINING_IMAGE_ARTIFACT_KEYS
        if _resolve_training_artifact(job, key) is not None
    }


def _job_payload(job: AutomationJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "scenario_version_id": job.scenario_version_id,
        "dataset_id": job.dataset_id,
        "vision_model_id": job.vision_model_id,
        "config_json": job.config_json,
        "input_snapshot_json": job.input_snapshot_json,
        "result_json": job.result_json,
        "artifact_urls": _training_artifact_urls(job),
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "can_stop": job.job_type in {"SCENE_EVALUATION", "PROMPT_OPTIMIZATION"}
        and job.status in {"QUEUED", "WAITING_GPU", "RUNNING"},
        "can_restart": job.job_type in {"SCENE_EVALUATION", "PROMPT_OPTIMIZATION"}
        and job.status in {"FAILED", "CANCELED", "COMPLETED"},
    }


@router.get("")
def list_automation_jobs(
    limit: int = 100,
    database: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    jobs = database.scalars(
        select(AutomationJob)
        .where(AutomationJob.is_deleted.is_(False))
        .order_by(AutomationJob.id.desc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return [_job_payload(job) for job in jobs]


@router.get("/{job_id}")
def get_automation_job(
    job_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    job = database.get(AutomationJob, job_id)
    if job is None or job.is_deleted:
        raise HTTPException(status_code=404, detail="自动化任务不存在。")
    return _job_payload(job)


@router.get("/{job_id}/artifacts/{artifact_key}")
def get_training_artifact(
    job_id: int,
    artifact_key: str,
    database: Session = Depends(get_db),
) -> FileResponse:
    job = database.get(AutomationJob, job_id)
    if job is None or job.is_deleted:
        raise HTTPException(status_code=404, detail="自动化任务不存在。")
    if artifact_key not in TRAINING_IMAGE_ARTIFACT_KEYS:
        raise HTTPException(status_code=404, detail="不支持的训练图表类型。")
    artifact_path = _resolve_training_artifact(job, artifact_key)
    if artifact_path is None:
        raise HTTPException(status_code=404, detail="训练图表尚未生成或已被清理。")
    media_type = mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"
    return FileResponse(artifact_path, media_type=media_type, filename=artifact_path.name)


@router.post("/scene-evaluations")
def queue_scene_evaluation(
    payload: SceneEvaluationRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = database.get(InspectionScenarioVersion, payload.scenario_version_id)
    dataset = database.get(Dataset, payload.dataset_id)
    if version is None or version.is_deleted:
        raise HTTPException(status_code=404, detail="场景版本不存在。")
    if version.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="场景评测只能使用已发布的场景版本。")
    if dataset is None or dataset.is_deleted:
        raise HTTPException(status_code=404, detail="数据集不存在。")
    if dataset.purpose != "TEST":
        raise HTTPException(status_code=400, detail="场景评测只能使用 TEST 分类的数据集。")
    item_ids = database.scalars(
        select(DatasetItem.id).where(
            DatasetItem.dataset_id == dataset.id,
            DatasetItem.is_deleted.is_(False),
        )
    ).all()
    if not item_ids:
        raise HTTPException(status_code=400, detail="测试数据集没有可用样本。")
    job = AutomationJob(
        job_type="SCENE_EVALUATION",
        scenario_version_id=version.id,
        dataset_id=dataset.id,
        input_snapshot_json={
            "dataset_revision": dataset.revision,
            "dataset_item_ids": item_ids,
        },
    )
    database.add(job)
    database.commit()
    return {"job_id": job.id, "status": job.status, "item_count": len(item_ids)}


@router.post("/prompt-optimizations")
def queue_prompt_optimization(
    payload: PromptOptimizationRequest,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    version = database.get(InspectionScenarioVersion, payload.scenario_version_id)
    dataset = database.get(Dataset, payload.dataset_id)
    if version is None or version.status != "PUBLISHED":
        raise HTTPException(status_code=400, detail="请选择已发布的场景版本。")
    if version.scenario is None or version.scenario.mode != "VLM_DIRECT":
        raise HTTPException(status_code=400, detail="提示词优化仅支持直接 VLM 检测场景。")
    if dataset is None or dataset.purpose != "TEST":
        raise HTTPException(status_code=400, detail="提示词优化必须选择 TEST 数据集。")
    detection_model = database.get(VlmModelConfig, payload.detection_vlm_model_id)
    if (
        detection_model is None
        or detection_model.is_deleted
        or not detection_model.enabled
    ):
        raise HTTPException(status_code=400, detail="选择的检测 VLM 不存在或未启用。")
    optimizer = database.get(VlmModelConfig, payload.optimizer_vlm_model_id)
    if optimizer is None or optimizer.is_deleted or not optimizer.enabled:
        raise HTTPException(status_code=400, detail="选择的优化提示词 VLM 不存在或未启用。")
    labeled_count = database.scalar(
        select(DatasetItem.id)
        .where(
            DatasetItem.dataset_id == dataset.id,
            DatasetItem.ground_truth.is_not(None),
            DatasetItem.is_deleted.is_(False),
        )
        .limit(1)
    )
    if labeled_count is None:
        raise HTTPException(status_code=400, detail="提示词优化至少需要一条标注为 OK 或 NG 的测试样本。")
    job = AutomationJob(
        job_type="PROMPT_OPTIMIZATION",
        scenario_version_id=version.id,
        dataset_id=dataset.id,
        config_json=payload.model_dump(),
        input_snapshot_json={"dataset_revision": dataset.revision},
    )
    database.add(job)
    database.commit()
    return {
        "job_id": job.id,
        "status": job.status,
        "message": (
            "提示词优化任务已保存并排队；检测模型："
            f"{detection_model.name}，优化提示词模型：{optimizer.name}。"
        ),
    }


@router.post("/{job_id}/cancel")
def cancel_automation_job(
    job_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    job = database.get(AutomationJob, job_id)
    if job is None or job.is_deleted:
        raise HTTPException(status_code=404, detail="自动化任务不存在。")
    if job.job_type not in {"SCENE_EVALUATION", "PROMPT_OPTIMIZATION"}:
        raise HTTPException(status_code=409, detail="当前只支持停止场景评测和场景优化任务。")
    if job.status in {"QUEUED", "WAITING_GPU"}:
        job.status = "CANCELED"
        job.completed_at = datetime.utcnow()
        database.commit()
        return {"id": job.id, "status": job.status, "message": "任务已停止。"}
    if job.status == "RUNNING":
        # A remote VLM request cannot be interrupted safely from a second web
        # request.  The worker checks this cooperative state before/after each
        # sample and ends the task as soon as its current model call returns.
        job.status = "CANCEL_REQUESTED"
        database.commit()
        return {
            "id": job.id,
            "status": job.status,
            "message": "已请求停止；当前模型调用完成后将不再执行下一条样本。",
        }
    if job.status == "CANCEL_REQUESTED":
        return {"id": job.id, "status": job.status, "message": "任务正在停止。"}
    raise HTTPException(status_code=409, detail="当前任务已结束，不能停止。")


@router.post("/{job_id}/restart")
def restart_automation_job(
    job_id: int,
    database: Session = Depends(get_db),
) -> dict[str, Any]:
    """Requeue a completed, canceled, or failed scene task with its snapshot."""

    job = database.get(AutomationJob, job_id)
    if job is None or job.is_deleted:
        raise HTTPException(status_code=404, detail="自动化任务不存在。")
    if job.job_type not in {"SCENE_EVALUATION", "PROMPT_OPTIMIZATION"}:
        raise HTTPException(status_code=409, detail="当前只支持重新启动场景评测和场景优化任务。")
    if job.status not in {"FAILED", "CANCELED", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="当前任务尚未结束，不能重新启动。")
    config = dict(job.config_json or {})
    config["restart_count"] = int(config.get("restart_count", 0)) + 1
    config["last_restarted_at"] = datetime.utcnow().isoformat()
    job.config_json = config
    job.status = "QUEUED"
    job.started_at = None
    job.completed_at = None
    job.error_message = None
    job.result_json = {}
    database.commit()
    return {
        "id": job.id,
        "status": job.status,
        "message": "任务已重新进入队列，将沿用原场景、数据集和任务参数执行。",
    }
