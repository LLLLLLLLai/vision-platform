"""Persistent worker for reviews and dataset-driven scene evaluation.

The worker intentionally claims one job at a time.  This keeps SQLite safe in
the local deployment and gives the Linux deployment a clear upgrade path to a
separate worker process without changing job semantics.
"""

import asyncio
import subprocess
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.intelligence import (
    AutomationJob,
    Dataset,
    DatasetItem,
    InspectionScenarioVersion,
    ScenarioExecution,
    VlmModelConfig,
    VisionModel,
    VisionModelVersion,
)
from app.services.openai_compatible_vlm import vlm_client
from app.services.scenario_runtime import scenario_runtime
from app.services.yolo_training import (
    YoloTrainingError,
    build_training_spec,
    run_yolo_training,
)


def _metrics(rows: list[tuple[str | None, str | None]]) -> dict[str, Any]:
    labeled = [(str(actual).upper(), str(predicted).upper()) for actual, predicted in rows if actual]
    total = len(rows)
    labeled_count = len(labeled)
    correct = sum(actual == predicted for actual, predicted in labeled)
    return {
        "total": total,
        "labeled": labeled_count,
        "correct": correct,
        "accuracy": round(correct / labeled_count, 4) if labeled_count else None,
        "ok_to_ok": sum(actual == "OK" and predicted == "OK" for actual, predicted in labeled),
        "ng_to_ng": sum(actual == "NG" and predicted == "NG" for actual, predicted in labeled),
        "false_accept": sum(actual == "NG" and predicted == "OK" for actual, predicted in labeled),
        "false_reject": sum(actual == "OK" and predicted == "NG" for actual, predicted in labeled),
        "uncertain_or_error": sum(predicted not in {"OK", "NG"} for _, predicted in labeled),
    }


def _optimization_target_reached(
    metrics: dict[str, Any],
    target_accuracy: float,
) -> bool:
    """Return whether a prompt has genuinely met a valid measured target."""
    try:
        target = float(target_accuracy)
        accuracy = float(metrics.get("accuracy"))
        labeled = int(metrics.get("labeled", metrics.get("total", 0)))
    except (TypeError, ValueError):
        return False
    return 0.0 < target <= 1.0 and labeled > 0 and 0.0 <= accuracy <= 1.0 and accuracy >= target


def _gpu_free_memory_mb() -> list[int]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    values: list[int] = []
    for line in output.splitlines():
        try:
            values.append(int(line.strip()))
        except ValueError:
            continue
    return values


class AutomationWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._next_gpu_poll_at = 0.0

    async def start(self) -> None:
        if not settings.automation_worker_enabled or self._task is not None:
            return
        self._recover_interrupted_jobs()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="vision-platform-automation-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
        self._task = None

    def _recover_interrupted_jobs(self) -> None:
        """Make jobs interrupted by a process restart visible and recoverable.

        A local YOLO run is executed by Ultralytics in a background thread and
        cannot be terminated safely from a web request.  Keeping a stale
        ``RUNNING`` status after an unexpected restart is worse than reporting
        a clear failure: it hides the job forever and can lead an operator to
        assume that an old GPU run is still valid.
        """

        with SessionLocal() as database:
            jobs = list(
                database.scalars(
                    select(AutomationJob).where(AutomationJob.status == "RUNNING")
                )
            )
            if not jobs:
                return
            recovered_at = datetime.utcnow()
            for job in jobs:
                previous_error = (job.error_message or "").strip()
                message = "服务重启导致任务中断，请确认运行日志后重新创建任务。"
                job.status = "FAILED"
                job.error_message = f"{previous_error}\n{message}".strip()
                job.completed_at = recovered_at
            database.commit()

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                handled = await self.process_once()
            except Exception:
                handled = False
            wait_seconds = 0.1 if handled else settings.automation_worker_poll_seconds
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass

    async def process_once(self) -> bool:
        with SessionLocal() as database:
            job = database.scalar(
                select(AutomationJob)
                .where(AutomationJob.status == "QUEUED")
                .order_by(AutomationJob.id)
                .limit(1)
            )
            if job is None and time.monotonic() >= self._next_gpu_poll_at:
                job = database.scalar(
                    select(AutomationJob)
                    .where(AutomationJob.status == "WAITING_GPU")
                    .order_by(AutomationJob.id)
                    .limit(1)
                )
            if job is None:
                return False
            job.status = "RUNNING"
            job.started_at = job.started_at or datetime.utcnow()
            database.commit()
            try:
                await self._dispatch(database, job)
            except Exception as exc:
                job.status = "FAILED"
                job.error_message = str(exc)
                job.completed_at = datetime.utcnow()
                database.commit()
            return True

    async def _dispatch(self, database: Any, job: AutomationJob) -> None:
        job_type = job.job_type.upper()
        if job_type == "SCENE_EVALUATION":
            await self._evaluate_scene(database, job)
        elif job_type == "VLM_REVIEW":
            await self._review_execution(database, job)
        elif job_type == "YOLO_TRAINING":
            await self._run_yolo_training(database, job)
        elif job_type == "RESNET_TRAINING":
            raise RuntimeError("ResNet 训练已下线，请创建 YOLO 目标检测、分割或分类模型。")
        elif job_type == "PROMPT_OPTIMIZATION":
            await self._optimize_prompt(database, job)
        else:
            raise RuntimeError(f"不支持的自动化任务类型：{job.job_type}")

    async def _evaluate_scene(self, database: Any, job: AutomationJob) -> None:
        version = database.scalar(
            select(InspectionScenarioVersion)
            .options(
                selectinload(InspectionScenarioVersion.scenario),
                selectinload(InspectionScenarioVersion.nodes),
                selectinload(InspectionScenarioVersion.edges),
            )
            .where(InspectionScenarioVersion.id == job.scenario_version_id)
        )
        dataset = database.scalar(
            select(Dataset)
            .options(selectinload(Dataset.items))
            .where(Dataset.id == job.dataset_id)
        )
        if version is None or dataset is None:
            raise RuntimeError("场景版本或数据集不存在。")
        item_ids = set(job.input_snapshot_json.get("dataset_item_ids", []))
        items = [
            item
            for item in dataset.items
            if not item.is_deleted and (not item_ids or item.id in item_ids)
        ]
        rows: list[tuple[str | None, str | None]] = []
        detail: list[dict[str, Any]] = []
        for item in items:
            execution = await scenario_runtime.execute(
                database,
                version,
                image_path=item.media_path,
                source="EVALUATION",
                dataset_item_id=item.id,
                context={"dataset_code": dataset.code, "ground_truth": item.ground_truth},
            )
            rows.append((item.ground_truth, execution.result))
            detail.append(
                {
                    "dataset_item_id": item.id,
                    "ground_truth": item.ground_truth,
                    "result": execution.result,
                    "execution_id": execution.id,
                    "elapsed_ms": execution.elapsed_ms,
                }
            )
        job.result_json = {"metrics": _metrics(rows), "items": detail}
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        database.commit()

    async def _review_execution(self, database: Any, job: AutomationJob) -> None:
        execution_id = int((job.config_json or {}).get("execution_id", 0))
        execution = database.scalar(
            select(ScenarioExecution)
            .options(
                selectinload(ScenarioExecution.review),
                selectinload(ScenarioExecution.scenario_version).selectinload(
                    InspectionScenarioVersion.scenario
                ),
            )
            .where(ScenarioExecution.id == execution_id)
        )
        if execution is None:
            raise RuntimeError("待复核的场景执行记录不存在。")
        review = await scenario_runtime.review(database, execution)
        job.result_json = {
            "execution_id": execution.id,
            "review_status": review.status,
            "verdict": review.verdict,
            "reason": review.reason,
        }
        job.status = "COMPLETED" if review.status in {"COMPLETED", "SKIPPED"} else "FAILED"
        job.error_message = review.reason if review.status == "FAILED" else None
        job.completed_at = datetime.utcnow()
        database.commit()

    async def _optimize_prompt(self, database: Any, job: AutomationJob) -> None:
        version = database.scalar(
            select(InspectionScenarioVersion)
            .options(selectinload(InspectionScenarioVersion.scenario))
            .where(InspectionScenarioVersion.id == job.scenario_version_id)
        )
        dataset = database.scalar(
            select(Dataset)
            .options(selectinload(Dataset.items))
            .where(Dataset.id == job.dataset_id)
        )
        if version is None or dataset is None:
            raise RuntimeError("场景版本或数据集不存在。")
        if version.scenario.mode != "VLM_DIRECT":
            raise RuntimeError("当前提示词优化仅支持直接 VLM 检测场景。")
        config = job.config_json or {}
        detection_id = config.get("detection_vlm_model_id") or version.primary_vlm_model_id
        detection_model = (
            database.get(VlmModelConfig, int(detection_id)) if detection_id else None
        )
        optimizer_id = config.get("optimizer_vlm_model_id") or detection_id
        optimizer = database.get(VlmModelConfig, int(optimizer_id)) if optimizer_id else None
        if (
            detection_model is None
            or optimizer is None
            or not detection_model.enabled
            or not optimizer.enabled
        ):
            raise RuntimeError("检测 VLM 或优化提示词 VLM 不存在、未启用。")
        items = [item for item in dataset.items if not item.is_deleted and item.ground_truth]
        if not items:
            raise RuntimeError("提示词优化需要至少一条已标注 OK/NG 的测试样本。")
        max_rounds = int(config.get("max_rounds", 10))
        target_accuracy = float(config.get("target_accuracy", 0.95))
        if not 0.0 < target_accuracy <= 1.0:
            raise RuntimeError("目标准确率必须大于 0% 且不超过 100%。请修改任务后重新创建。")
        candidate_prompt = str(config.get("prompt_template") or version.prompt_template or "").strip()
        optimization_requirements = str(
            config.get("optimization_requirements")
            or "保持当前检测目标和 JSON 输出约定，优先降低漏判风险。"
        ).strip()
        if not candidate_prompt:
            raise RuntimeError("待优化检测提示词为空。")
        if not optimization_requirements:
            raise RuntimeError("请填写优化要求。")
        best = await self._evaluate_prompt(detection_model, candidate_prompt, items)
        baseline_metric_target_reached = _optimization_target_reached(
            best.get("metrics", {}),
            target_accuracy,
        )
        history = [
            {
                "round": 0,
                "prompt": candidate_prompt,
                "requirements_satisfied": False,
                "optimizer_requirements_claim": False,
                "metric_target_reached": baseline_metric_target_reached,
                "target_reached": False,
                "requirement_reason": "基线提示词尚未经过优化模型的要求核对。",
                **best,
            }
        ]
        best_prompt = candidate_prompt
        best_accuracy = best.get("accuracy") or 0.0
        best_requirements_satisfied = False
        best_requirement_reason = "基线提示词尚未经过优化模型的要求核对。"
        stop_reason = "已达到最大优化轮数。"

        for round_number in range(1, max_rounds + 1):
            database.refresh(job)
            if job.status == "CANCELED":
                return
            optimization_prompt = (
                "请基于当前检测提示词、测试指标和用户优化要求，生成下一轮供检测模型直接使用的完整提示词。\n"
                "用户优化要求：\n"
                f"{optimization_requirements}\n\n"
                "必须保留或明确要求检测模型只输出 JSON，且 JSON 至少包含 result、confidence、reason；"
                "看不清时应返回 UNCERTAIN。不要虚构图片中未提供的信息。\n"
                f"当前提示词：{best_prompt}\n"
                f"当前指标：{best.get('metrics', {})}\n"
                "重点降低把 NG 判为 OK 的风险。"
            )
            proposal = await vlm_client.judge(
                optimizer,
                prompt=optimization_prompt,
                system_prompt=(
                    "你是工业视觉检测提示词优化专家，不执行图片检测。"
                    "仅输出 JSON 对象，格式为："
                    "{\"prompt\":\"优化后的完整检测提示词\","
                    "\"requirements_satisfied\":true,"
                    "\"requirement_reason\":\"说明该提示词如何满足用户优化要求\"}。"
                    "prompt 必须是可直接交给检测 VLM 使用的中文提示词。"
                ),
            )
            proposed_prompt = str(
                proposal.get("prompt") or proposal.get("optimized_prompt") or ""
            ).strip()
            same_as_current_best = proposed_prompt == best_prompt
            requirements_satisfied = bool(proposal.get("requirements_satisfied", False))
            requirement_reason = str(
                proposal.get("requirement_reason") or proposal.get("reason") or ""
            ).strip()
            if not proposed_prompt:
                history.append(
                    {
                        "round": round_number,
                        "status": "SKIPPED",
                        "reason": "优化 VLM 未生成可用的新提示词。",
                    }
                )
                stop_reason = "优化模型未生成可用的新提示词。"
                break
            metrics = await self._evaluate_prompt(detection_model, proposed_prompt, items)
            metric_target_reached = _optimization_target_reached(
                metrics.get("metrics", {}),
                target_accuracy,
            )
            history.append(
                {
                    "round": round_number,
                    "prompt": proposed_prompt,
                    "requirements_satisfied": requirements_satisfied,
                    "optimizer_requirements_claim": requirements_satisfied,
                    "metric_target_reached": metric_target_reached,
                    "target_reached": metric_target_reached and requirements_satisfied,
                    "requirement_reason": requirement_reason,
                    **metrics,
                }
            )
            accuracy = metrics.get("accuracy") or 0.0
            if (
                accuracy > best_accuracy
                or (
                    accuracy == best_accuracy
                    and requirements_satisfied
                    and not best_requirements_satisfied
                )
            ):
                best_prompt = proposed_prompt
                best_accuracy = accuracy
                best = metrics
                best_requirements_satisfied = requirements_satisfied
                best_requirement_reason = requirement_reason
            if metric_target_reached and requirements_satisfied:
                if proposed_prompt != best_prompt:
                    best_prompt = proposed_prompt
                    best_accuracy = accuracy
                    best = metrics
                    best_requirements_satisfied = True
                    best_requirement_reason = requirement_reason
                stop_reason = "优化模型已核对用户要求，且测试数据集的实测准确率达到目标。"
                break
            if same_as_current_best:
                stop_reason = "优化模型未生成不同的候选提示词。"
                break

        best_metric_target_reached = _optimization_target_reached(
            best.get("metrics", {}),
            target_accuracy,
        )
        target_reached = best_metric_target_reached and best_requirements_satisfied
        job.result_json = {
            "baseline_prompt": candidate_prompt,
            "best_prompt": best_prompt,
            "best_metrics": best,
            "optimization_requirements": optimization_requirements,
            "target_accuracy": target_accuracy,
            "requirements_satisfied": best_requirements_satisfied,
            "optimizer_requirements_claim": best_requirements_satisfied,
            "requirement_reason": best_requirement_reason,
            "metric_target_reached": best_metric_target_reached,
            "target_reached": target_reached,
            "stop_reason": stop_reason,
            "history": history,
            "requires_manual_draft": True,
            "detection_vlm_model_id": detection_model.id,
            "optimizer_vlm_model_id": optimizer.id,
            "independent_optimizer": optimizer.id != detection_model.id,
        }
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        database.commit()

    async def _evaluate_prompt(
        self,
        model: VlmModelConfig,
        prompt: str,
        items: list[DatasetItem],
    ) -> dict[str, Any]:
        rows: list[tuple[str | None, str | None]] = []
        details: list[dict[str, Any]] = []
        for item in items:
            output = await vlm_client.judge(
                model,
                prompt=prompt,
                image_path=item.media_path,
            )
            result = str(output.get("result", "UNCERTAIN")).upper()
            if result not in {"OK", "NG"}:
                result = "NG"
            rows.append((item.ground_truth, result))
            details.append(
                {
                    "dataset_item_id": item.id,
                    "ground_truth": item.ground_truth,
                    "result": result,
                    "confidence": output.get("confidence"),
                }
            )
        metrics = _metrics(rows)
        return {"metrics": metrics, "accuracy": metrics.get("accuracy"), "items": details}

    async def _run_yolo_training(self, database: Any, job: AutomationJob) -> None:
        required = int((job.config_json or {}).get("gpu_memory_required_mb", 4096))
        free_memory = await asyncio.to_thread(_gpu_free_memory_mb)
        if not free_memory or max(free_memory) < required + settings.training_gpu_reserve_mb:
            job.status = "WAITING_GPU"
            self._next_gpu_poll_at = time.monotonic() + settings.training_gpu_poll_seconds
            job.result_json = {
                "required_gpu_memory_mb": required,
                "reserve_gpu_memory_mb": settings.training_gpu_reserve_mb,
                "available_gpu_memory_mb": free_memory,
            }
            database.commit()
            return

        model = database.get(VisionModel, job.vision_model_id)
        dataset = database.get(Dataset, job.dataset_id)
        if model is None or model.is_deleted or not model.enabled:
            raise RuntimeError("训练模型不存在或已停用。")
        if dataset is None or dataset.is_deleted:
            raise RuntimeError("训练数据集不存在。")
        snapshot = job.input_snapshot_json or {}
        samples = snapshot.get("training_samples")
        if not isinstance(samples, list) or not samples:
            raise RuntimeError("训练任务缺少冻结的数据集样本清单，请重新创建训练任务。")
        config = dict(job.config_json or {})
        gpu_index = max(range(len(free_memory)), key=lambda index: free_memory[index])
        try:
            spec = build_training_spec(
                job_id=job.id,
                vision_model_id=model.id,
                model_code=model.code,
                task_type=model.task_type,
                base_model=model.base_model,
                labels=model.labels_json or [],
                dataset_id=dataset.id,
                dataset_code=dataset.code,
                version=str(config.get("version") or "1.0"),
                samples=samples,
                config=config,
                device=gpu_index,
            )
        except YoloTrainingError as exc:
            raise RuntimeError(str(exc)) from exc

        job.log_path = str(spec.log_path)
        job.result_json = {
            "available_gpu_memory_mb": free_memory,
            "selected_gpu_index": gpu_index,
            "dataset_split": snapshot.get("dataset_split"),
            "training_validation": snapshot.get("training_validation"),
            "message": "GPU 资源满足要求，正在导出数据并启动本地 YOLO 训练。",
        }
        database.commit()
        try:
            training_result = await asyncio.to_thread(run_yolo_training, spec)
        except YoloTrainingError as exc:
            raise RuntimeError(str(exc)) from exc

        database.refresh(job)
        if job.status == "CANCELED":
            job.completed_at = datetime.utcnow()
            database.commit()
            return
        existing_version = database.scalar(
            select(VisionModelVersion).where(
                VisionModelVersion.vision_model_id == model.id,
                VisionModelVersion.version == str(config.get("version") or "1.0"),
                VisionModelVersion.is_deleted.is_(False),
            )
        )
        if existing_version is not None:
            raise RuntimeError("同名模型版本已存在，训练产物未自动登记。")
        version = VisionModelVersion(
            vision_model_id=model.id,
            dataset_id=dataset.id,
            version=str(config.get("version") or "1.0"),
            status="DRAFT",
            weights_path=str(training_result["weights_path"]),
            metrics_json=dict(training_result.get("metrics_json") or {}),
            artifact_paths_json=dict(training_result.get("artifact_paths_json") or {}),
        )
        database.add(version)
        database.flush()
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        job.log_path = str(training_result.get("log_path") or spec.log_path)
        job.result_json = {
            "available_gpu_memory_mb": free_memory,
            "selected_gpu_index": gpu_index,
            "dataset_split": snapshot.get("dataset_split"),
            "training_validation": snapshot.get("training_validation"),
            "model_version": {
                "id": version.id,
                "version": version.version,
                "status": version.status,
                "weights_path": version.weights_path,
                "metrics_json": version.metrics_json,
                "artifact_paths_json": version.artifact_paths_json,
            },
            "message": "YOLO 训练完成，已生成草稿模型版本；请检查指标后手动发布。",
        }
        database.commit()


automation_worker = AutomationWorker()
