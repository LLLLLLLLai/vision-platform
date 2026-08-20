from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import TextIO


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(f"[{timestamp()}] {message.rstrip()}\n")
        output.flush()


def relay(stream: TextIO, path: Path) -> None:
    with path.open("a", encoding="utf-8") as output:
        for raw_line in iter(stream.readline, ""):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            output.write(f"[{timestamp()}] {line}\n")
            output.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a child process and prefix stdout/stderr lines with time."
    )
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise SystemExit("A child command is required.")

    append_log(args.stdout_log, f"START command={' '.join(command)}")
    append_log(args.stderr_log, "START diagnostic stream")
    child = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def forward_signal(signum, _frame) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    for signal_name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            signal.signal(signal_value, forward_signal)

    stdout_thread = threading.Thread(
        target=relay,
        args=(child.stdout, args.stdout_log),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=relay,
        args=(child.stderr, args.stderr_log),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = child.wait()
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    append_log(args.stdout_log, f"STOP exit_code={return_code}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
