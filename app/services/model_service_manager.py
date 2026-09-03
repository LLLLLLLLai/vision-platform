from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psutil

from app.core.config import PROJECT_ROOT, settings


@dataclass(frozen=True)
class ModelServiceDefinition:
    code: str
    name: str
    category: str
    url: str
    script: Path
    python_candidates: tuple[Path, ...]

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or "127.0.0.1"

    @property
    def port(self) -> int:
        parsed = urlparse(self.url)
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    @property
    def python_executable(self) -> Path:
        for candidate in self.python_candidates:
            if candidate.exists():
                return candidate
        return self.python_candidates[0]


def _python_path(environment: str) -> Path:
    if os.name == "nt":
        return PROJECT_ROOT / environment / "Scripts" / "python.exe"
    return PROJECT_ROOT / environment / "bin" / "python"


SERVICE_DEFINITIONS = {
    service.code: service
    for service in (
        ModelServiceDefinition(
            code="grounding_dino",
            name="Grounding DINO",
            category="开放词汇定位",
            url=settings.grounding_service_url,
            script=PROJECT_ROOT / "scripts" / "run_grounding.py",
            python_candidates=(_python_path(".venv-qwen"), _python_path(".venv")),
        ),
        ModelServiceDefinition(
            code="dinov2",
            name="DINOv2",
            category="参考图相似度",
            url=settings.dinov2_service_url,
            script=PROJECT_ROOT / "scripts" / "run_dinov2.py",
            python_candidates=(_python_path(".venv"),),
        ),
        ModelServiceDefinition(
            code="qwen3_vl",
            name="Qwen3-VL",
            category="VLM 结果复核",
            url=settings.qwen_vl_service_url,
            script=PROJECT_ROOT / "scripts" / "run_qwen_vl.py",
            python_candidates=(_python_path(".venv-qwen"), _python_path(".venv")),
        ),
        ModelServiceDefinition(
            code="paddleocr",
            name="PaddleOCR",
            category="专用 OCR",
            url=settings.paddleocr_service_url,
            script=PROJECT_ROOT / "scripts" / "run_ocr.py",
            python_candidates=(_python_path("ocr_service/.venv"), _python_path(".venv")),
        ),
        ModelServiceDefinition(
            code="sam2",
            name="SAM2",
            category="线束分割",
            url=settings.sam2_service_url,
            script=PROJECT_ROOT / "scripts" / "run_sam2.py",
            python_candidates=(_python_path(".venv"), _python_path(".venv-qwen")),
        ),
    )
}

STATE_DIR = PROJECT_ROOT / "data" / "model_services"
LOG_DIR = PROJECT_ROOT / "logs" / "model_services"
TIMESTAMPED_LOG_RUNNER = PROJECT_ROOT / "scripts" / "run_with_timestamped_logs.py"


def get_service_definition(code: str) -> ModelServiceDefinition:
    try:
        return SERVICE_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"未知模型服务：{code}") from exc


def _pid_file(service: ModelServiceDefinition) -> Path:
    return STATE_DIR / f"{service.code}.pid"


def _log_paths(service: ModelServiceDefinition) -> tuple[Path, Path]:
    return (
        LOG_DIR / f"{service.code}.out.log",
        LOG_DIR / f"{service.code}.error.log",
    )


def _call_log_path(service: ModelServiceDefinition) -> Path:
    return LOG_DIR / f"{service.code}.calls.log"


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_pid(service: ModelServiceDefinition) -> int | None:
    path = _pid_file(service)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _listener_pid(port: int) -> int | None:
    for connection in psutil.net_connections(kind="inet"):
        if not connection.laddr or connection.status != psutil.CONN_LISTEN:
            continue
        if connection.laddr.port == port and connection.pid:
            return connection.pid
    return None


def _process_matches_service(process: psutil.Process, service: ModelServiceDefinition) -> bool:
    try:
        command = " ".join(process.cmdline()).lower()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return False
    tokens = (
        service.script.name.lower(),
        service.script.stem.lower(),
        f"{service.code.replace('_', '')}_service",
        service.code.replace("_", ""),
    )
    normalized = command.replace("_", "").replace("-", "")
    return any(token.replace("_", "").replace("-", "") in normalized for token in tokens)


def _owned_process(service: ModelServiceDefinition) -> psutil.Process | None:
    managed_pid = _read_pid(service)
    if managed_pid and psutil.pid_exists(managed_pid):
        try:
            return psutil.Process(managed_pid)
        except psutil.NoSuchProcess:
            pass

    listener_pid = _listener_pid(service.port)
    if not listener_pid:
        return None

    try:
        process = psutil.Process(listener_pid)
    except psutil.NoSuchProcess:
        return None

    current = process
    while current:
        if _process_matches_service(current, service):
            return current
        try:
            current = current.parent()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            break
    return None


