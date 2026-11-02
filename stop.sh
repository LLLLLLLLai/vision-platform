#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pid_file="$project_root/logs/platform.pid"

if [[ ! -f "$pid_file" ]]; then
  echo "Vision Platform is not running (no PID file)."
  exit 0
fi

pid="$(cat "$pid_file")"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
  echo "Invalid PID file: $pid_file" >&2
  exit 1
fi
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$pid_file"
  echo "Vision Platform is not running; removed stale PID file."
  exit 0
fi
if [[ ! -r "/proc/$pid/cmdline" ]] ||
   [[ "$(tr '\0' ' ' < "/proc/$pid/cmdline")" != *"$project_root/scripts/run.py"* ]]; then
  echo "PID $pid does not belong to this project; refusing to stop it." >&2
  exit 1
fi

kill -TERM "$pid"
for ((attempt = 0; attempt < 30; attempt++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "Vision Platform stopped (PID $pid)."
    exit 0
  fi
  sleep 1
done

echo "Vision Platform did not stop within 30 seconds (PID $pid); check logs/platform.log." >&2
exit 1
