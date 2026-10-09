"""Persistent worker for reviews and dataset-driven scene evaluation.

The worker intentionally claims one job at a time. SQLite keeps the local
deployment simple; MySQL 8 uses ``FOR UPDATE SKIP LOCKED`` when multiple
worker processes are enabled, so they do not claim the same job.
"""

import asyncio
import json
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
from app.services.openai_compatible_vlm import (
    INSPECTION_VLM_SYSTEM_PROMPT,
    VlmRequestError,
    vlm_client,
)
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


_DETECTION_TERMINAL_RESULTS = {"OK", "NG", "UNCERTAIN", "ERROR"}
_MAX_CONSECUTIVE_UNUSABLE_OPTIMIZATION_CANDIDATES = 3


def _prompt_optimizer_system_prompt() -> str:
    """Return the non-negotiable response contract for a prompt optimizer.

    The detection prompt can itself require an ``OK``/``NG`` result. That
    requirement must never be confused with the optimizer's own response: the
    optimizer always returns a JSON object containing the new prompt.
    """

    return (
        "你是工业视觉检测提示词编辑器，不执行图片检测，也不得给出本次检测的 OK、NG "
        "或其他检测结论。用户的优化要求描述的是【生成后的检测提示词】应该如何约束检测模型，"
        "不是你自身的最终输出要求。\n"
        "不可变的生产输出契约：生成后的检测提示词必须要求检测模型返回一个 JSON 对象，"
        "且必须保留 result 字段。用户可以把 result 的业务取值收窄为 OK/NG，"
        "但绝不能要求检测模型改为纯文本、Markdown、XML，或删除 JSON/result 字段。\n"
        "无论用户要求中是否出现“result 只能返回 OK/NG”，你的最终响应都必须且只能是 JSON 对象："
        '{"optimized_detection_prompt":"可直接交给检测模型使用的完整提示词",'
        '"requirements_satisfied":true,"requirement_reason":"说明如何写入并满足用户要求"}。\n'
        "特别说明：若用户要求检测模型的 result 只能是 OK 或 NG，应把该限制写进 "
        "optimized_detection_prompt 字符串中；你自己绝不能仅输出 {\"result\":\"OK\"}、"
        "{\"result\":\"NG\"} 或任何检测结论。即使开启思考模式，最终 content 也必须返回上述 JSON。"
    )


def _build_prompt_optimization_request(
    *,
    optimization_requirements: str,
    current_prompt: str,
    metrics: dict[str, Any],
    preserved_variables: set[str],
    input_values: dict[str, Any],
    previous_candidate_feedback: list[str] | None = None,
) -> str:
    """Separate the optimizer contract from the generated prompt contract."""

    placeholder_text = ", ".join(
        f"{{{{ {item} }}}}" for item in sorted(preserved_variables)
    ) or "无"
    sample_inputs = json.dumps(input_values, ensure_ascii=False, sort_keys=True)
    feedback_items = [
        str(item).strip()
        for item in (previous_candidate_feedback or [])
        if str(item).strip()
    ][-3:]
    feedback_text = "\n".join(f"- {item}" for item in feedback_items) or "无（这是第一轮）。"
    return (
        "请生成下一轮供【检测模型】直接使用的完整提示词。\n\n"
        "【优化器自身返回格式】\n"
        "你必须遵循系统消息中的 optimized_detection_prompt JSON 格式；不要返回任何检测结果。\n\n"
        "【检测模型需要满足的用户优化要求】\n"
        "以下内容只需要写入 optimized_detection_prompt，用来约束后续检测模型。"
        "它不约束优化器自身的 JSON 输出，也不能改变平台的生产输出契约。\n"
        "<<<USER_OPTIMIZATION_REQUIREMENTS\n"
        f"{optimization_requirements}\n"
        "USER_OPTIMIZATION_REQUIREMENTS>>>\n\n"
        "【不可变的生产输出契约】\n"
        "检测模型必须只返回一个 JSON 对象并保留 result 字段；"
        "业务要求“结果只能 OK/NG”时，限制的是 result 字段的取值，不是改成只输出纯文本 OK/NG。"
        "不得删除生产接口变量、不得要求忽略图片，也不得改变 JSON 输出格式。\n\n"
        "【必须保留的生产接口变量】\n"
        "以下占位符属于生产接口契约，必须在 optimized_detection_prompt 中原样保留，不能改名或删除：\n"
        f"{placeholder_text}\n"
        "本次评测会以如下示例值渲染变量；示例值不是提示词正文的一部分：\n"
        f"{sample_inputs}\n\n"
        "【当前检测提示词】\n"
        "<<<CURRENT_DETECTION_PROMPT\n"
        f"{current_prompt}\n"
        "CURRENT_DETECTION_PROMPT\n\n"
        "【当前评测指标】\n"
        f"{json.dumps(metrics, ensure_ascii=False, sort_keys=True)}\n\n"
        "【上一轮候选反馈】\n"
        f"{feedback_text}\n"
        "若上一轮候选无效、未变化或未提升，请生成一个与当前提示词不同且更可执行的完整版本。\n\n"
        "重点降低把 NG 判为 OK 的风险。保留当前检测目标、生产接口变量和输出结构；"
        "任何用户优化要求都不能改变上述生产输出契约。"
    )


