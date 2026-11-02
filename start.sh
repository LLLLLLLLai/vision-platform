#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pid_file="$project_root/logs/platform.pid"
log_file="$project_root/logs/platform.log"

if [[ -x "$project_root/.venv/bin/python" ]]; then
  python_bin="$project_root/.venv/bin/python"
  environment_name=".venv"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  python_bin="$CONDA_PREFIX/bin/python"
  environment_name="Conda: $CONDA_PREFIX"
elif [[ -n "${VISION_CONDA_PREFIX:-}" && -x "$VISION_CONDA_PREFIX/bin/python" ]]; then
  python_bin="$(cd "$VISION_CONDA_PREFIX" && pwd)/bin/python"
  environment_name="Conda: $VISION_CONDA_PREFIX"
else
  echo "No usable Python environment. Create .venv, activate Conda, or set VISION_CONDA_PREFIX." >&2
  exit 1
fi

mkdir -p "$project_root/logs"
if [[ -f "$pid_file" ]]; then
  existing_pid="$(cat "$pid_file")"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
    if [[ -r "/proc/$existing_pid/cmdline" ]] &&
       [[ "$(tr '\0' ' ' < "/proc/$existing_pid/cmdline")" == *"$project_root/scripts/run.py"* ]]; then
      echo "Vision Platform is already running (PID $existing_pid)."
      exit 0
    fi
    echo "PID file points to another process; check $pid_file before starting." >&2
    exit 1
  fi
  rm -f "$pid_file"
fi

cd "$project_root"
echo "Using $environment_name ($python_bin)"
port="$("$python_bin" -c 'from app.core.config import settings; print(settings.app_port)')"
health_ready() {
  "$python_bin" -c 'import http.client, json, sys; connection = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=2); connection.request("GET", "/api/v1/health"); response = connection.getresponse(); sys.exit(0 if response.status == 200 and json.loads(response.read()).get("status") == "READY" else 1)' "$port" >/dev/null 2>&1
}
if health_ready; then
  echo "A service is already ready on port $port without this project's PID file; refusing to start a duplicate." >&2
  exit 1
fi
"$python_bin" "$project_root/scripts/init_db.py"
export PYTHONUNBUFFERED=1
nohup "$python_bin" "$project_root/scripts/run.py" >> "$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"

for ((attempt = 0; attempt < 30; attempt++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "Vision Platform exited during startup. Recent logs:" >&2
    tail -n 20 "$log_file" >&2
    exit 1
  fi
  if health_ready && kill -0 "$pid" 2>/dev/null; then
    echo "Vision Platform started (PID $pid, port $port). Log: $log_file"
    exit 0
  fi
  sleep 1
done

echo "Process $pid is running but health check is not ready. Check $log_file or run ./stop.sh." >&2
exit 1
