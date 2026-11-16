select_platform_python() {
  if [[ -x "$project_root/.venv/bin/python" ]]; then
    python_bin="$project_root/.venv/bin/python"
    environment_name=".venv"
  elif [[ -x "$project_root/venv/bin/python" ]]; then
    python_bin="$project_root/venv/bin/python"
    environment_name="venv"
  elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
    python_bin="$CONDA_PREFIX/bin/python"
    environment_name="Current Conda: $CONDA_PREFIX"
  elif [[ -n "${VISION_CONDA_PREFIX:-}" && -x "$VISION_CONDA_PREFIX/bin/python" ]]; then
    python_bin="$VISION_CONDA_PREFIX/bin/python"
    environment_name="Configured Conda: $VISION_CONDA_PREFIX"
  else
    echo "No usable Python: create .venv/venv, activate Conda, or set VISION_CONDA_PREFIX." >&2
    return 1
  fi
}