def _extract_optimized_detection_prompt(proposal: dict[str, Any]) -> str:
    """Read the new prompt while accepting earlier field names for compatibility."""

    for key in ("optimized_detection_prompt", "optimized_prompt", "prompt"):
        value = proposal.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _optimizer_proposal_error(
    proposal: dict[str, Any],
    *,
    preserved_variables: set[str],
) -> str | None:
    """Reject a detection verdict mistakenly returned as an optimizer response."""

    prompt = _extract_optimized_detection_prompt(proposal)
    if not prompt:
        result = str(proposal.get("result") or "").strip().upper()
        if result in _DETECTION_TERMINAL_RESULTS:
            return (
                "优化模型返回了检测结论，而不是 optimized_detection_prompt。"
                "用户的 OK/NG 要求必须写入生成的检测提示词。"
            )
        return "优化模型未返回 optimized_detection_prompt。"
    if prompt.upper() in _DETECTION_TERMINAL_RESULTS:
        return "优化模型把检测结论当成了优化后的提示词。"
    if len(prompt) < 16:
        return "优化后的提示词过短，无法作为检测模型的完整指令。"
    missing_variables = preserved_variables - _prompt_variables(prompt)
    if missing_variables:
        return "候选提示词缺少必须保留的变量：" + "、".join(
            f"{{{{ {item} }}}}" for item in sorted(missing_variables)
        )
    return None


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
                "progress": {
                    "percent": 100,
                    "stage": "CANCELED",
                    "label": "已停止",
                    "completed": True,
                },
            }
        )
        job.result_json = result
        job.status = "CANCELED"
        job.completed_at = datetime.utcnow()
        database.commit()

    @staticmethod
    def _set_job_progress(
        database: Any,
        job: AutomationJob,
        *,
        percent: float,
        stage: str,
        label: str,
        current: int | None = None,
        total: int | None = None,
        indeterminate: bool = False,
        completed: bool = False,
    ) -> None:
        """Persist compact, UI-friendly progress without changing job schema.

        The durable JSON field keeps this backwards compatible with existing
        SQLite/MySQL installations.  Progress is intentionally stage-based for
        local YOLO training, because Ultralytics runs in a worker thread and
        does not expose a reliable epoch callback in every supported release.
        """

        result = dict(job.result_json or {})
        payload: dict[str, Any] = {
            "percent": round(max(0.0, min(float(percent), 100.0)), 1),
            "stage": stage,
            "label": label,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if current is not None:
            payload["current"] = max(0, int(current))
        if total is not None:
            payload["total"] = max(0, int(total))
        if indeterminate:
            payload["indeterminate"] = True
        if completed:
            payload["completed"] = True
        result["progress"] = payload
        job.result_json = result
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

    async def _generate_optimized_prompt(
        self,
        optimizer: VlmModelConfig,
        *,
        optimization_prompt: str,
        preserved_variables: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        """Request a prompt proposal and repair one malformed optimizer reply.

        A common ambiguity is a user requirement such as ``result 只能返回
        OK/NG``. This method makes one explicit correction attempt when the
        optimizer returns that verdict itself instead of a new detection prompt.
        """

        proposal, first_attempts = await self._judge_with_retry(
            optimizer,
            purpose="优化提示词 VLM",
            prompt=optimization_prompt,
            system_prompt=_prompt_optimizer_system_prompt(),
        )
        first_error = _optimizer_proposal_error(
            proposal,
            preserved_variables=preserved_variables,
        )
        if first_error is None:
            return proposal, first_attempts, None

        visible_response = {
            key: value
            for key, value in proposal.items()
            if not key.startswith("_")
        }
        correction_prompt = (
            f"{optimization_prompt}\n\n"
            "【上一轮输出格式错误，必须修正】\n"
            f"错误原因：{first_error}\n"
            "上一轮响应摘要：\n"
            f"{json.dumps(visible_response, ensure_ascii=False)[:4000]}\n"
            "请不要执行检测或只返回 OK/NG。请重新输出完整 JSON，"
            "并将用户关于检测 result 状态的要求写进 optimized_detection_prompt 字符串。"
        )
        repaired, repair_attempts = await self._judge_with_retry(
            optimizer,
            purpose="优化提示词 VLM 格式修复",
            prompt=correction_prompt,
            system_prompt=_prompt_optimizer_system_prompt(),
        )
        final_error = _optimizer_proposal_error(
            repaired,
            preserved_variables=preserved_variables,
        )
        attempts = dict(repair_attempts)
        attempts.update(
            {
                "format_repair_attempted": True,
                "format_repair_reason": first_error,
                "initial_attempt": first_attempts,
                "total_attempt_count": int(first_attempts.get("attempt_count", 0))
                + int(repair_attempts.get("attempt_count", 0)),
                "total_retry_count": int(first_attempts.get("retry_count", 0))
                + int(repair_attempts.get("retry_count", 0)),
            }
        )
        return repaired, attempts, final_error

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
            self._set_job_progress(
                database,
                job,
                percent=1,
                stage="RUNNING",
                label="任务已开始执行",
            )
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
                    result["progress"] = {
                        "percent": 100,
                        "stage": "FAILED",
                        "label": "执行失败",
                        "completed": True,
                        "updated_at": datetime.utcnow().isoformat(),
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
        total_items = len(items)
        self._set_job_progress(
            database,
            job,
            percent=0,
            stage="EVALUATING",
            label="正在准备场景评测",
            current=0,
            total=total_items,
        )
        for item_index, item in enumerate(items, start=1):
            if self._cancellation_requested(database, job):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={
                        "metrics": _metrics(rows),
                        "items": detail,
                    },
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
            self._set_job_progress(
                database,
                job,
                percent=(item_index / total_items * 100) if total_items else 100,
                stage="EVALUATING",
                label=f"正在评测第 {item_index}/{total_items} 个样本",
                current=item_index,
                total=total_items,
            )
            if self._cancellation_requested(database, job):
                self._finish_canceled(
                    database,
                    job,
                    partial_result={"metrics": _metrics(rows), "items": detail},
                )
                return
        job.result_json = {
            "metrics": _metrics(rows),
            "items": detail,
            "progress": {
                "percent": 100,
                "stage": "COMPLETED",
                "label": "场景评测完成",
                "current": total_items,
                "total": total_items,
                "completed": True,
                "updated_at": datetime.utcnow().isoformat(),
            },
        }
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
            or "保持当前检测目标和输出字段约定，优先降低漏判风险。"
        ).strip()
        input_values = config.get("input_values")
        if not isinstance(input_values, dict):
            input_values = {}
        if not candidate_prompt:
            raise RuntimeError("待优化检测提示词为空。")
        if not optimization_requirements:
            raise RuntimeError("请填写优化要求。")
        preserved_variables = _prompt_variables(candidate_prompt)
        # Baseline evaluates every sample once; each following round adds one
        # prompt-generation step and another full dataset evaluation.
        planned_progress_units = max(1, len(items) + max_rounds * (len(items) + 1))
        self._set_job_progress(
            database,
            job,
            percent=0,
            stage="BASELINE_EVALUATION",
            label="正在评测基线提示词",
            current=0,
            total=planned_progress_units,
        )
        best = await self._evaluate_prompt(
            database,
            job,
            detection_model,
            candidate_prompt,
            items,
            input_values=input_values,
            progress_offset=0,
            progress_total=planned_progress_units,
            progress_label="正在评测基线提示词",
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
        previous_candidate_feedback: list[str] = []
        consecutive_unusable_candidates = 0

        def save_partial_history(message: str) -> None:
            """Keep completed rounds visible while the task continues."""

            job.result_json = {
                "baseline_prompt": candidate_prompt,
                "best_prompt": best_prompt,
                "best_metrics": best,
                "optimization_requirements": optimization_requirements,
                "target_accuracy": target_accuracy,
                "max_rounds": max_rounds,
                "history": history,
                "input_values": input_values,
                "variable_placeholders": sorted(preserved_variables),
                "candidate_feedback": previous_candidate_feedback[-3:],
                "message": message,
                "progress": dict((job.result_json or {}).get("progress") or {}),
            }
            database.commit()

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
            round_offset = len(items) + (round_number - 1) * (len(items) + 1)
            self._set_job_progress(
                database,
                job,
                percent=round_offset / planned_progress_units * 100,
                stage="GENERATING_PROMPT",
                label=f"正在生成第 {round_number}/{max_rounds} 轮候选提示词",
                current=round_offset,
                total=planned_progress_units,
            )
            optimization_prompt = _build_prompt_optimization_request(
                optimization_requirements=optimization_requirements,
                current_prompt=best_prompt,
                metrics=best.get("metrics", {}),
                preserved_variables=preserved_variables,
                input_values=input_values,
                previous_candidate_feedback=previous_candidate_feedback,
            )
            proposal, optimizer_attempts, proposal_error = await self._generate_optimized_prompt(
                optimizer,
                optimization_prompt=optimization_prompt,
                preserved_variables=preserved_variables,
            )
            proposed_prompt = _extract_optimized_detection_prompt(proposal)
            same_as_current_best = proposed_prompt == best_prompt
            requirements_satisfied = bool(proposal.get("requirements_satisfied", False))
            requirement_reason = str(
                proposal.get("requirement_reason") or proposal.get("reason") or ""
            ).strip()
            if proposal_error:
                history.append(
                    {
                        "round": round_number,
                        "status": "SKIPPED",
                        "optimizer_attempts": optimizer_attempts,
                        "reason": proposal_error,
                    }
                )
                previous_candidate_feedback.append(
                    f"第 {round_number} 轮候选不可用：{proposal_error}"
                )
                consecutive_unusable_candidates += 1
                if consecutive_unusable_candidates >= _MAX_CONSECUTIVE_UNUSABLE_OPTIMIZATION_CANDIDATES:
                    stop_reason = (
                        "优化模型连续 3 轮未生成可用的新提示词；"
                        "已完成格式修复和续跑后提前结束。"
                    )
                    save_partial_history("优化模型连续输出无效候选，任务已提前结束。")
                    break
                save_partial_history("本轮候选不可用，正在继续生成下一轮提示词。")
                continue
            missing_variables = preserved_variables - _prompt_variables(proposed_prompt)
            if missing_variables:
                missing_text = "、".join(
                    f"{{{{ {item} }}}}" for item in sorted(missing_variables)
                )
                history.append(
                    {
                        "round": round_number,
                        "status": "SKIPPED",
                        "prompt": proposed_prompt,
                        "optimizer_attempts": optimizer_attempts,
                        "reason": "候选提示词缺少必须保留的变量：" + missing_text,
                    }
                )
                previous_candidate_feedback.append(
                    f"第 {round_number} 轮候选丢失生产变量：{missing_text}"
                )
                consecutive_unusable_candidates += 1
                if consecutive_unusable_candidates >= _MAX_CONSECUTIVE_UNUSABLE_OPTIMIZATION_CANDIDATES:
                    stop_reason = "优化模型连续 3 轮修改生产接口变量，已提前结束。"
                    save_partial_history("候选提示词连续丢失生产变量，任务已提前结束。")
                    break
                save_partial_history("本轮候选丢失生产变量，正在继续生成下一轮提示词。")
                continue
            if same_as_current_best:
                history.append(
                    {
                        "round": round_number,
                        "status": "SKIPPED",
                        "prompt": proposed_prompt,
                        "optimizer_attempts": optimizer_attempts,
                        "reason": "候选提示词与当前最佳提示词完全相同，未执行重复评测。",
                    }
                )
                previous_candidate_feedback.append(
                    f"第 {round_number} 轮候选与当前最佳提示词相同；"
                    "下一轮必须给出不同且更可执行的提示词。"
                )
                consecutive_unusable_candidates += 1
                if consecutive_unusable_candidates >= _MAX_CONSECUTIVE_UNUSABLE_OPTIMIZATION_CANDIDATES:
                    stop_reason = "优化模型连续 3 轮重复当前最佳提示词，已提前结束。"
                    save_partial_history("候选提示词连续重复，任务已提前结束。")
                    break
                save_partial_history("本轮候选重复当前提示词，正在继续生成下一轮。")
                continue
            consecutive_unusable_candidates = 0
            metrics = await self._evaluate_prompt(
                database,
                job,
                detection_model,
                proposed_prompt,
                items,
                input_values=input_values,
                progress_offset=round_offset + 1,
                progress_total=planned_progress_units,
                progress_label=f"正在评测第 {round_number}/{max_rounds} 轮候选提示词",
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
            save_partial_history("任务运行中，已保存当前完成的优化轮次。")

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
            "max_rounds": max_rounds,
            "completed_rounds": len(history) - 1,
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
            "progress": {
                "percent": 100,
                "stage": "COMPLETED",
                "label": "场景优化完成",
                "current": planned_progress_units,
                "total": planned_progress_units,
                "completed": True,
                "updated_at": datetime.utcnow().isoformat(),
            },
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
        progress_offset: int | None = None,
        progress_total: int | None = None,
        progress_label: str | None = None,
    ) -> dict[str, Any]:
        rows: list[tuple[str | None, str | None]] = []
        details: list[dict[str, Any]] = []
        values = input_values or {}
        total_retries = 0
        for item_index, item in enumerate(items, start=1):
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
                system_prompt=INSPECTION_VLM_SYSTEM_PROMPT,
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
            if progress_offset is not None and progress_total:
                current = progress_offset + item_index
                self._set_job_progress(
                    database,
                    job,
                    percent=current / progress_total * 100,
                    stage="EVALUATING_PROMPT",
                    label=progress_label or "正在评测候选提示词",
                    current=current,
                    total=progress_total,
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
                "progress": {
                    "percent": 5,
                    "stage": "WAITING_GPU",
                    "label": "等待可用 GPU 显存",
                    "updated_at": datetime.utcnow().isoformat(),
                },
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
            "progress": {
                "percent": 15,
                "stage": "PREPARING_TRAINING",
                "label": "正在导出数据并准备训练",
                "updated_at": datetime.utcnow().isoformat(),
            },
        }
        database.commit()
        self._set_job_progress(
            database,
            job,
            percent=20,
            stage="TRAINING",
            label="YOLO 训练执行中，等待训练框架完成当前轮次",
            indeterminate=True,
        )
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
            "progress": {
                "percent": 100,
                "stage": "COMPLETED",
                "label": "模型训练完成",
                "completed": True,
                "updated_at": datetime.utcnow().isoformat(),
            },
        }
        database.commit()


automation_worker = AutomationWorker()
