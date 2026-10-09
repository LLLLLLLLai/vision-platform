"""Restart the local Vision Platform process after validating its listener."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def listener_process(port: int) -> psutil.Process | None:
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        if connection.laddr.port == port and connection.pid:
            return psutil.Process(connection.pid)
    return None


def is_platform_process(process: psutil.Process) -> bool:
    try:
        command_line = " ".join(process.cmdline()).lower().replace("\\", "/")
        working_directory = Path(process.cwd()).resolve()
    except (psutil.AccessDenied, psutil.ZombieProcess):
        return False
    project_marker = str(PROJECT_ROOT).lower()
    belongs_to_project = project_marker in command_line or working_directory == PROJECT_ROOT
    return belongs_to_project and ("scripts/run.py" in command_line or "uvicorn" in command_line)


def stop_platform(port: int) -> int | None:
    process = listener_process(port)
    if process is None:
        return None
    if not is_platform_process(process):
        raise RuntimeError(f"端口 {port} 当前不是 Vision Platform 进程，已拒绝停止。")
    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return process.pid


def start_platform() -> int:
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    wrapper = PROJECT_ROOT / "scripts" / "run_with_timestamped_logs.py"
    command = [
        sys.executable,
        str(wrapper),
        "--stdout-log",
        str(log_dir / "platform.stdout.log"),
        "--stderr-log",
        str(log_dir / "platform.stderr.log"),
        "--",
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run.py"),
    ]
    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        creationflags=creation_flags,
    )
    return process.pid


def wait_for_platform(port: int, timeout_seconds: int) -> int:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/api/v1/configuration/recipes"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status != 200:
                    time.sleep(0.5)
                    continue
                payload = json.loads(response.read().decode("utf-8"))
                return len(payload) if isinstance(payload, list) else 0
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"平台在 {timeout_seconds} 秒内未能在端口 {port} 就绪。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely restart local Vision Platform.")
    parser.add_argument("--port", type=int, default=9010)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    stopped_pid = stop_platform(args.port)
    started_pid = start_platform()
    recipe_count = wait_for_platform(args.port, args.timeout)
    print(f"RESTART=OK stopped_pid={stopped_pid or 'NONE'} starter_pid={started_pid}")
    print(f"RECIPES_HTTP=200 recipes_returned={recipe_count}")


if __name__ == "__main__":
    main()
