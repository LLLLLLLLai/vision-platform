"""Persistent worker for reviews and dataset-driven scene evaluation.

The worker intentionally claims one job at a time. SQLite keeps the local
deployment simple; MySQL 8 uses ``FOR UPDATE SKIP LOCKED`` when multiple
worker processes are enabled, so they do not claim the same job.
"""

import asyncio
import re
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
from app.services.openai_compatible_vlm import VlmRequestError, vlm_client
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


_PROMPT_VARIABLE = re.compile(r"\{\{\s*(input\.[A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_TRANSIENT_VLM_MARKERS = (
    "timeout",
    "timed out",
    "read timeout",
    "connect timeout",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "http 408",
    "http 409",
    "http 425",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def _prompt_variables(template: str) -> set[str]:
    return {match.group(1) for match in _PROMPT_VARIABLE.finditer(template or "")}


def _render_prompt_template(template: str, values: dict[str, Any]) -> str:
    """Render only runtime values while retaining the reusable source template."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).split(".", 1)[1]
        value = values.get(key)
        return "" if value is None else str(value)

    return _PROMPT_VARIABLE.sub(replace, template)


def _is_transient_vlm_error(error: Exception) -> bool:
    message = str(error).lower()
    return isinstance(error, VlmRequestError) and any(
        marker in message for marker in _TRANSIENT_VLM_MARKERS
    )


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
            cancellation_requested = list(
                database.scalars(
                    select(AutomationJob).where(
                        AutomationJob.status == "CANCEL_REQUESTED"
                    )
                )
            )
            jobs = list(
                database.scalars(
                    select(AutomationJob).where(AutomationJob.status == "RUNNING")
                )
            )
            if not jobs and not cancellation_requested:
                return
            recovered_at = datetime.utcnow()
            for job in cancellation_requested:
                result = dict(job.result_json or {})
                result.update(
                    {
                        "canceled": True,
                        "canceled_at": recovered_at.isoformat(),
                        "message": "服务重启时已完成停止请求。",
                    }
                )
                job.result_json = result
                job.status = "CANCELED"
                job.completed_at = recovered_at
            for job in jobs:
                previous_error = (job.error_message or "").strip()
                message = "服务重启导致任务中断，请确认运行日志后重新创建任务。"
                job.status = "FAILED"
                job.error_message = f"{previous_error}\n{message}".strip()
                job.completed_at = recovered_at
            database.commit()

    @staticmethod
    def _cancellation_requested(database: Any, job: AutomationJob) -> bool:
        """Refresh the row so a web-request stop reaches an active worker."""

        database.refresh(job)
        return job.status in {"CANCELED", "CANCEL_REQUESTED"}

    @staticmethod
    def _finish_canceled(
        database: Any,
        job: AutomationJob,
        *,
        partial_result: dict[str, Any] | None = None,
    ) -> None:
        """Persist a useful partial result instead of turning stop into failure."""

        database.refresh(job)
        result = dict(job.result_json or {})
        if partial_result:
            result.update(partial_result)
        result.update(
            {
                "canceled": True,
                "canceled_at": datetime.utcnow().isoformat(),
                "message": "任务已按请求停止；已保留当前已完成的样本和轮次结果。",
            }
        )
        job.result_json = result
        job.status = "CANCELED"
        job.completed_at = datetime.utcnow()
        database.commit()

    async def _judge_with_retry(
        self,
        model: VlmModelConfig,
        *,
        purpose: str,
        retries: int = 1,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Retry only temporary OpenAI-compatible VLM failures once.

        Retrying invalid prompt/model configuration would hide useful errors,
        while a one-time retry handles transient worker, network and gateway
        failures without immediately discarding a long-running optimization.
        """

        attempts: list[dict[str, Any]] = []
        for attempt in range(1, retries + 2):
            try:
                output = await vlm_client.judge(model, **kwargs)
                return output, {
                    "attempt_count": attempt,
                    "retry_count": attempt - 1,
                    "attempt_errors": attempts,
                }
            except Exception as exc:
                transient = _is_transient_vlm_error(exc)
                attempts.append(
                    {
                        "attempt": attempt,
                        "transient": transient,
                        "error": str(exc),
                    }
                )
                if not transient or attempt > retries:
                    retry_text = f"已重试 {attempt - 1} 次" if attempt > 1 else "未重试"
                    raise RuntimeError(f"{purpose}调用失败（{retry_text}）：{exc}") from exc
                await asyncio.sleep(min(1.0 * attempt, 3.0))

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
            def next_job(status: str) -> AutomationJob | None:
                statement = (
                    select(AutomationJob)
                    .where(AutomationJob.status == status)
                    .order_by(AutomationJob.id)
                    .limit(1)
                )
                if database.get_bind().dialect.name == "mysql":
                    statement = statement.with_for_update(skip_locked=True)
                return database.scalar(statement)

            job = next_job("QUEUED")
            if job is None and time.monotonic() >= self._next_gpu_poll_at:
                job = next_job("WAITING_GPU")
            if job is None:
                return False
            job.status = "RUNNING"
            job.started_at = job.started_at or datetime.utcnow()
            database.commit()
            try:
                await self._dispatch(database, job)
            except Exception as exc:
                database.refresh(job)
                if job.status in {"CANCELED", "CANCEL_REQUESTED"}:
                    self._finish_canceled(database, job)
                else:
                    result = dict(job.result_json or {})
                    result["failure"] = {
                        "message": str(exc),
                        "failed_at": datetime.utcnow().isoformat(),
                        "retry_policy": "VLM 临时超时、网关和连接异常自动重试 1 次；非临时错误不重试。",
                    }
                    job.result_json = result
                    job.status = "FAILED"
                    job.error_message = str(exc)
                    job.completed_at = datetime.utcnow()
                    database.commit()
            else:
                database.refresh(job)
                if job.status == "CANCEL_REQUESTED":
                    self._finish_canceled(database, job)
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
            if self._cancellation_requested(database, job):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={"metrics": _metrics(rows), "items": detail},
                )
                return
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
            if self._cancellation_requested(database, job):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={"metrics": _metrics(rows), "items": detail},
                )
                return
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
        input_values = config.get("input_values")
        if not isinstance(input_values, dict):
            input_values = {}
        if not candidate_prompt:
            raise RuntimeError("待优化检测提示词为空。")
        if not optimization_requirements:
            raise RuntimeError("请填写优化要求。")
        preserved_variables = _prompt_variables(candidate_prompt)
        best = await self._evaluate_prompt(
            database,
            job,
            detection_model,
            candidate_prompt,
            items,
            input_values=input_values,
        )
        if best.get("canceled"):
            self._finish_canceled(database, job, partial_result={"history": []})
            return
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
            if self._cancellation_requested(database, job):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={
                        "baseline_prompt": candidate_prompt,
                        "best_prompt": best_prompt,
                        "best_metrics": best,
                        "history": history,
                        "optimization_requirements": optimization_requirements,
                    },
                )
                return
            placeholder_text = ", ".join(f"{{{{ {item} }}}}" for item in sorted(preserved_variables)) or "无"
            optimization_prompt = (
                "请基于当前检测提示词、测试指标和用户优化要求，生成下一轮供检测模型直接使用的完整提示词。\n"
                "用户优化要求：\n"
                f"{optimization_requirements}\n\n"
                "必须保留或明确要求检测模型只输出 JSON，且 JSON 至少包含 result、confidence、reason；"
                "看不清时应返回 UNCERTAIN。不要虚构图片中未提供的信息。\n"
                "以下变量占位符属于生产接口契约，必须原样保留、不能改名或删除：\n"
                f"{placeholder_text}\n"
                "本次评测会自动以如下示例值渲染变量；这些值不是提示词正文的一部分：\n"
                f"{input_values}\n"
                f"当前提示词：{best_prompt}\n"
                f"当前指标：{best.get('metrics', {})}\n"
                "重点降低把 NG 判为 OK 的风险。"
            )
            proposal, optimizer_attempts = await self._judge_with_retry(
                optimizer,
                purpose="优化提示词 VLM",
                prompt=optimization_prompt,
                system_prompt=(
                    "你是工业视觉检测提示词优化专家，不执行图片检测。"
                    "仅输出 JSON 对象，格式为："
                    "{\"prompt\":\"优化后的完整检测提示词\","
                    "\"requirements_satisfied\":true,"
                    "\"requirement_reason\":\"说明该提示词如何满足用户优化要求\"}。"
                    "prompt 必须是可直接交给检测 VLM 使用的中文提示词。"
                    "即使开启了思考模式，也必须在最终输出中返回上述 JSON，不要把提示词放在思考过程里。"
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
            missing_variables = preserved_variables - _prompt_variables(proposed_prompt)
            if missing_variables:
                history.append(
                    {
                        "round": round_number,
                        "status": "SKIPPED",
                        "prompt": proposed_prompt,
                        "optimizer_attempts": optimizer_attempts,
                        "reason": "候选提示词缺少必须保留的变量："
                        + "、".join(f"{{{{ {item} }}}}" for item in sorted(missing_variables)),
                    }
                )
                stop_reason = "优化模型修改了生产接口变量，已拒绝该候选提示词。"
                break
            metrics = await self._evaluate_prompt(
                database,
                job,
                detection_model,
                proposed_prompt,
                items,
                input_values=input_values,
            )
            if metrics.get("canceled"):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={
                        "baseline_prompt": candidate_prompt,
                        "best_prompt": best_prompt,
                        "best_metrics": best,
                        "history": history,
                        "optimization_requirements": optimization_requirements,
                    },
                )
                return
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
                    "optimizer_attempts": optimizer_attempts,
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

            # Preserve completed rounds even if a later remote VLM call fails.
            job.result_json = {
                "baseline_prompt": candidate_prompt,
                "best_prompt": best_prompt,
                "best_metrics": best,
                "optimization_requirements": optimization_requirements,
                "target_accuracy": target_accuracy,
                "history": history,
                "input_values": input_values,
                "variable_placeholders": sorted(preserved_variables),
                "message": "任务运行中，已保存当前完成的优化轮次。",
            }
            database.commit()

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
            "input_values": input_values,
            "variable_placeholders": sorted(preserved_variables),
        }
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        database.commit()

    async def _evaluate_prompt(
        self,
        database: Any,
        job: AutomationJob,
        model: VlmModelConfig,
        prompt: str,
        items: list[DatasetItem],
        *,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows: list[tuple[str | None, str | None]] = []
        details: list[dict[str, Any]] = []
        values = input_values or {}
        total_retries = 0
        for item in items:
            if self._cancellation_requested(database, job):
                metrics = _metrics(rows)
                return {
                    "canceled": True,
                    "metrics": metrics,
                    "accuracy": metrics.get("accuracy"),
                    "items": details,
                    "retry_count": total_retries,
                }
            rendered_prompt = _render_prompt_template(prompt, values)
            output, attempts = await self._judge_with_retry(
                model,
                purpose="检测 VLM",
                prompt=rendered_prompt,
                image_path=item.media_path,
                context={"optimization_inputs": values},
            )
            total_retries += int(attempts.get("retry_count", 0))
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
                    "model_attempt_count": attempts.get("attempt_count"),
                    "model_retry_count": attempts.get("retry_count"),
                }
            )
            if self._cancellation_requested(database, job):
                metrics = _metrics(rows)
                return {
                    "canceled": True,
                    "metrics": metrics,
                    "accuracy": metrics.get("accuracy"),
                    "items": details,
                    "retry_count": total_retries,
                }
        metrics = _metrics(rows)
        return {
            "metrics": metrics,
            "accuracy": metrics.get("accuracy"),
            "items": details,
            "retry_count": total_retries,
        }

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