def _tail(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError as exc:
        return f"读取日志失败：{exc}"


async def _health(service: ModelServiceDefinition) -> tuple[bool, dict[str, Any] | str]:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{service.url.rstrip('/')}/health")
            response.raise_for_status()
            payload = response.json()
        status = str(payload.get("status", "READY")).upper()
        return status in {"READY", "OK", "HEALTHY"}, payload
    except Exception as exc:
        return False, str(exc)


async def service_status(service: ModelServiceDefinition) -> dict[str, Any]:
    healthy, health_detail = await _health(service)
    listener_pid = _listener_pid(service.port)
    managed_pid = _read_pid(service)
    managed_pid_alive = bool(managed_pid and psutil.pid_exists(managed_pid))
    if managed_pid and not managed_pid_alive and not listener_pid:
        try:
            _pid_file(service).unlink(missing_ok=True)
        except OSError:
            pass
        managed_pid = None
    stdout_path, stderr_path = _log_paths(service)
    status = "READY" if healthy else "STOPPED"

    if not healthy and listener_pid:
        status = "STARTING"
        try:
            if time.time() - psutil.Process(listener_pid).create_time() > 120:
                status = "ERROR"
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            status = "ERROR"
    elif not healthy and managed_pid:
        if managed_pid_alive:
            status = "STARTING"
            try:
                if time.time() - psutil.Process(managed_pid).create_time() > 120:
                    status = "ERROR"
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                status = "ERROR"

    return {
        "code": service.code,
        "name": service.name,
        "category": service.category,
        "url": service.url,
        "host": service.host,
        "port": service.port,
        "status": status,
        "healthy": healthy,
        "health_detail": health_detail,
        "pid": listener_pid or (managed_pid if managed_pid_alive else None),
        "managed": managed_pid_alive,
        "script_path": str(service.script),
        "python_path": str(service.python_executable),
        "script_exists": service.script.exists(),
        "python_exists": service.python_executable.exists(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "last_error": _tail(stderr_path, 12) if status == "ERROR" else "",
    }


async def list_service_statuses() -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(service_status(service) for service in SERVICE_DEFINITIONS.values())))


def start_service(service: ModelServiceDefinition) -> dict[str, Any]:
    if _listener_pid(service.port):
        return {"status": "RUNNING", "message": f"{service.name} 已在端口 {service.port} 运行"}
    if not service.script.exists():
        raise RuntimeError(f"启动脚本不存在：{service.script}")
    if not service.python_executable.exists():
        raise RuntimeError(f"Python 环境不存在：{service.python_executable}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = _log_paths(service)
    stale_pid = _pid_file(service)
    if stale_pid.exists():
        stale_pid.unlink()

    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )

    if os.name == "nt":
        startup_script = (
            "$env:PYTHONUNBUFFERED='1'; "
            "$process = Start-Process "
            f"-FilePath {_powershell_literal(str(service.python_executable))} "
            f"-ArgumentList @({_powershell_literal(str(service.script))}) "
            f"-WorkingDirectory {_powershell_literal(str(PROJECT_ROOT))} "
            "-WindowStyle Hidden "
            f"-RedirectStandardOutput {_powershell_literal(str(stdout_path))} "
            f"-RedirectStandardError {_powershell_literal(str(stderr_path))} "
            "-PassThru; $process.Id"
        )
        try:
            launcher = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    startup_script,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
        except subprocess.TimeoutExpired:
            process_id = None
        else:
            if launcher.returncode != 0:
                raise RuntimeError(
                    f"{service.name} 启动失败：{launcher.stderr.strip() or launcher.stdout.strip()}"
                )
            try:
                process_id = int(launcher.stdout.strip().splitlines()[-1])
            except (IndexError, ValueError):
                process_id = None
    else:
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        command = [
            str(service.python_executable),
            str(TIMESTAMPED_LOG_RUNNER),
            "--stdout-log",
            str(stdout_path),
            "--stderr-log",
            str(stderr_path),
            "--",
            str(service.python_executable),
            str(service.script),
        ]
        with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creation_flags,
            )
        process_id = process.pid

    if process_id is not None:
        _pid_file(service).write_text(str(process_id), encoding="utf-8")
    else:
        _pid_file(service).unlink(missing_ok=True)
    return {
        "status": "STARTING",
        "message": f"{service.name} 已提交启动，请等待模型加载完成",
        "pid": process_id,
    }


def stop_service(service: ModelServiceDefinition) -> dict[str, Any]:
    process = _owned_process(service)
    if process is None:
        if _listener_pid(service.port):
            raise RuntimeError("端口已被非平台模型进程占用，为避免误停已拒绝操作")
        _pid_file(service).unlink(missing_ok=True)
        return {"status": "STOPPED", "message": f"{service.name} 当前未运行"}

    processes = process.children(recursive=True)
    for child in reversed(processes):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        pass

    gone, alive = psutil.wait_procs([*processes, process], timeout=8)
    for remaining in alive:
        try:
            remaining.kill()
        except psutil.NoSuchProcess:
            pass

    _pid_file(service).unlink(missing_ok=True)
    return {"status": "STOPPED", "message": f"{service.name} 已停止", "pid": process.pid}


def service_logs(service: ModelServiceDefinition, lines: int = 200) -> dict[str, Any]:
    stdout_path, stderr_path = _log_paths(service)
    call_log_path = _call_log_path(service)
    return {
        "code": service.code,
        "name": service.name,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "call_log_path": str(call_log_path),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "calls": _tail(call_log_path, lines),
        "stdout": _tail(stdout_path, lines),
        "stderr": _tail(stderr_path, lines),
    }
